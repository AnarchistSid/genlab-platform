#!/usr/bin/env python3
"""Backfill views/likes/comments/shares for ALL historical publishes.

2026-07-23: since 2026-03-17 the platform has 2,016 SUCCESS/INSIGHTS_*
rows in publishing_analytics. Many still show views=0 because the
metric collector's per-post fetch failed, was skipped, or was never
scheduled at collection window time.

This script:
  1. Queries all publishing_analytics rows with status IN ('SUCCESS',
     'INSIGHTS_6H', 'INSIGHTS_24H', 'INSIGHTS_48H', 'INSIGHTS_168H')
  2. Groups by (platform, niche_id) for batched API access
  3. Calls the existing platform metric fetchers
     (learning/metrics/{youtube,facebook,instagram,threads}.py) — same
     code path the metric_collector uses, so numbers match
  4. Updates publishing_analytics.{views, likes, comments, shares,
     metrics_fetched} for each row

Phases:
  --phase pa       ← update publishing_analytics only (safe, reversible)
  --phase orphan   ← also compute reward_48h for pending_feedback rows
                     with reward_48h IS NULL (adds data, no bandit write)
  --phase bandit   ← ALSO feed 237 orphans to bandit_arms (irreversible)

Default: --dry-run --phase pa. --commit required to persist. Explicit
--commit-bandit required for phase=bandit (double-guard).

Safety:
  - --limit N caps rows per invocation (default 20)
  - Twitter is out of scope (rule #23) — skipped
  - Deleted / removed posts (API returns 404 / null) log warning + skip
  - Backup DB advised BEFORE --commit (script prints reminder)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap PROJECT_ROOT so genlab_core imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "genlab-core" / "src"))

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_historical_metrics")


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL not set in env")
    return dsn


def _fetch_metrics_via_platform_module(
    platform: str, post_id: str, niche_id: str
) -> dict | None:
    """Call the existing per-platform metric fetcher.

    Reuses the code path that metric_collector uses so backfilled numbers
    are directly comparable to organic collection. Returns None on failure
    (deleted post, API error, missing credentials).

    Twitter is out of scope per rule #23 — return None.
    """
    if platform == "twitter":
        return None
    try:
        if platform == "youtube":
            from genlab_core.learning.metrics.youtube import _fetch_youtube as fetch_youtube
            # Strip "youtube:" prefix if present
            video_id = post_id.split(":", 1)[-1] if post_id.startswith("youtube:") else post_id
            return fetch_youtube(video_id, niche_id=niche_id)
        elif platform == "facebook":
            from genlab_core.learning.metrics.facebook import _fetch_facebook as fetch_facebook
            fb_id = post_id.split(":", 1)[-1] if post_id.startswith("facebook:") else post_id
            return fetch_facebook(fb_id, niche_id=niche_id)
        elif platform == "instagram":
            from genlab_core.learning.metrics.instagram import _fetch_instagram as fetch_instagram
            ig_id = post_id.split(":", 1)[-1] if post_id.startswith("instagram:") else post_id
            return fetch_instagram(ig_id, niche_id=niche_id)
        elif platform == "threads":
            from genlab_core.learning.metrics.threads import _fetch_threads as fetch_threads
            th_id = post_id.split(":", 1)[-1] if post_id.startswith("threads:") else post_id
            return fetch_threads(th_id, niche_id=niche_id)
        else:
            logger.warning("unknown platform %s", platform)
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch failed for %s/%s (%s): %s", platform, post_id[:20], niche_id, exc)
        return None


def _fetch_candidates(conn: psycopg.Connection, limit: int) -> list[dict]:
    """Query publishing_analytics rows that need backfilling."""
    rows = conn.execute(
        """
        SELECT id::text AS id, niche_id, platform, post_id, status,
               views, likes, comments, shares, saves, metrics_fetched
        FROM publishing_analytics
        WHERE status IN ('SUCCESS', 'INSIGHTS_6H', 'INSIGHTS_24H',
                         'INSIGHTS_48H', 'INSIGHTS_168H')
          AND post_id IS NOT NULL
          AND post_id != ''
          AND platform != 'twitter'
          AND (metrics_fetched IS NULL
               OR metrics_fetched < NOW() - INTERVAL '48 hours'
               OR views = 0)
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return list(rows)


