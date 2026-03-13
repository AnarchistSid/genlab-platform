"""Prefect flow for weekly config update: bandit learning → YAML write-back.

Reads completed PendingFeedbackTask records, derives optimal posting hours
per platform from reward-weighted analysis, and calls config_writer to
apply bounded schedule updates.

Run standalone:
    python -m genlab_core.learning.config_update_flow

Or as a Prefect deployment:
    prefect deployment build genlab_core/learning/config_update_flow.py:weekly_config_update
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from prefect import flow, task
except ImportError:  # pragma: no cover — Prefect is optional
    def flow(fn=None, **kwargs):  # type: ignore[misc]
        if fn is not None:
            return fn
        return lambda f: f

    def task(fn=None, **kwargs):  # type: ignore[misc]
        if fn is not None:
            return fn
        return lambda f: f

from genlab_core.learning.config_writer import (
    update_schedule_from_bandit,
    update_templates_from_bandit,
)
from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
from genlab_core.learning.pending_feedback_task import PendingFeedbackTask


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 30
MIN_PLATFORM_OBS = 50  # Minimum tasks per platform before trusting schedule data


# ---------------------------------------------------------------------------
# Optimal hour computation
# ---------------------------------------------------------------------------

@task(name="compute_optimal_hours")
def compute_optimal_hours(
    completed_tasks: list[PendingFeedbackTask],
) -> tuple[dict[str, int], dict[str, int]]:
    """From completed tasks with rewards, find the best posting hour per platform.

    Groups tasks by platform + published hour, computes the average reward
    per hour, and picks the hour with the highest average.

    Returns:
        A tuple of (optimal_hours, observation_counts) where:
        - optimal_hours: ``{platform: best_hour}``
        - observation_counts: ``{platform: total_observations}``
    """
    hour_rewards: dict[str, dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    platform_counts: dict[str, int] = defaultdict(int)

    for t in completed_tasks:
        if t.reward_48h is None:
            continue
        pub_hour = t.published_at.hour  # Local hour from stored timestamp
        hour_rewards[t.platform][pub_hour].append(t.reward_48h)
        platform_counts[t.platform] += 1

    optimal: dict[str, int] = {}
    for platform, hours in hour_rewards.items():
        if not hours:
            continue
        best_hour = max(
            hours.keys(),
            key=lambda h: sum(hours[h]) / len(hours[h]),
        )
        optimal[platform] = best_hour
        logger.info(
            "[config_update] %s optimal_hour=%02d:00 (avg_reward=%.4f, n=%d)",
            platform,
            best_hour,
            sum(hours[best_hour]) / len(hours[best_hour]),
            len(hours[best_hour]),
        )

    return optimal, dict(platform_counts)


@task(name="compute_arm_posteriors")
def compute_arm_posteriors(
    completed_tasks: list[PendingFeedbackTask],
) -> tuple[dict[str, tuple[float, float]], dict[str, int]]:
    """Compute Beta posteriors per content type from completed tasks.

    For each content type, the reward is treated as a Bernoulli signal:
    reward >= 0.5 is a 'success', below is a 'failure'. This gives
    (alpha, beta) parameters for a Beta posterior used in Thompson Sampling.

    Returns:
        A tuple of (arm_posteriors, observation_counts) where:
        - arm_posteriors: ``{content_type: (alpha, beta)}``
        - observation_counts: ``{content_type: n_observations}``
    """
    type_rewards: dict[str, list[float]] = defaultdict(list)

    for t in completed_tasks:
        if t.reward_48h is None:
            continue
        ct = t.content_type or "unknown"
        type_rewards[ct].append(t.reward_48h)

    posteriors: dict[str, tuple[float, float]] = {}
    counts: dict[str, int] = {}
    for ct, rewards in type_rewards.items():
        successes = sum(1.0 for r in rewards if r >= 0.5)
        failures = len(rewards) - successes
        # Beta posterior with uniform prior (alpha=1, beta=1)
        posteriors[ct] = (1.0 + successes, 1.0 + failures)
        counts[ct] = len(rewards)
        logger.debug(
            "[config_update] content_type=%s alpha=%.1f beta=%.1f n=%d",
            ct, posteriors[ct][0], posteriors[ct][1], counts[ct],
        )

    return posteriors, counts


# ---------------------------------------------------------------------------
# Fetch completed tasks
# ---------------------------------------------------------------------------

@task(name="fetch_completed_tasks")
def fetch_completed_tasks(
    backlog_client: Any,
    niche_id: Optional[str] = None,
    lookback_days: int = LOOKBACK_DAYS,
) -> list[PendingFeedbackTask]:
    """Fetch completed PendingFeedbackTasks from the last N days.

    Uses PendingFeedbackStore to query SharePoint, then filters for tasks
    that have 48h rewards and were published within the lookback window.
    """
    store = PendingFeedbackStore(backlog_client)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)

    try:
        # Query all items (including complete) to get historical data
        items = backlog_client.get_items(
            store.LIST_NAME,
            filter_query="Status eq 'complete'",
        )
    except Exception as exc:
        logger.warning("[config_update] Failed to fetch completed tasks: %s", exc)
        return []

    tasks: list[PendingFeedbackTask] = []
    for item in items:
        try:
            task_obj = PendingFeedbackStore._from_sharepoint_item(item)
            if niche_id and task_obj.niche_id != niche_id:
                continue
            if task_obj.published_at.tzinfo is None:
                pub = task_obj.published_at.replace(tzinfo=timezone.utc)
            else:
                pub = task_obj.published_at
            if pub < cutoff:
                continue
            if task_obj.reward_48h is not None:
                tasks.append(task_obj)
        except Exception as exc:
            logger.debug("[config_update] parse error: %s", exc)

    logger.info(
        "[config_update] fetched %d completed tasks (lookback=%d days, niche=%s)",
        len(tasks), lookback_days, niche_id or "all",
    )
    return tasks


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

@flow(name="weekly_config_update")
def weekly_config_update(
    config_dir: Path | str | None = None,
    niche_id: Optional[str] = None,
    backlog_client: Any = None,
    dry_run: bool = False,
) -> dict[str, bool]:
    """Weekly Prefect flow: apply bandit-learned config changes.

    Args:
        config_dir: Directory containing publishing.yaml and templates.yaml.
            If None, attempts to discover from AGENT_ROOT.
        niche_id: Optional filter for a specific niche.
        backlog_client: BacklogClient instance. If None, creates one from env.
        dry_run: If True, compute changes but do not write files.

    Returns:
        Dict with ``{"schedule_updated": bool, "templates_updated": bool}``.
    """
    result = {"schedule_updated": False, "templates_updated": False}

    # Resolve config_dir
    if config_dir is not None:
        config_dir = Path(config_dir)
    else:
        try:
            from genlab_core.utils.env import get_agent_root
            config_dir = get_agent_root() / "config"
        except Exception:
            logger.error("[config_update] Cannot resolve config_dir — pass explicitly")
            return result

    # Resolve backlog client
    if backlog_client is None:
        try:
            from genlab_core.http.backlog_client import BacklogClient
            backlog_client = BacklogClient()
        except Exception as exc:
            logger.error("[config_update] Failed to create BacklogClient: %s", exc)
            return result

    # Fetch completed tasks
    completed = fetch_completed_tasks(backlog_client, niche_id=niche_id)
    if not completed:
        logger.info("[config_update] No completed tasks — nothing to update")
        return result

    # Schedule update
    publishing_path = config_dir / "publishing.yaml"
    if publishing_path.exists():
        optimal_hours, obs_counts = compute_optimal_hours(completed)
        if optimal_hours and not dry_run:
            result["schedule_updated"] = update_schedule_from_bandit(
                publishing_path,
                niche_id or "all",
                optimal_hours,
                obs_counts,
            )
        elif optimal_hours:
            logger.info(
                "[config_update] DRY RUN — would update schedule: %s", optimal_hours,
            )
    else:
        logger.info("[config_update] %s not found — skip schedule update", publishing_path)

    # Template ratio update
    templates_path = config_dir / "templates.yaml"
    if templates_path.exists():
        posteriors, type_counts = compute_arm_posteriors(completed)
        if posteriors and not dry_run:
            result["templates_updated"] = update_templates_from_bandit(
                templates_path,
                niche_id or "all",
                posteriors,
                type_counts,
            )
        elif posteriors:
            logger.info(
                "[config_update] DRY RUN — would update template ratios: %s",
                posteriors,
            )
    else:
        logger.info("[config_update] %s not found — skip template update", templates_path)

    logger.info("[config_update] result: %s", result)
    return result


if __name__ == "__main__":
    weekly_config_update()
