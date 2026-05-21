"""Training data pipeline for the hook quality classifier.

Pulls historical hook + engagement pairs from SharePoint for training.
Cross-references PendingFeedback (completed tasks with reward_48h) with
Blueprints (hook_text) to build labelled training examples.

The 75th percentile of reward_48h is used as the label threshold:
examples above it are "high quality" (label=1), below are label=0.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MIN_EXAMPLES = 50  # Lowered from 200 — even a noisy signal is better than neutral 0.5


@dataclass
class HookExample:
    """One training example: a hook paired with its engagement outcome."""

    hook_text: str
    platform: str
    niche_id: str
    reward_48h: float
    published_at: str = ""
    skip_rate_proxy: float | None = None  # IG Reels: 1 - (avg_watch_time / video_duration)


def pull_training_data(
    backlog_client: Any,
    niche_id: str | None = None,
    min_age_hours: float = 72,
) -> list[HookExample]:
    """Pull completed feedback tasks cross-referenced with blueprints for hook text.

    Steps:
        1. Query GenLab_PendingFeedback where Status='complete'
        2. For each task, look up the corresponding blueprint for hook_text
        3. Return list of HookExample with reward_48h as the engagement signal

    Args:
        backlog_client: An initialized BacklogClient instance.
        niche_id: If set, only return examples for this niche.
        min_age_hours: Minimum hours since publish (ignored — filtering
            is done by status='complete' which implies 48h+ has passed).

    Returns:
        List of HookExample instances. Empty list on any error.
    """
    from genlab_core.learning.pending_feedback_store import PendingFeedbackStore

    PendingFeedbackStore(backlog_client)

    # Step 1: Get completed feedback tasks via the analytics proxy.
    # PendingFeedbackStore stores items in a SharePoint list accessible
    # via backlog_client's low-level proxy pattern.
    try:
        # The PendingFeedback list is not a first-class BacklogClient table,
        # so we query it via the graph proxy pattern used by the store.
        # BacklogClient exposes named proxy attributes; PendingFeedback
        # is accessed via get_items on the store's client reference.
        # Since get_items may not exist on BacklogClient, fall back to
        # using the store's internal parsing.
        items = _query_completed_feedback(backlog_client)
    except Exception as exc:
        logger.warning("[hook_training] Failed to query PendingFeedback: %s", exc)
        return []

    examples: list[HookExample] = []
    _blueprint_cache: dict[str, dict | None] = {}
    _analytics_cache: dict[str, float | None] = {}  # content_id → skip_rate

    for item in items:
        fields = item.get("fields", item)

        # Filter by niche_id if specified
        item_niche = fields.get("NicheId", fields.get("niche_id", ""))
        if niche_id and item_niche != niche_id:
            continue

        # Must have reward_48h
        reward_raw = fields.get("Reward48h", fields.get("reward_48h"))
        if reward_raw is None:
            continue
        try:
            reward = float(reward_raw)
        except (TypeError, ValueError):
            continue

        # PF rows store hook_text directly in their `extra` JSONB at create
        # time (publish_all_platforms writes it via PendingFeedbackTask).
        # _query_completed_feedback spreads `extra` into `fields`, so we
        # can read hook_text from the same dict.
        #
        # Older versions of this loop tried to bridge PF → Blueprint via
        # find_blueprint_by_candidate_id(post_id) — but PF's `post_id` is
        # the platform_post_id (e.g. an Instagram media ID) while
        # blueprints are keyed by candidate_id. The bridge never matched,
        # every blueprint lookup returned None, every example was dropped
        # at the empty-hook check, and the hook classifier accumulated
        # zero training examples for the entire post-Sprint-65 window
        # (2026-03-17 onward). 2026-05-20 audit found the discrepancy.
        hook_text = (fields.get("hook_text") or "").strip()

        # Fallback: try the blueprint hop via task_id (the real
        # candidate_id) for legacy rows whose extra was empty at create.
        if not hook_text:
            task_id = fields.get("task_id", "") or fields.get("content_id", "")
            if task_id:
                if task_id not in _blueprint_cache:
                    try:
                        _blueprint_cache[task_id] = (
                            backlog_client.find_blueprint_by_candidate_id(task_id)
                        )
                    except Exception:
                        _blueprint_cache[task_id] = None
                hook_text = _extract_hook_text(_blueprint_cache[task_id])

        if not hook_text:
            continue

        platform = fields.get("Platform", fields.get("platform", ""))
        published_at = fields.get("PublishedAt", fields.get("published_at", ""))

        # Compute skip_rate_proxy for IG Reels from Analytics data.
        # Keyed by platform_post_id (the Instagram media ID), which is
        # what Analytics rows use as their post_id.
        skip_rate = None
        post_id_for_skip = fields.get("post_id") or fields.get("PostID", "")
        if platform == "instagram" and post_id_for_skip:
            skip_rate = _lookup_skip_rate(
                backlog_client, post_id_for_skip, _analytics_cache,
            )

        examples.append(
            HookExample(
                hook_text=hook_text,
                platform=platform,
                niche_id=item_niche,
                reward_48h=reward,
                published_at=published_at,
                skip_rate_proxy=skip_rate,
            )
        )

    logger.info(
        "[hook_training] Pulled %d training examples (niche=%s)",
        len(examples),
        niche_id or "all",
    )
    return examples


def compute_engagement_labels(
    examples: list[HookExample],
    skip_rate_threshold: float = 0.30,
) -> list[int]:
    """Compute binary labels using engagement + skip rate signals.

    Label = 1 (high quality) when EITHER:
      - reward_48h >= 75th percentile, OR
      - skip_rate_proxy is available AND < skip_rate_threshold (0.30)

    This dual-signal approach means a hook that retains viewers (low skip
    rate) is labelled positively even if engagement is moderate, because
    retention is the strongest algo signal for Reels distribution.

    For tiny datasets (< 5 examples), falls back to labelling all as 0
    since there isn't enough data for a meaningful threshold.

    Args:
        examples: List of HookExample with reward_48h values.
        skip_rate_threshold: Skip rate below this is labelled positive.

    Returns:
        List of int labels (0 or 1), same length as examples.
    """
    if not examples:
        return []

    rewards = np.array([ex.reward_48h for ex in examples])

    if len(rewards) < 5:
        return [0] * len(examples)

    reward_threshold = float(np.percentile(rewards, 75))

    labels = []
    for ex, r in zip(examples, rewards, strict=False):
        # Primary signal: engagement above 75th percentile
        if r >= reward_threshold:
            labels.append(1)
        # Secondary signal: low skip rate (strong retention)
        elif (
            ex.skip_rate_proxy is not None
            and ex.skip_rate_proxy < skip_rate_threshold
        ):
            labels.append(1)
        else:
            labels.append(0)

    return labels


# ── Internal helpers ─────────────────────────────────────────────────


def _lookup_skip_rate(
    backlog_client: Any,
    content_id: str,
    cache: dict[str, float | None],
) -> float | None:
    """Look up skip_rate_proxy from Analytics for an IG Reels post.

    skip_rate_proxy = 1 - (ig_reels_avg_watch_time / video_duration)
    Returns None if metrics are unavailable.
    """
    if content_id in cache:
        return cache[content_id]

    try:
        records = backlog_client.analytics.all(
            formula=f"AND({{candidate_id}}='{content_id}',{{platform}}='instagram')",
            max_records=1,
        )
        if not records:
            cache[content_id] = None
            return None

        rec = records[0].get("fields", records[0])
        avg_watch = rec.get("ig_reels_avg_watch_time")
        duration = rec.get("video_duration")

        if avg_watch is not None and duration and float(duration) > 0:
            skip_rate = 1.0 - (float(avg_watch) / float(duration))
            skip_rate = max(0.0, min(1.0, skip_rate))
            cache[content_id] = skip_rate
            return skip_rate

        cache[content_id] = None
        return None

    except Exception as exc:
        logger.debug("[hook_training] skip_rate lookup failed for %s: %s", content_id, exc)
        cache[content_id] = None
        return None


def _query_completed_feedback(backlog_client: Any) -> list[dict]:
    """Query the pending_feedback table for completed items.

    Uses psycopg directly against the canonical PG schema. The legacy
    SharePoint-style get_items('Status eq complete') paths fail because
    the live column is `collection_status`, not `Status`.
    """
    import os

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        logger.warning("[hook_training] DATABASE_URL unset — returning empty")
        return []
    try:
        import psycopg
    except ImportError:
        logger.warning("[hook_training] psycopg not installed — returning empty")
        return []
    try:
        with psycopg.connect(db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT niche_id, post_id, task_id, platform, reward_48h,
                           publish_time, extra
                    FROM pending_feedback
                    WHERE collection_status = 'complete'
                      AND reward_48h IS NOT NULL
                    """
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.warning("[hook_training] DB query failed: %s", exc)
        return []

    items: list[dict] = []
    for niche_id, post_id, task_id, platform, reward_48h, publish_time, extra in rows:
        items.append({
            "fields": {
                "niche_id": niche_id,
                "PostID": post_id,
                "post_id": post_id,
                # task_id is the candidate_id used by blueprints; keep it
                # distinct from post_id so the hook_text fallback can
                # bridge PF -> Blueprint correctly for legacy rows.
                "task_id": task_id,
                "content_id": task_id or post_id,
                "Platform": platform,
                "platform": platform,
                "Reward48h": reward_48h,
                "reward_48h": reward_48h,
                "PublishedAt": publish_time.isoformat() if publish_time else "",
                "published_at": publish_time.isoformat() if publish_time else "",
                **(extra or {}),
            }
        })
    return items


def _extract_hook_text(blueprint: dict | None) -> str:
    """Extract hook text from a blueprint record, trying multiple field names."""
    if not blueprint:
        return ""
    fields = blueprint.get("fields", blueprint)
    # Try field names in priority order
    for key in ("hook_text", "hook", "caption_hook"):
        val = fields.get(key, "")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return ""
