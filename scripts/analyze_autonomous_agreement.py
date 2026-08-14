#!/usr/bin/env python3
"""Phase 5.B session 2 — autonomous reviewer agreement analyzer.

Answers the roadmap success questions:

  * Success criterion 1: operator's strategist-review volume drops
    90%+ (measure: fraction of proposals that hit
    llm_accept/llm_reject vs operator_gate)
  * Success criterion 2: autonomous accept/reject quality matches
    operator's 85%+ agreement rate (measure: outcome-verifier
    verdict on autonomously-accepted proposals matches what
    operator-accepted proposals achieve)

Reads:
  * strategist_reports.proposals — how many proposals total, what
    the auto-accept path decided
  * strategist_outcome_verification — did they land well after
    apply?

## Usage

    uv run python scripts/analyze_autonomous_agreement.py
    uv run python scripts/analyze_autonomous_agreement.py --lookback-weeks 4

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("analyze_autonomous_agreement")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lookback-weeks", type=int, default=4)
    return ap.parse_args(argv)


def _analyze(conn, lookback_weeks: int) -> dict:
    """Compute autonomous vs operator counts + quality signals.
    All fail-open to None fields on any query error."""
    # classifier_source actual values (per Phase 1.C `_classify_
    # decision_source` at strategist_actions.py): 'llm' / 'heuristic'
    # / 'manual'. Autonomous = llm + heuristic; operator = manual.
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE classifier_source = 'heuristic')::int AS n_heuristic,
              COUNT(*) FILTER (WHERE classifier_source = 'llm')::int AS n_llm,
              COUNT(*) FILTER (WHERE classifier_source = 'manual')::int AS n_manual,
              COUNT(*)::int AS n_total
            FROM strategist_outcome_verification
            WHERE applied_at >= NOW() - (%s || ' weeks')::INTERVAL
              AND classifier_source IS NOT NULL
            """,
            (lookback_weeks,),
        ).fetchone()
    except Exception as exc:
        logger.warning("[analyzer] volume query failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {
            "n_heuristic": 0, "n_llm": 0, "n_manual": 0, "n_total": 0,
            "autonomous_share": None,
            "quality_auto": None, "quality_operator": None,
        }
    n_heur = row["n_heuristic"] or 0
    n_llm = row["n_llm"] or 0
    n_manual = row["n_manual"] or 0
    n_total = row["n_total"] or 0
    autonomous_share = None
    if n_total > 0:
        autonomous_share = ((n_heur + n_llm) / n_total * 100)

    # Quality: outcome=improved rate per source class
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE classifier_source IN ('heuristic', 'llm')
                  AND verdict = 'improved'
              )::int AS auto_improved,
              COUNT(*) FILTER (
                WHERE classifier_source IN ('heuristic', 'llm')
                  AND verdict = 'regressed'
              )::int AS auto_regressed,
              COUNT(*) FILTER (
                WHERE classifier_source = 'manual'
                  AND verdict = 'improved'
              )::int AS op_improved,
              COUNT(*) FILTER (
                WHERE classifier_source = 'manual'
                  AND verdict = 'regressed'
              )::int AS op_regressed
            FROM strategist_outcome_verification
            WHERE applied_at >= NOW() - (%s || ' weeks')::INTERVAL
              AND verdict IN ('improved', 'regressed')
            """,
            (lookback_weeks,),
        ).fetchone()
    except Exception:
        return {
            "n_heuristic": n_heur, "n_llm": n_llm, "n_manual": n_manual,
            "n_total": n_total, "autonomous_share": autonomous_share,
            "quality_auto": None, "quality_operator": None,
        }
    ai, ar = row["auto_improved"] or 0, row["auto_regressed"] or 0
    oi, orj = row["op_improved"] or 0, row["op_regressed"] or 0
    quality_auto = (ai / (ai + ar) * 100) if (ai + ar) > 0 else None
    quality_op = (oi / (oi + orj) * 100) if (oi + orj) > 0 else None
    return {
        "n_heuristic": n_heur, "n_llm": n_llm, "n_manual": n_manual,
        "n_total": n_total, "autonomous_share": autonomous_share,
        "quality_auto": quality_auto, "quality_operator": quality_op,
    }


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL unset")
        return 1

    import psycopg
    from psycopg.rows import dict_row

    print(
        f"\nAutonomous-reviewer agreement (lookback={args.lookback_weeks}wk)"
    )
    print("roadmap gate 1: autonomous_share >= 90%  |  gate 2: quality_auto ~= quality_operator (~85%)")
    print("-" * 70)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        r = _analyze(conn, args.lookback_weeks)
        print(f"Total verdicts: {r['n_total']}")
        print(f"  heuristic (auto_accept):  {r['n_heuristic']}")
        print(f"  llm (accept/reject via reviewer): {r['n_llm']}")
        print(f"  manual (operator):       {r['n_manual']}")
        share = f"{r['autonomous_share']:.1f}%" if r["autonomous_share"] is not None else "—"
        qa = f"{r['quality_auto']:.1f}%" if r["quality_auto"] is not None else "—"
        qo = f"{r['quality_operator']:.1f}%" if r["quality_operator"] is not None else "—"
        print(f"\nAutonomous share (gate 1): {share}")
        print(f"Quality auto (improved / improved+regressed): {qa}")
        print(f"Quality operator (comparison baseline):       {qo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