def _phase_pa(
    conn: psycopg.Connection, candidates: list[dict], commit: bool
) -> tuple[int, int, int]:
    """Phase 1: update publishing_analytics with fresh metrics.

    Returns (fetched, updated, skipped).
    """
    fetched = 0
    updated = 0
    skipped = 0

    for row in candidates:
        pid = row["post_id"]
        platform = row["platform"]
        niche = row["niche_id"]

        metrics = _fetch_metrics_via_platform_module(platform, pid, niche)
        if metrics is None:
            skipped += 1
            logger.info(
                "SKIP  %s/%s/%s post_id=%s (fetch returned None)",
                niche, platform, row["id"][:8], pid[:20],
            )
            continue

        fetched += 1
        # Normalize field names from various platform fetchers
        new_views = int(metrics.get("views") or metrics.get("plays") or 0)
        new_likes = int(metrics.get("likes") or 0)
        new_comments = int(metrics.get("comments") or metrics.get("replies") or 0)
        new_shares = int(metrics.get("shares") or metrics.get("reposts") or metrics.get("retweets") or 0)
        new_saves = int(metrics.get("saves") or metrics.get("saved") or 0)

        old_views = int(row["views"] or 0)
        old_likes = int(row["likes"] or 0)
        old_comments = int(row["comments"] or 0)
        old_shares = int(row["shares"] or 0)
        old_saves = int(row["saves"] or 0)

        # 2026-07-23 SAFETY: view counts are MONOTONIC — they only go up
        # over time (Meta/YT never reduce them). If the fetcher returns
        # a LOWER value than what we already have, that's a fetcher bug
        # (metric-shape drift on old posts, Meta v22 field deprecation,
        # etc). NEVER overwrite a higher stored value with a lower fetched
        # one — that's data loss. Take max() per field.
        #
        # First batch of the initial script overwrote ~100 rows with
        # lower values because Meta returns 0 for some old-post metric
        # fields. This guard prevents recurrence.
        views = max(old_views, new_views)
        likes = max(old_likes, new_likes)
        comments = max(old_comments, new_comments)
        shares = max(old_shares, new_shares)
        saves = max(old_saves, new_saves)

        # Skip UPDATE if nothing changed
        if (
            views == old_views and likes == old_likes and comments == old_comments
            and shares == old_shares and saves == old_saves
        ):
            logger.info(
                "NOCHG %s/%s/%s views=%d (fetcher matches stored, only stamp mf)",
                niche, platform, row["id"][:8], views,
            )
            if commit:
                conn.execute(
                    "UPDATE publishing_analytics SET metrics_fetched = NOW() WHERE id = %s::uuid",
                    (row["id"],),
                )
                conn.commit()
                updated += 1
            continue

        if commit:
            conn.execute(
                """
                UPDATE publishing_analytics
                SET views = %s,
                    likes = %s,
                    comments = %s,
                    shares = %s,
                    saves = %s,
                    metrics_fetched = NOW()
                WHERE id = %s::uuid
                """,
                (views, likes, comments, shares, saves, row["id"]),
            )
            conn.commit()
            updated += 1
            logger.info(
                "UPD   %s/%s/%s views=%d→%d likes=%d→%d (guarded)",
                niche, platform, row["id"][:8], old_views, views, old_likes, likes,
            )
        else:
            logger.info(
                "DRY   %s/%s/%s would set views=%d→%d likes=%d→%d",
                niche, platform, row["id"][:8], old_views, views, old_likes, likes,
            )

    return fetched, updated, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["pa", "orphan", "bandit"], default="pa")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--commit-bandit", action="store_true",
        help="Required in addition to --commit for --phase bandit (double-guard)",
    )
    args = parser.parse_args()

    logger.info("phase=%s limit=%d commit=%s", args.phase, args.limit, args.commit)
    if args.commit and args.phase == "pa":
        logger.warning("REMINDER: consider backing up publishing_analytics before --commit")

    with psycopg.connect(_get_dsn(), row_factory=dict_row) as conn:
        if args.phase == "pa":
            candidates = _fetch_candidates(conn, args.limit)
            logger.info("found %d candidates for phase=pa", len(candidates))
            fetched, updated, skipped = _phase_pa(conn, candidates, args.commit)
            logger.info(
                "phase=pa DONE: fetched=%d updated=%d skipped=%d",
                fetched, updated, skipped,
            )
        elif args.phase == "orphan":
            logger.warning(
                "phase=orphan not implemented in this pass — see docstring "
                "for the next-session plan. Ship phase=pa first + review "
                "results before enabling phase=orphan."
            )
            return 0
        elif args.phase == "bandit":
            logger.warning(
                "phase=bandit requires phase=orphan complete + explicit "
                "--commit-bandit. Not enabled in this pass — irreversible "
                "bandit writes need a dedicated review session."
            )
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
