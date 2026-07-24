#!/usr/bin/env python3
"""One-shot: backfill blueprints columns from extra JSONB.

Handles two fields today (action_taken_source, hook_classifier_score)
both discovered via test_promoted_columns_vs_db_schema on 2026-07-24.
Same class-of-bug: column exists in DB but wasn't in PROMOTED_COLUMNS,
so writes silently landed in extra JSONB.



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
        # Field 1: action_taken_source (TEXT).
        n_ats = _preview_and_maybe_backfill(
            conn,
            field="action_taken_source",
            cast="",  # text, no cast
            apply=args.commit,
        )

        # Field 2: hook_classifier_score (FLOAT).
        n_hcs = _preview_and_maybe_backfill(
            conn,
            field="hook_classifier_score",
            cast="::float",  # extra->>'val' returns text; column is float
            apply=args.commit,
        )

        if args.commit:
            conn.commit()
            logger.info(
                "TOTAL BACKFILLED: action_taken_source=%d hook_classifier_score=%d",
                n_ats,
                n_hcs,
            )
        return 0


def _preview_and_maybe_backfill(conn, *, field: str, cast: str, apply: bool) -> int:
    """Preview rows where extra has the field but column is NULL/blank.
    If ``apply``, execute the UPDATE and return rowcount. Else return 0
    after logging the preview.

    ``cast`` is appended after ``extra->>%s`` in the SET clause when
    the target column type isn't TEXT (e.g. FLOAT for
    hook_classifier_score). Empty string for TEXT columns."""
    rows = conn.execute(
        f"""
        SELECT id, niche_id, extra->>%s AS extra_val
        FROM blueprints
        WHERE extra->>%s IS NOT NULL
          AND ({field} IS NULL{" OR " + field + " = ''" if cast == "" else ""})
        ORDER BY reviewed_at DESC NULLS LAST
        LIMIT 500
        """,
        (field, field),
    ).fetchall()

    logger.info("[%s] %d rows need backfill", field, len(rows))
    for r in rows[:5]:
        logger.info("  bp=%s niche=%s → %s=%s", str(r[0])[:8], r[1], field, r[2])
    if len(rows) > 5:
        logger.info("  (... and %d more)", len(rows) - 5)

    if not apply or not rows:
        return 0

    # Backfill in a single UPDATE.
    cur = conn.execute(
        f"""
        UPDATE blueprints
        SET {field} = (extra->>%s){cast}
        WHERE extra->>%s IS NOT NULL
          AND ({field} IS NULL{" OR " + field + " = ''" if cast == "" else ""})
        """,
        (field, field),
    )
    logger.info("[%s] BACKFILLED %d rows", field, cur.rowcount)
    return cur.rowcount


if __name__ == "__main__":
    sys.exit(main())
