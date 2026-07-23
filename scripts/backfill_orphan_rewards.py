#!/usr/bin/env python3
"""Tier 1: backfill reward_48h for orphan pending_feedback rows.

2026-07-23 finding: 237 pending_feedback rows have
``collection_status = 'complete'`` AND ``reward_48h IS NULL``. These
went through all 4 collection windows but reward was never computed
(pre-migration era, transient fetch failures, etc). They contribute
zero learning signal despite the posts existing.

This script:
  1. Queries orphan rows (status=complete, reward_48h IS NULL)
  2. For each: fetches CURRENT metrics via fetch_platform_metrics
  3. Computes reward via RewardShaper (same code path as normal
     48h collection)
  4. Updates pending_feedback.reward_48h
  5. Optionally feeds to bandit via _default_bandit_updater

Phases (--phase):
  fetch    — retrieve metrics only, dry-run reward compute (default)
  update   — persist reward_48h to pending_feedback (data-only, safe)
  bandit   — ALSO push into bandit_arms via _default_bandit_updater
             (IRREVERSIBLE — requires --commit-bandit)

Safety:
  * Twitter rows skipped (rule #23)
  * --limit N caps rows per invocation (default 20)
  * Failed metric fetches log warning + skip (no partial reward)
  * Bandit push requires BOTH --commit + --commit-bandit (double-guard)
  * The current-metric snapshot vs historical-48h-metric asymmetry is
    real but bounded: reward is percentile-relative or channel-relative,
    so a post with slightly-more-late-tail views doesn't produce a
    wildly-inflated reward. Bandit sees the signal as first-time,
    not double-count (reward_48h was NULL, so it was never fed before).

Class-of-fix: same "fill first-time signal for orphaned rows" shape
as the 2026-05-21 backfill_bandit_from_pending_feedback.py which
covered the 2026-03-17 → 2026-05-19 outage window.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "genlab-core" / "src"))

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_orphan_rewards")


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL not set in env")
    return dsn


def _fetch_orphans(conn: psycopg.Connection, limit: int) -> list[dict]:
    """Query pending_feedback rows with status='complete' but no reward."""
    rows = conn.execute(
        """
        SELECT id::text AS id, niche_id, platform, post_id, task_id,
               arm_id, extra->'arm_ids_by_dimension' AS arm_ids_by_dim,
               extra->'bandit_context' AS bandit_context,
               publish_time
        FROM pending_feedback
        WHERE collection_status = 'complete'
          AND reward_48h IS NULL
          AND platform != 'twitter'
          AND post_id IS NOT NULL
          AND post_id != ''
        ORDER BY publish_time DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return list(rows)


def _compute_reward_for_orphan(
    row: dict,
) -> tuple[float | None, dict | None]:
    """Fetch metrics + compute reward. Returns (reward, metrics) or
    (None, None) on any failure. Same code path as normal 48h."""
    try:
        from genlab_core.learning.metric_collector import (
            fetch_platform_metrics,
            get_channel_metrics as _channel_fn,
        )
        from genlab_core.learning.percentile_targets import get_percentile_target
        from genlab_core.learning.reward_shaper import RewardShaper
    except Exception as exc:
        logger.error("import failed: %s", exc)
        return None, None

    platform = row["platform"]
    niche_id = row["niche_id"] or ""
    post_id = row["post_id"] or ""

    try:
        # Fetch at 168h window semantics — best proxy for "final state"
        # of an older orphan row. fetch_platform_metrics handles the
        # post_id prefix normalization internally.
        metrics = fetch_platform_metrics(platform, post_id, "168h", niche_id=niche_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fetch failed for %s/%s (%s): %s",
            platform, post_id[:20], niche_id, exc,
        )
        return None, None

    if not metrics:
        logger.info("no metrics returned for %s/%s", platform, post_id[:20])
        return None, None

    try:
        shaper = RewardShaper(
            channel_metrics_fn=_channel_fn,
            percentile_targets_fn=get_percentile_target,
            niche_id=niche_id,
        )
        reward = shaper.compute_reward(platform=platform, metrics=metrics)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "shaper failed for %s/%s: %s",
            platform, post_id[:20], exc,
        )
        return None, None

    if reward is None:
        logger.info(
            "shaper returned None for %s/%s (no reward math possible)",
            platform, post_id[:20],
        )
        return None, None

    return float(reward), metrics


