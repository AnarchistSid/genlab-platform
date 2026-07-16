#!/usr/bin/env python3
"""One-shot backfill: normalize ``facebook:facebook:`` post_id rows in analytics.

Context (2026-07-14)
--------------------
PR #748 (2026-07-09) fixed the write-side double-prefix bug at
``pending_feedback_task.to_sharepoint_fields:111`` — it was blindly
prepending ``{platform}:`` to ``self.platform_post_id`` even when the
id was already prefixed upstream. That fix backfilled 297 rows in
``pending_feedback`` but did NOT touch the ``analytics`` table.

Session 2026-07-14 diagnostic on the drift detector found 85 rows in
``analytics`` with ``post_id LIKE 'facebook:facebook:%'`` (16-19 per
niche × 5 niches × 2026-06-14 → 2026-07-09 window). Each row has
``reach = 0`` because Meta's API can't resolve the malformed
composite id — the metric fetch silently returned empty, downstream
computed ``engagement_rate = 0 / 0 = 0.0``. These rows pollute
Analytics dashboard aggregations + Top Performers cards even though
the drift detector's ``reach > 0`` filter excludes them from the
distribution stats.

Fix strategy
------------
For each ``facebook:facebook:<id>`` row:
  1. Compute canonical ``facebook:<id>`` (strip one leading prefix)
  2. If a canonical row ALREADY EXISTS for the same ``(post_id,
     collected_at, metric_type)`` triple → DELETE the double-prefix
     row (the canonical row is authoritative)
  3. Otherwise → UPDATE the double-prefix row's post_id to canonical

Both branches converge on: one canonical row per (post_id, snapshot).
No re-fetch from Meta — the double-prefix rows all had reach=0 so
their values were useless anyway; the canonical rows (if they exist)
carry the real numbers already.

Safety
------
- Read-only preview mode via ``--dry-run`` (default).
- ``--commit`` required for actual UPDATE + DELETE.
- Idempotent — re-running finds 0 rows after the first commit.
- All actions logged with row counts so operator can audit.

Usage
-----
    # Preview
    uv run python scripts/backfill_analytics_double_prefix.py

    # Execute
    uv run python scripts/backfill_analytics_double_prefix.py --commit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("backfill_analytics_double_prefix")


def _connect():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        logger.error("psycopg not installed")
        sys.exit(1)

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    return psycopg.connect(dsn, row_factory=dict_row)


def _find_double_prefix_rows(conn) -> list[dict]:
    """Return all facebook:facebook:* rows in analytics with their canonical mate."""
    cur = conn.execute(
        """
        WITH doubles AS (
          SELECT id, post_id, collected_at, metric_type,
                 substring(post_id from 10) AS canonical_id  -- strip leading "facebook:"
          FROM analytics
          WHERE post_id LIKE 'facebook:facebook:%'
        )
        SELECT d.id AS double_id,
               d.post_id AS double_post_id,
               d.canonical_id,
               d.collected_at,
               d.metric_type,
               (SELECT a.id FROM analytics a
                WHERE a.post_id = d.canonical_id
                  AND a.collected_at = d.collected_at
                  AND a.metric_type = d.metric_type
                LIMIT 1) AS canonical_row_id
        FROM doubles d
        ORDER BY d.collected_at DESC
        """
    )
    return list(cur.fetchall())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--commit", action="store_true", help="Apply UPDATE/DELETE. Default is dry-run."
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mode = "COMMIT" if args.commit else "DRY-RUN"
    logger.info("Backfill mode: %s", mode)

    with _connect() as conn:
        rows = _find_double_prefix_rows(conn)
        if not rows:
            logger.info("No double-prefix rows to backfill. Done.")
            return

        logger.info("Found %d double-prefix rows in analytics", len(rows))

        # Bucket: canonical exists → DELETE double. Canonical missing → UPDATE double.
        to_delete = [r for r in rows if r["canonical_row_id"] is not None]
        to_update = [r for r in rows if r["canonical_row_id"] is None]

        logger.info(
            "  → %d have canonical mates (will DELETE double-prefix rows)",
            len(to_delete),
        )
        logger.info(
            "  → %d have no canonical mate (will UPDATE post_id to canonical)",
            len(to_update),
        )

        if not args.commit:
            logger.info("Dry-run only. Pass --commit to apply.")
            return

        deleted = 0
        for r in to_delete:
            conn.execute("DELETE FROM analytics WHERE id = %s", (r["double_id"],))
            deleted += 1

        updated = 0
        for r in to_update:
            conn.execute(
                "UPDATE analytics SET post_id = %s WHERE id = %s",
                (r["canonical_id"], r["double_id"]),
            )
            updated += 1

        conn.commit()
        logger.info(
            "Applied: %d DELETE + %d UPDATE = %d total", deleted, updated, deleted + updated
        )

        # Verify — should return 0 rows now
        remaining = _find_double_prefix_rows(conn)
        if remaining:
            logger.warning("%d double-prefix rows STILL present after backfill", len(remaining))
        else:
            logger.info("Verified: 0 double-prefix rows remain")


if __name__ == "__main__":
    main()
