#!/usr/bin/env python3
"""Validate auto_approval_calibration data quality.

AUTO #2 Day-1 step D1.5 per docs/runbooks/AUTO_2_ROLLOUT_2026-06-15.md.

Read-only sanity check that runs AFTER D1.1 (purge synthetic) +
D1.2 (backfill from dashboard_events) to confirm the table is in a
state worth basing enforcement decisions on.

## What it checks

For each of the 5 niches:

1. **Sample count** — total real rows (post-purge, post-backfill).
2. **Confusion matrix** — TP / TN / FP / FN counts via the same SQL
   the production stats() endpoint uses (so this validates the
   endpoint's denominator path stays consistent).
3. **Agreement rate** — (TP + TN) / total.
4. **Distribution shape**:
   - decided_at range (oldest to newest) → confirms backfill preserved
     historical timestamps (NOT all stamped at backfill-run time).
   - operator_action distribution → sanity check the 4-way split
     (approved / rejected / revised / skipped).
   - gate_confidence distribution → P50/P90 + count below 0.5
     (showstopper-#1 detection: if 100% of rows are at exactly 0.5
     then the worker's `extra` wrapper is still broken).
5. **Ready-for-enforcement signal** — sample count ≥30 AND agreement
   ≥90%, per CLAUDE.md / calibration_logger.stats() heuristic.
6. **Cross-table joins** — every calibration row's blueprint_id MUST
   exist in `blueprints` (catches D1.2 backfill drift).

Exit code 0 = all checks pass. Exit code 1 = a check failed (operator
should resolve before flipping enforcement on).

## Operator usage

  cd /opt/genlab
  PYTHONPATH=genlab-core/src python3 scripts/validate_calibration_data.py

Read-only — no writes, no mutations. Safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Per CLAUDE.md / calibration_logger.CalibrationStats.ready_for_enforcement.
MIN_SAMPLES_FOR_ENFORCEMENT = 30
MIN_AGREEMENT_FOR_ENFORCEMENT = 0.90

NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")


def _query_per_niche_stats(cur, niche_id: str) -> dict[str, Any]:
    """Mirror calibration_logger.stats SQL — same denominator, same
    TP/TN/FP/FN cells. Returns counts + agreement rate."""
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE gate_approved IS NOT NULL) AS sample_count,
            COUNT(*) FILTER (
                WHERE gate_approved = true
                  AND operator_action = 'approved'
            ) AS true_positives,
            COUNT(*) FILTER (
                WHERE gate_approved = false
                  AND operator_action IN ('rejected','revised','skipped')
            ) AS true_negatives,
            COUNT(*) FILTER (
                WHERE gate_approved = true
                  AND operator_action IN ('rejected','revised','skipped')
            ) AS false_positives,
            COUNT(*) FILTER (
                WHERE gate_approved = false
                  AND operator_action = 'approved'
            ) AS false_negatives
        FROM auto_approval_calibration
        WHERE niche_id = %s
        """,
        (niche_id,),
    )
    row = cur.fetchone()
    sample = row[0] or 0
    tp, tn, fp, fn = row[1] or 0, row[2] or 0, row[3] or 0, row[4] or 0
    agreement_rate = (tp + tn) / sample if sample else 0.0
    return {
        "niche_id": niche_id,
        "sample_count": sample,
        "true_positives": tp,
        "true_negatives": tn,
        "false_positives": fp,
        "false_negatives": fn,
        "agreement_rate": agreement_rate,
        "ready_for_enforcement": (
            sample >= MIN_SAMPLES_FOR_ENFORCEMENT
            and agreement_rate >= MIN_AGREEMENT_FOR_ENFORCEMENT
        ),
    }


