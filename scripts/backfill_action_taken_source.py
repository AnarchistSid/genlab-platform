#!/usr/bin/env python3
"""One-shot: backfill blueprints.action_taken_source from extra JSONB.

Discovered 2026-07-24 via gate_examinations diagnostic + verification
that auto-approver was writing to the wrong storage layer.

Root cause: ``action_taken_source`` column existed in the blueprints
table but was NOT in PROMOTED_COLUMNS in
``genlab_core.storage.postgres``. PostgresBackend routed the field
into the ``extra`` JSONB column instead of the dedicated column.

Result: 23 auto-approvals (dating back to ai_creators enrollment)
had ``action_taken_source = NULL`` in the column but
``extra->>'action_taken_source' = 'auto_approver_v1'``. Every
downstream query filtering the column returned 0 matches -- including:

* my new outcome_readiness signal (wrongly reported 0 samples)
* calibration_logger's _NON_OPERATOR_SOURCE_TAGS exclusion (would
  double-count auto-approvals as operator agreement)
* dashboard filters distinguishing auto vs manual approvals

The column-promotion fix (b996aedf successor commit) handles future
writes. This script backfills the 23 historical rows.

Idempotent: only touches rows where extra has the value AND column is
NULL. Safe to re-run.

Usage:
    cd /opt/genlab
    set -a; source .env; set +a
    uv run python scripts/backfill_action_taken_source.py         # dry-run
    uv run python scripts/backfill_action_taken_source.py --commit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--commit",
        action="store_true",
        help="Actually write. Default is dry-run.",
    )
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set — cannot connect")
        return 2

    # Use raw psycopg — this is a one-shot admin script that scans
    # across all niches, matches the operator-tool category on the
    # allowlist (backfill scripts / retro_credit).
    import psycopg

    with psycopg.connect(dsn) as conn:
        # Preview: which rows are affected.
        rows = conn.execute(
            """
            SELECT id, niche_id, extra->>'action_taken_source' AS extra_val
            FROM blueprints
            WHERE extra->>'action_taken_source' IS NOT NULL
              AND (action_taken_source IS NULL OR action_taken_source = '')
            ORDER BY reviewed_at DESC NULLS LAST
            """
        ).fetchall()

        logger.info("Found %d rows needing backfill", len(rows))
        for r in rows[:10]:
            logger.info("  bp=%s niche=%s → source=%s", str(r[0])[:8], r[1], r[2])
        if len(rows) > 10:
            logger.info("  (... and %d more)", len(rows) - 10)

        if not args.commit:
            logger.info("DRY-RUN. Re-run with --commit to write.")
            return 0

        # Backfill in a single UPDATE — atomic, one round-trip.
        cur = conn.execute(
            """
            UPDATE blueprints
            SET action_taken_source = extra->>'action_taken_source'
            WHERE extra->>'action_taken_source' IS NOT NULL
              AND (action_taken_source IS NULL OR action_taken_source = '')
            """
        )
        updated = cur.rowcount
        conn.commit()
        logger.info("BACKFILLED %d rows", updated)
        return 0


if __name__ == "__main__":
    sys.exit(main())
