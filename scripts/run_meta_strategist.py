#!/usr/bin/env python3
"""Phase 2.G — Meta-strategist.

Weekly LLM review of the strategist's proposal quality using
outcome_verifier verdicts. Answers: "was last week's strategist
worth its cost? Which proposal types should we trust more/less?"

## Data pipeline

  1. Query strategist_reports for prior week's proposals
  2. Join to strategist_outcome_verification for verdicts
  3. Bucket into per-proposal-type accuracy stats
  4. Send summary to Anthropic → structured JSON verdict
  5. Persist to meta_strategist_reports

## Cost

One LLM call per weekly run. Uses budget gate — same caller_type
'optional' as the strategist itself.

## Usage

    uv run python scripts/run_meta_strategist.py
    uv run python scripts/run_meta_strategist.py --dry-run
    uv run python scripts/run_meta_strategist.py --week-of 2026-08-10

## Exit codes

  * 0 — completed (row written or dry-run summary)
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

logger = logging.getLogger("run_meta_strategist")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--week-of", default=None,
                    help="Override week (YYYY-MM-DD, default = last Mon)")
    return ap.parse_args(argv)


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _fetch_signal(conn, week_of: date) -> dict:
    """Aggregate per-proposal-type accuracy from Phase 1.C data."""
    signal = {"per_type": {}, "totals": {"reviewed": 0, "verdicts": 0}}
    try:
        rows = conn.execute(
            """
            SELECT proposal_type,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE verdict = 'improved') AS improved,
                   COUNT(*) FILTER (WHERE verdict = 'unchanged') AS unchanged,
                   COUNT(*) FILTER (WHERE verdict = 'regressed') AS regressed
            FROM strategist_outcome_verification
            WHERE applied_at >= %s AND applied_at < %s
              AND verdict != 'pending'
            GROUP BY proposal_type
            """,
            (week_of, week_of + timedelta(days=7)),
        ).fetchall()
    except Exception as exc:
        logger.debug("[meta_strategist] signal query failed: %s", exc)
        return signal

    for r in rows or []:
        ptype = r.get("proposal_type") if hasattr(r, "get") else r[0]
        signal["per_type"][ptype] = {
            "total": int(r.get("total") if hasattr(r, "get") else r[1]),
            "improved": int(r.get("improved") if hasattr(r, "get") else r[2]),
            "unchanged": int(r.get("unchanged") if hasattr(r, "get") else r[3]),
            "regressed": int(r.get("regressed") if hasattr(r, "get") else r[4]),
        }
        signal["totals"]["reviewed"] += signal["per_type"][ptype]["total"]
        signal["totals"]["verdicts"] += signal["per_type"][ptype]["total"]
    return signal


META_SYSTEM_PROMPT = """\
You are a META-STRATEGIST reviewing the quality of an LLM-based
STRATEGIST that generates weekly content-optimization proposals for
Gen Lab's 5 social channels.

Your job: given per-proposal-type outcome verdicts (improved /
unchanged / regressed), grade the strategist's quality and emit
concrete recommendations for tuning.

## Rules

  1. Overall grade: 'A' (excellent), 'B' (good), 'C' (mixed),
     'D' (needs work), 'F' (harmful — proposals doing more harm
     than good)
  2. Per-type grades: same scale, keyed by proposal_type
  3. Recommendations: 1-3 concrete actions the operator can take
     next week to improve overall grade
  4. Cold-start (few verdicts): grade 'B' + note "insufficient data"

## Output — JSON only, no prose

