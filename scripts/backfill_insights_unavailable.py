#!/usr/bin/env python3
"""One-shot backfill: mark historical stuck-at-SUCCESS rows as
INSIGHTS_UNAVAILABLE so the stuck-monitor stops firing on them.

Motivating incident: 2026-08-12 audit found 257 publishing_analytics
rows stuck at SUCCESS across all niches × platforms (mostly Threads).
Root cause: insights fetcher returned empty for those posts and
`run_fetch_insights` at line 417 did `if not insights: continue`,
never advancing status. Post is likely deleted, permissions revoked,
or the fetcher hit a persistent 400. Twelve+ retry cycles across
72h means these are unrecoverable.

Age filter: only rows where published_at is >72h old (i.e., past
the last insight window's max age). Younger rows might still be
transient failures — leave them to natural retry.

Runs idempotently: repeat safely; already-INSIGHTS_UNAVAILABLE
rows are skipped by the SQL WHERE clause.

Usage:
    # Dry-run first — see how many rows would advance
    python scripts/backfill_insights_unavailable.py

    # Apply
    python scripts/backfill_insights_unavailable.py --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("backfill_insights_unavailable")


def _load_env(env_file: str = "/opt/genlab/.env") -> None:
    if os.environ.get("DATABASE_URL"):
        return
    from pathlib import Path

    env_path = Path(env_file)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--env-file", default="/opt/genlab/.env")
    ap.add_argument(
        "--min-age-hours",
        type=int,
        default=72,
        help="Only advance rows published >N hours ago (default 72).",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    _load_env(args.env_file)

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 2

    import psycopg

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            # First, count candidates
            cur.execute(
                """
                SELECT niche_id, platform, COUNT(*)
                FROM publishing_analytics
                WHERE status = 'SUCCESS'
                  AND published_at < NOW() - make_interval(hours => %s)
                GROUP BY niche_id, platform
                ORDER BY 3 DESC
                """,
                (args.min_age_hours,),
            )
            groups = cur.fetchall()
            total = sum(int(r[2]) for r in groups)

            logger.info(
                "Found %d stuck rows across %d (niche, platform) groups:",
                total, len(groups),
            )
            for niche_id, platform, n in groups:
                logger.info("  %-14s %-12s %d", niche_id, platform, n)

            if not args.apply:
                logger.info("DRY RUN — pass --apply to advance status.")
                return 0

            if total == 0:
                logger.info("Nothing to backfill.")
                return 0

            # Apply the update
            cur.execute(
                """
                UPDATE publishing_analytics
                SET status = 'INSIGHTS_UNAVAILABLE',
                    updated_at = NOW()
                WHERE status = 'SUCCESS'
                  AND published_at < NOW() - make_interval(hours => %s)
                """,
                (args.min_age_hours,),
            )
            advanced = cur.rowcount
            conn.commit()
            logger.info(
                "Advanced %d rows from SUCCESS -> INSIGHTS_UNAVAILABLE",
                advanced,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