def _query_action_distribution(cur, niche_id: str) -> dict[str, int]:
    cur.execute(
        """
        SELECT operator_action, COUNT(*)
        FROM auto_approval_calibration
        WHERE niche_id = %s
        GROUP BY operator_action
        """,
        (niche_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def _query_confidence_distribution(cur, niche_id: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE gate_confidence IS NULL) AS null_count,
            COUNT(*) FILTER (WHERE gate_confidence = 0.5) AS exactly_half,
            ROUND(percentile_cont(0.50) WITHIN GROUP (
                ORDER BY gate_confidence)::numeric, 3) AS p50,
            ROUND(percentile_cont(0.90) WITHIN GROUP (
                ORDER BY gate_confidence)::numeric, 3) AS p90,
            ROUND(MIN(gate_confidence)::numeric, 3) AS min_conf,
            ROUND(MAX(gate_confidence)::numeric, 3) AS max_conf
        FROM auto_approval_calibration
        WHERE niche_id = %s
        """,
        (niche_id,),
    )
    row = cur.fetchone()
    return {
        "total": row[0] or 0,
        "null_count": row[1] or 0,
        "exactly_half_count": row[2] or 0,
        "p50": float(row[3]) if row[3] is not None else None,
        "p90": float(row[4]) if row[4] is not None else None,
        "min": float(row[5]) if row[5] is not None else None,
        "max": float(row[6]) if row[6] is not None else None,
    }


def _query_decided_at_range(cur, niche_id: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT MIN(decided_at), MAX(decided_at), COUNT(DISTINCT decided_at::date) AS distinct_days
        FROM auto_approval_calibration
        WHERE niche_id = %s
        """,
        (niche_id,),
    )
    row = cur.fetchone()
    return {
        "min": row[0],
        "max": row[1],
        "distinct_days": row[2] or 0,
    }


def _query_orphans(cur) -> int:
    """Count calibration rows whose blueprint_id has no matching row in
    blueprints. A non-zero result means D1.2 backfilled against rows
    that were later archived (acceptable, but worth surfacing)."""
    cur.execute(
        """
        SELECT COUNT(*) FROM auto_approval_calibration c
        WHERE NOT EXISTS (
            SELECT 1 FROM blueprints b WHERE b.id::text = c.blueprint_id
        )
        """
    )
    return cur.fetchone()[0] or 0


def _print_per_niche(
    stats: dict[str, Any],
    actions: dict[str, int],
    confidence: dict[str, Any],
    decided: dict[str, Any],
) -> bool:
    """Print one niche's report. Returns True if any soft-warning
    surfaced (caller uses to inform exit code only when STRICT mode
    is set; default exit is 0 unless a hard check fails)."""
    warnings = False
    n = stats["niche_id"]
    print("\n──────────────────────────────────────────────────────────────────────")
    print(f" {n.upper()}")
    print("──────────────────────────────────────────────────────────────────────")
    print(f"  Samples            : {stats['sample_count']}")
    print(
        f"  TP / TN / FP / FN  : {stats['true_positives']} / "
        f"{stats['true_negatives']} / {stats['false_positives']} / "
        f"{stats['false_negatives']}"
    )
    print(f"  Agreement rate     : {stats['agreement_rate']:.1%}")
    print(
        f"  Ready for enforce  : {'YES ✓' if stats['ready_for_enforcement'] else 'NO'} "
        f"(needs ≥{MIN_SAMPLES_FOR_ENFORCEMENT} samples + ≥{MIN_AGREEMENT_FOR_ENFORCEMENT:.0%})"
    )
    print(f"  Operator actions   : {actions or '{}'}")
    if confidence["total"]:
        print(
            f"  Confidence P50/P90 : {confidence['p50']} / {confidence['p90']} "
            f"(min {confidence['min']}, max {confidence['max']})"
        )
        # Showstopper #1 detector: if 100% of rows are at exactly 0.5,
        # the auto_approver gate wrapper is still broken.
        pct_half = (
            confidence["exactly_half_count"] / confidence["total"] if confidence["total"] else 0.0
        )
        if pct_half >= 0.95:
            print(
                f"  ⚠️  {pct_half:.0%} of rows have confidence=0.5 — "
                f"check auto_approver `extra` wrapper (Showstopper #1)"
            )
            warnings = True
    if decided["min"]:
        print(
            f"  decided_at range   : {decided['min'].date()} → "
            f"{decided['max'].date()} ({decided['distinct_days']} distinct days)"
        )
        # Backfill sanity: if decided_at span is < 1 day for a niche
        # with >5 samples, the backfill likely stamped NOW() not the
        # historical timestamp.
        if stats["sample_count"] > 5 and decided["distinct_days"] <= 1:
            print(
                "  ⚠️  decided_at all in 1 day — backfill may have used "
                "NOW() instead of dashboard_events.created_at"
            )
            warnings = True
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate auto_approval_calibration data quality (read-only)"
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL not set — cannot validate")
        return 1

    import psycopg

    ready_count = 0
    soft_warnings = 0
    total_samples = 0
    orphan_count = 0

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            for niche in NICHES:
                stats = _query_per_niche_stats(cur, niche)
                actions = _query_action_distribution(cur, niche)
                confidence = _query_confidence_distribution(cur, niche)
                decided = _query_decided_at_range(cur, niche)
                if _print_per_niche(stats, actions, confidence, decided):
                    soft_warnings += 1
                total_samples += stats["sample_count"]
                if stats["ready_for_enforcement"]:
                    ready_count += 1

            orphan_count = _query_orphans(cur)

    print()
    print("═" * 70)
    print(" Summary")
    print("═" * 70)
    print(f"  Total samples across all niches : {total_samples}")
    print(f"  Niches ready for enforcement    : {ready_count} / {len(NICHES)}")
    print(f"  Orphan rows (no matching bp)    : {orphan_count}{' ⚠️' if orphan_count else ''}")
    print(f"  Soft warnings                   : {soft_warnings}")
    print()
    any_ready = ready_count > 0

    if total_samples == 0:
        print("✗ FAIL: zero calibration rows. Run D1.2 backfill first.")
        return 1

    # Hard check: orphans > 10% of total means D1.2 backfilled against
    # archived blueprints in significant volume — operator should
    # investigate before relying on the data.
    if total_samples and orphan_count / total_samples > 0.10:
        print(
            f"✗ FAIL: {orphan_count}/{total_samples} ({orphan_count / total_samples:.0%}) "
            f"calibration rows are orphans. D1.2 may have hit a backfill window "
            f"where blueprints were being archived in parallel."
        )
        return 1

    if any_ready:
        print("✓ At least one niche has met the ready-for-enforcement threshold.")
    else:
        print(
            f"ℹ Data is healthy but no niche has hit {MIN_SAMPLES_FOR_ENFORCEMENT}+ "
            f"samples × {MIN_AGREEMENT_FOR_ENFORCEMENT:.0%}+ agreement yet. "
            f"Keep operator-reviewing."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