{
  "overall_grade": "A" | "B" | "C" | "D" | "F",
  "per_type_grades": {"arm_add": "A", "reward_weight": "C", ...},
  "recommendations": [
    "concrete action 1",
    "concrete action 2"
  ]
}
"""


def _build_user_prompt(week_of: date, signal: dict) -> str:
    lines = [
        f"Week of: {week_of}",
        f"Total proposals with verdicts: {signal['totals']['verdicts']}",
        "",
        "Per-proposal-type outcomes:",
    ]
    if not signal["per_type"]:
        lines.append("  (no verdicts available this week)")
    for ptype, counts in signal["per_type"].items():
        denom = counts["improved"] + counts["regressed"]
        acc = (
            counts["improved"] / denom * 100 if denom > 0 else None
        )
        acc_str = f"{acc:.0f}%" if acc is not None else "N/A"
        lines.append(
            f"  {ptype}: {counts['total']} verdicts — "
            f"improved={counts['improved']}, "
            f"unchanged={counts['unchanged']}, "
            f"regressed={counts['regressed']} "
            f"(accuracy: {acc_str})"
        )
    lines.append("")
    lines.append("Grade the strategist and emit recommendations. JSON only.")
    return "\n".join(lines)


def _parse_verdict(raw: str) -> dict:
    """Parse LLM JSON output. Returns default 'B' + empty
    recommendations on any parse error."""
    default = {
        "overall_grade": "B",
        "per_type_grades": {},
        "recommendations": [],
    }
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("[meta_strategist] JSON decode failed: %s", exc)
        return default
    return {
        "overall_grade": str(obj.get("overall_grade", "B"))[:1].upper(),
        "per_type_grades": obj.get("per_type_grades") or {},
        "recommendations": obj.get("recommendations") or [],
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

    week_of = (
        date.fromisoformat(args.week_of) if args.week_of
        else _monday_of(date.today() - timedelta(days=7))
    )

    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        signal = _fetch_signal(conn, week_of)

    print()
    print(f"Meta-strategist review for week_of={week_of}")
    print(f"  Total verdicts: {signal['totals']['verdicts']}")
    for ptype, counts in signal["per_type"].items():
        print(f"  {ptype}: {counts}")

    if signal["totals"]["verdicts"] == 0:
        print("  → No verdicts yet — skipping LLM call")
        print("    (first outcomes ready ~48h after strategist-apply fires)")
        return 0

    # Call LLM
    from genlab_core.intelligence.anthropic_client import (
        AnthropicStrategistClient,
    )
    client = AnthropicStrategistClient()
    user_prompt = _build_user_prompt(week_of, signal)
    try:
        result = client.generate_report(META_SYSTEM_PROMPT, user_prompt)
    except Exception as exc:
        logger.warning("[meta_strategist] LLM call failed: %s", exc)
        return 0
    verdict = _parse_verdict(result.text)
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)

    print(f"\nMeta-strategist verdict:")
    print(f"  Overall grade: {verdict['overall_grade']}")
    for ptype, grade in verdict["per_type_grades"].items():
        print(f"  {ptype}: {grade}")
    print(f"  Recommendations:")
    for rec in verdict["recommendations"]:
        print(f"    - {rec}")
    print(f"  LLM cost: ${cost:.4f}")

    if args.dry_run:
        print("\nDRY RUN — no write.")
        return 0

    # Persist
    with psycopg.connect(dsn) as conn:
        try:
            conn.execute(
                """
                INSERT INTO meta_strategist_reports
                  (week_of, proposals_reviewed, verdicts_available,
                   overall_grade, per_type_grades, recommendations,
                   llm_cost_usd)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                ON CONFLICT (week_of) DO UPDATE SET
                  proposals_reviewed = EXCLUDED.proposals_reviewed,
                  verdicts_available = EXCLUDED.verdicts_available,
                  overall_grade = EXCLUDED.overall_grade,
                  per_type_grades = EXCLUDED.per_type_grades,
                  recommendations = EXCLUDED.recommendations,
                  llm_cost_usd = EXCLUDED.llm_cost_usd,
                  run_at = NOW()
                """,
                (
                    week_of,
                    signal["totals"]["reviewed"],
                    signal["totals"]["verdicts"],
                    verdict["overall_grade"],
                    json.dumps(verdict["per_type_grades"]),
                    json.dumps(verdict["recommendations"]),
                    cost,
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("[meta_strategist] persist failed: %s", exc)
            return 0

    logger.info("meta_strategist: wrote row for week_of=%s", week_of)
    return 0


if __name__ == "__main__":
    sys.exit(main())
