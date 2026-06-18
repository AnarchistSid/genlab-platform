#!/usr/bin/env python3
"""W4.4 backfill: compute auto_approval_confidence for pre-W4.1 blueprints.

W4.1 (PR #284, shipped 2026-06-17) added per-blueprint
``auto_approval_confidence`` + ``auto_approval_predicted`` to the
``extra`` JSONB at push_to_backlog time. Blueprints created BEFORE
that PR lack both fields — 466 of them across all 5 niches as of
2026-06-18. The track-record card (W4.4, PR #306) and any future
historical-confidence-analytics consumer can't backfill against
those rows.

This script re-runs ``auto_approval_gate.evaluate()`` against each
qualifying blueprint, writes the result into ``extra``, and updates
the row. Idempotent: re-running re-evaluates the SAME blueprints
the same way, which is a no-op against rows that already have the
field (the WHERE clause filters those out).

Safety properties:
1. **READ ONLY composite_score / virality_score / validation_status
   / visual_paths / hook_text from the existing blueprint** — never
   re-computes or alters the underlying signals.
2. **WRITE ONLY into ``extra``** — never touches top-level columns,
   never changes ``status``, never touches ``scheduled_for``.
3. **Per-row try/except** — a single broken blueprint can't abort
   the batch. Errors logged; batch continues.
4. **Tenant-scoped writes via niche_scope()** — SR-A/C/D compliant.
5. **Cold-start tolerance** — missing fields treated as "unknown"
   per the gate's defensive contract; never raises.

Usage:
    # Dry-run (default): print what would happen, no writes
    uv run python scripts/backfill_confidence_score.py

    # Apply: actually write the backfilled fields
    uv run python scripts/backfill_confidence_score.py --apply

    # Scope to one niche (useful for testing on prod)
    uv run python scripts/backfill_confidence_score.py --niche anime --apply

Exit codes:
    0 = success (any number of rows backfilled, or dry-run)
    1 = abort (DB connection failed, niche resolution failed)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_confidence_score")


VALID_NICHES = {"ai_creators", "anime", "gaming", "movies", "sports"}


def _build_synth_blueprint(row: dict) -> dict:
    """Build the synthetic blueprint dict the gate expects, from a
    Postgres row. Mirrors the W4.1 push_to_backlog code at
    ``stages/push_to_backlog.py:1685``.

    The gate's evaluate() reads from these specific paths:
      * blueprint["hook_text"]            — top-level
      * blueprint["visual_paths"]         — top-level (fallback)
      * blueprint["extra"]["composite_score"]
      * blueprint["extra"]["virality_score"]
      * blueprint["extra"]["validation_status"]
      * blueprint["extra"]["visual_paths"] — primary visual source

    Anything else (id, niche_id, status, candidate_id) is ignored by
    evaluate() — it's a pure function over signal fields.
    """
    extra = row.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}

    return {
        "hook_text": row.get("hook_text") or "",
        # ``visual_paths`` is checked in both places by the gate
        # (defensively, since some old blueprints had it top-level).
        # Prefer extra over top-level if both exist; the gate's
        # _decode_visual_paths handles both shapes.
        "visual_paths": extra.get("visual_paths") or row.get("visual_paths") or "",
        "extra": {
            "composite_score": extra.get("composite_score"),
            "virality_score": extra.get("virality_score"),
            "validation_status": extra.get("validation_status"),
            "visual_paths": extra.get("visual_paths"),
        },
    }


def _backfill_one(conn, row: dict, apply: bool) -> tuple[bool, str]:
    """Returns (success, message). On success, message describes the
    action taken; on failure, a human-readable error."""
    from genlab_core.scheduling.auto_approval_gate import evaluate

    try:
        synth = _build_synth_blueprint(row)
        decision = evaluate(synth)
        confidence = round(decision.confidence, 4)
        approved = decision.approved
    except Exception as exc:
        return False, f"evaluate() raised: {exc}"

    if not apply:
        return True, (
            f"would update id={row['id']} niche={row['niche_id']} "
            f"confidence={confidence} approved={approved}"
        )

    try:
        with conn.cursor() as cur:
            # Use jsonb_set on the existing extra so we don't clobber
            # other keys. Two writes: confidence + predicted.
            cur.execute(
                """
                UPDATE blueprints
                SET extra = jsonb_set(
                    jsonb_set(
                        COALESCE(extra, '{}'::jsonb),
                        '{auto_approval_confidence}',
                        to_jsonb(%s::numeric),
                        true
                    ),
                    '{auto_approval_predicted}',
                    to_jsonb(%s::boolean),
                    true
                )
                WHERE id = %s
                  AND NOT (extra ? 'auto_approval_confidence')
                """,
                (confidence, approved, row["id"]),
            )
            conn.commit()
    except Exception as exc:
        conn.rollback()
        return False, f"UPDATE failed: {exc}"

    return True, (
        f"updated id={row['id']} niche={row['niche_id']} "
        f"confidence={confidence} approved={approved}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the backfilled fields (default: dry-run)",
    )
    parser.add_argument(
        "--niche",
        choices=sorted(VALID_NICHES) + ["all"],
        default="all",
        help="Niche to backfill (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max rows to process (default: no limit)",
    )
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 1

    try:
        from genlab_core.storage.tenant_context import niche_scope, pg_connect
    except ImportError as exc:
        logger.error("genlab_core.storage.tenant_context not importable: %s", exc)
        return 1

    # Build the WHERE clause:
    #   * extra does NOT already have auto_approval_confidence
    #   * status is anything (we backfill ARCHIVED + PUBLISHED + VR
    #     equally — historical analytics needs all of them)
    #   * niche filter applied below
    niche_clause = "" if args.niche == "all" else "AND niche_id = %s"
    limit_clause = "" if args.limit is None else f"LIMIT {int(args.limit)}"

    # ``visual_paths`` is NOT a top-level column on blueprints — the
    # gate's defensive ``blueprint.get("visual_paths") or extra.get(...)``
    # check returns the extra value (which is the only one populated
    # in this schema). Select only real columns + extra.
    select_sql = f"""
        SELECT id, niche_id, hook_text, extra
        FROM blueprints
        WHERE NOT (extra ? 'auto_approval_confidence')
          {niche_clause}
        ORDER BY created_at DESC
        {limit_clause}
    """
    select_params = () if args.niche == "all" else (args.niche,)

    # Admin scope for the SELECT — we cross niches when --niche all
    scope = "all" if args.niche == "all" else args.niche
    success_count = 0
    fail_count = 0
    skipped_count = 0

    with niche_scope(scope):
        with pg_connect(dsn, niche_id=scope) as conn:
            with conn.cursor() as cur:
                cur.execute(select_sql, select_params)
                rows = cur.fetchall()
            # Convert tuple rows to dicts (psycopg returns tuples by
            # default when using `cursor` instead of `dict_row` factory)
            cols = ["id", "niche_id", "hook_text", "extra"]
            row_dicts = [dict(zip(cols, r, strict=True)) for r in rows]

            logger.info(
                "Backfill %s — %s mode — %d blueprint(s) need backfill",
                args.niche,
                "APPLY" if args.apply else "DRY-RUN",
                len(row_dicts),
            )

            for row in row_dicts:
                ok, msg = _backfill_one(conn, row, apply=args.apply)
                if ok:
                    success_count += 1
                    if success_count <= 5 or success_count % 50 == 0:
                        logger.info("  %s", msg)
                else:
                    fail_count += 1
                    logger.warning("  FAIL: %s", msg)

    logger.info(
        "Done. success=%d fail=%d skipped=%d",
        success_count,
        fail_count,
        skipped_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