def _feed_bandit(
    row: dict, reward: float
) -> bool:
    """Push reward into bandit_arms. Uses same wire as normal 48h
    collection so posteriors update identically to a live fire."""
    try:
        from genlab_core.learning.metric_collector import _default_bandit_updater
    except Exception as exc:
        logger.error("bandit import failed: %s", exc)
        return False

    niche_id = row["niche_id"] or ""
    arm_id = row["arm_id"] or ""
    platform = row["platform"]

    # bandit_context can be a JSON string (older rows) or dict (newer)
    bc_raw = row.get("bandit_context")
    if isinstance(bc_raw, str):
        try:
            import json
            bandit_context = json.loads(bc_raw)
        except Exception:
            bandit_context = None
    else:
        bandit_context = bc_raw

    if not arm_id:
        logger.info(
            "SKIP bandit push (no arm_id) for %s/%s",
            platform, row["post_id"][:20],
        )
        return False

    try:
        _default_bandit_updater(
            niche_id=niche_id,
            content_type=arm_id,
            platform=platform,
            reward=reward,
            bandit_context=bandit_context,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "bandit updater failed for %s/%s: %s",
            platform, row["post_id"][:20], exc,
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=["fetch", "update", "bandit"], default="fetch"
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--commit-bandit", action="store_true")
    args = parser.parse_args()

    logger.info(
        "phase=%s limit=%d commit=%s commit-bandit=%s",
        args.phase, args.limit, args.commit, args.commit_bandit,
    )

    if args.phase == "bandit" and not args.commit_bandit:
        logger.error(
            "phase=bandit requires --commit-bandit (double-guard against "
            "irreversible bandit_arms writes)"
        )
        return 2

    with psycopg.connect(_get_dsn(), row_factory=dict_row) as conn:
        orphans = _fetch_orphans(conn, args.limit)
        logger.info("found %d orphan pending_feedback rows", len(orphans))

        fetched = 0
        updated = 0
        bandit_pushed = 0
        skipped = 0
        errors = 0

        for row in orphans:
            reward, metrics = _compute_reward_for_orphan(row)

            if reward is None:
                skipped += 1
                continue

            fetched += 1
            logger.info(
                "COMPUTED %s/%s/%s reward=%.3f (arm=%s)",
                row["niche_id"], row["platform"], row["id"][:8],
                reward, (row["arm_id"] or "?")[:30],
            )

            if args.phase in ("update", "bandit"):
                if args.commit:
                    try:
                        conn.execute(
                            "UPDATE pending_feedback SET reward_48h = %s, "
                            "updated_at = NOW() WHERE id = %s::uuid",
                            (reward, row["id"]),
                        )
                        conn.commit()
                        updated += 1
                        logger.info(
                            "UPDATED  %s/%s reward_48h=%.3f",
                            row["platform"], row["id"][:8], reward,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "UPDATE failed for %s: %s", row["id"][:8], exc
                        )
                        errors += 1
                        continue
                else:
                    logger.info(
                        "DRY-UPD  %s/%s would set reward_48h=%.3f",
                        row["platform"], row["id"][:8], reward,
                    )

            if args.phase == "bandit" and args.commit and args.commit_bandit:
                if _feed_bandit(row, reward):
                    bandit_pushed += 1
                    logger.info(
                        "PUSHED   %s/%s → bandit arm=%s reward=%.3f",
                        row["platform"], row["id"][:8],
                        (row["arm_id"] or "?")[:30], reward,
                    )
                else:
                    errors += 1

        logger.info(
            "DONE phase=%s: fetched=%d updated=%d bandit_pushed=%d skipped=%d errors=%d",
            args.phase, fetched, updated, bandit_pushed, skipped, errors,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
