"""Daily Facebook post-survival check.

Audit ref: R-33.

The audit found that the documented "FB 24h survival check
(REMOVED_BY_META)" was unimplemented — zero occurrences in
``genlab-core/src/``, the Sprint-47 "DELETED status + alert" claim
was stale doc-drift. Meta-removed reels were never detected.

This module closes that gap:

  1. Find ``publishing_analytics`` rows where
        platform = 'facebook'
        status   = 'SUCCESS'
        published_at BETWEEN now() - max_age_hours AND now() - min_age_hours
        post_id IS NOT NULL
        AND extra->>'fb_survival_checked' IS NULL (first-pass) OR a re-check window.
  2. For each, call ``FacebookClient.check_post_alive(post_id)``.
        * True  → stamp ``extra.fb_survival_checked`` so we don't re-check daily.
        * False → flip ``status`` to ``REMOVED_BY_META`` + stamp
                  ``extra.removed_at``.
        * None  → log + skip (transient; try again next day).

Run via the ``genlab-fb-survival-check.service`` systemd unit; daily
at 06:00 UTC. The earliest a post can be checked is 24h after publish
(default ``min_age_hours=24``); the latest is 7 days (``max_age_hours=
168``) — far enough back to catch a missed daily run, narrow enough
to avoid full-table scans.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

REMOVED_STATUS = "REMOVED_BY_META"
EXTRA_KEY_CHECKED = "fb_survival_checked"
EXTRA_KEY_REMOVED = "removed_at"

# A published FB row progresses through these statuses as metric_collector
# walks the engagement windows: SUCCESS → INSIGHTS_6H → INSIGHTS_24H →
# INSIGHTS_48H → INSIGHTS_168H. Any of them is still a "live post that
# was successfully published" and is a valid survival-check target — a
# post can be removed at any age.
LIVE_POST_STATUSES = (
    "SUCCESS",
    "INSIGHTS_6H",
    "INSIGHTS_24H",
    "INSIGHTS_48H",
    "INSIGHTS_168H",
)


def find_candidates(
    *,
    dsn: str | None = None,
    min_age_hours: int = 24,
    max_age_hours: int = 168,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Pull FB SUCCESS rows in the survival-check window.

    Skips rows already marked checked (``extra->>'fb_survival_checked'``
    is set) so the daily run is idempotent and cheap.
    """
    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, post_id, niche_id, published_at, extra
                  FROM publishing_analytics
                 WHERE platform = 'facebook'
                   AND status   = ANY(%s)
                   AND post_id IS NOT NULL
                   AND post_id <> ''
                   AND published_at IS NOT NULL
                   AND published_at <= now() - interval '{min_age_hours} hours'
                   AND published_at >= now() - interval '{max_age_hours} hours'
                   AND (extra->>%s) IS NULL
                 ORDER BY published_at DESC
                 LIMIT %s
                """,
                (list(LIVE_POST_STATUSES), EXTRA_KEY_CHECKED, limit),
            )
            return list(cur.fetchall())


def mark_checked(
    *,
    row_id: str,
    dsn: str | None = None,
) -> None:
    """Stamp ``extra.fb_survival_checked`` so we don't re-check the row tomorrow."""
    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
    timestamp = datetime.now(UTC).isoformat()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            UPDATE publishing_analytics
               SET extra = COALESCE(extra, '{}'::jsonb)
                         || jsonb_build_object(%s, %s::text),
                   updated_at = now()
             WHERE id = %s
            """,
            (EXTRA_KEY_CHECKED, timestamp, row_id),
        )


def mark_removed(
    *,
    row_id: str,
    dsn: str | None = None,
) -> None:
    """Flip ``status`` → REMOVED_BY_META and stamp the removal timestamp.

    Both the status flip and the JSONB stamp happen in the same
    ``UPDATE`` so an interrupted run can't leave half-state.
    """
    dsn = dsn or os.environ.get("DATABASE_URL") or "dbname=genlab"
    timestamp = datetime.now(UTC).isoformat()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            UPDATE publishing_analytics
               SET status = %s,
                   extra  = COALESCE(extra, '{}'::jsonb)
                          || jsonb_build_object(%s, %s::text, %s, %s::text),
                   updated_at = now()
             WHERE id = %s
            """,
            (
                REMOVED_STATUS,
                EXTRA_KEY_CHECKED,
                timestamp,
                EXTRA_KEY_REMOVED,
                timestamp,
                row_id,
            ),
        )


def run_check(
    *,
    client: Any | None = None,
    dsn: str | None = None,
    min_age_hours: int = 24,
    max_age_hours: int = 168,
    limit: int = 200,
) -> dict[str, int]:
    """Execute one pass of the survival check.

    Args:
        client: a ``FacebookClient`` (or anything with
            ``check_post_alive(post_id) -> bool | None``). Injected
            so tests don't need real credentials; defaults to a
            freshly-constructed ``FacebookClient`` reading env vars.
        dsn: Postgres connection. Defaults to ``DATABASE_URL``.
        min_age_hours/max_age_hours/limit: see :func:`find_candidates`.

    Returns:
        Dict with ``examined``, ``alive``, ``removed``, ``ambiguous``,
        and ``error`` counts. Sent to the systemd journal so an alert
        can fire on a sudden spike in removals.
    """
    if client is None:
        # Lazy import — keeps the module importable without
        # ``META_ACCESS_TOKEN`` (e.g. in unit tests).
        from genlab_core.platforms.facebook import FacebookClient

        client = FacebookClient()

    counts = {
        "examined": 0,
        "alive": 0,
        "removed": 0,
        "ambiguous": 0,
        "error": 0,
    }

    candidates = find_candidates(
        dsn=dsn,
        min_age_hours=min_age_hours,
        max_age_hours=max_age_hours,
        limit=limit,
    )
    logger.info(
        "[fb_survival_check] examining %d FB rows (window: %dh..%dh ago)",
        len(candidates),
        min_age_hours,
        max_age_hours,
    )

    for row in candidates:
        counts["examined"] += 1
        post_id = row["post_id"]
        try:
            alive = client.check_post_alive(post_id)
        except Exception as exc:
            counts["error"] += 1
            logger.warning("[fb_survival_check] client error for %s: %s", post_id, exc)
            continue

        if alive is True:
            counts["alive"] += 1
            mark_checked(row_id=row["id"], dsn=dsn)
        elif alive is False:
            counts["removed"] += 1
            mark_removed(row_id=row["id"], dsn=dsn)
            logger.warning(
                "[fb_survival_check] REMOVED_BY_META: niche=%s post=%s (published %s)",
                row.get("niche_id"),
                post_id,
                row.get("published_at"),
            )
        else:
            counts["ambiguous"] += 1
            # Don't stamp checked — try again next run.
            logger.debug(
                "[fb_survival_check] ambiguous: post=%s — retrying next run",
                post_id,
            )

    logger.info(
        "[fb_survival_check] done: %s",
        json.dumps(counts, separators=(",", ":")),
    )
    return counts


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m genlab_core.monitoring.fb_survival_check",
        description=(
            "Check whether 24h-old Facebook posts are still live; "
            "flip status to REMOVED_BY_META when Meta has taken them down."
        ),
    )
    p.add_argument(
        "--min-age-hours",
        type=int,
        default=24,
        help="Skip posts younger than this (default 24).",
    )
    p.add_argument(
        "--max-age-hours",
        type=int,
        default=168,
        help="Skip posts older than this (default 168 = 7 days).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max rows examined per run (default 200).",
    )
    p.add_argument("--dsn", help="Override DATABASE_URL.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    counts = run_check(
        dsn=args.dsn,
        min_age_hours=args.min_age_hours,
        max_age_hours=args.max_age_hours,
        limit=args.limit,
    )
    print(json.dumps(counts, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.exit(main())
