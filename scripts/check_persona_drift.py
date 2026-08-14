#!/usr/bin/env python3
"""Phase 4.D — persona voice drift checker.

Fires every 6h via ``genlab-persona-drift.timer``. Per niche:

  1. Find recent PUBLISHED blueprints without an existing drift score.
  2. Sample every N-th (default 20 per roadmap) so LLM cost stays
     minimal — roughly 1-3 checks per niche per day given typical
     publish cadence.
  3. Compute drift via LLM (respects Phase 2.D budget gate).
  4. Persist to persona_drift_scores.
  5. If drift_score < 0.6, insert a WARNING row in pipeline_alerts.

## Alert idempotency

pipeline_alerts rows use check_name = 'persona_drift:{niche}' so
repeated below-threshold blueprints in the same niche update the
same row (via the existing check-name-based dedup pattern).

## Usage

    uv run python scripts/check_persona_drift.py
    uv run python scripts/check_persona_drift.py --dry-run
    uv run python scripts/check_persona_drift.py --sample-rate 1  # score every
    uv run python scripts/check_persona_drift.py --niche gaming

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime

logger = logging.getLogger("check_persona_drift")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")

# Roadmap: "every 20th publish". Runner samples 1-of-N unchecked
# to hit that in expectation over time.
DEFAULT_SAMPLE_RATE = 20
ALERT_THRESHOLD = 0.6  # matches persona_drift.ALERT_THRESHOLD


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE,
                    help="Score 1 out of N unchecked blueprints (default 20)")
    ap.add_argument("--lookback-days", type=int, default=7)
    return ap.parse_args(argv)


def _find_unscored_blueprints(conn, niche_id: str, lookback_days: int):
    """PUBLISHED blueprints in lookback window that don't yet have
    a drift score. Fail-open to []."""
    try:
        rows = conn.execute(
            """
            SELECT b.id::text AS bp_id, b.hook_text
            FROM blueprints b
            LEFT JOIN persona_drift_scores pds
              ON pds.blueprint_id = b.id
            WHERE b.niche_id = %s
              AND b.status = 'PUBLISHED'
              AND b.updated_at >= NOW() - (%s || ' days')::INTERVAL
              AND b.hook_text IS NOT NULL AND b.hook_text != ''
              AND pds.id IS NULL
            ORDER BY b.updated_at DESC
            LIMIT 100
            """,
            (niche_id, lookback_days),
        ).fetchall()
    except Exception as exc:
        logger.warning("[drift] query failed niche=%s: %s", niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        {
            "blueprint_id": r.get("bp_id") if hasattr(r, "get") else r[0],
            "hook_text": r.get("hook_text") if hasattr(r, "get") else r[1],
        }
        for r in rows or []
    ]


def _persist_score(
    conn, blueprint_id: str, niche_id: str, hook_text: str, result,
) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO persona_drift_scores
              (blueprint_id, niche_id, drift_score, hook_text,
               persona_hash, reasons, llm_cost_usd)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (blueprint_id) DO NOTHING
            """,
            (
                blueprint_id, niche_id, result.drift_score, hook_text,
                result.persona_hash, json.dumps(result.reasons),
                result.llm_cost_usd,
            ),
        )
        return True
    except Exception as exc:
        logger.warning("[drift] persist failed bp=%s: %s", blueprint_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _emit_alert(
    conn, niche_id: str, blueprint_id: str, drift_score: float,
    reasons: list[str],
) -> bool:
    """Write a WARNING row to pipeline_alerts. Uses stable check_name
    per niche so repeated hits update the same row rather than
    proliferate."""
    try:
        message = (
            f"persona drift detected — score {drift_score:.2f} < {ALERT_THRESHOLD} "
            f"for blueprint {blueprint_id[:8]}. Reasons: "
            + " | ".join(reasons[:2])
        )
        conn.execute(
            """
            INSERT INTO pipeline_alerts
              (check_name, severity, message, first_seen_at, last_seen_at,
               occurrence_count, extra)
            VALUES (%s, 'warning', %s, NOW(), NOW(), 1, %s::jsonb)
            ON CONFLICT (check_name) DO UPDATE SET
              severity = 'warning',
              message = EXCLUDED.message,
              last_seen_at = NOW(),
              occurrence_count = pipeline_alerts.occurrence_count + 1,
              extra = EXCLUDED.extra
            """,
            (
                f"persona_drift:{niche_id}",
                message,
                json.dumps({
                    "blueprint_id": blueprint_id,
                    "drift_score": drift_score,
                    "reasons": reasons,
                }),
            ),
        )
        return True
    except Exception as exc:
        logger.warning("[drift] alert emit failed niche=%s: %s", niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _run_niche(
    conn, niche_id: str, sample_rate: int, lookback_days: int, dry_run: bool,
) -> dict:
    from genlab_core.quality.persona_drift import compute_drift

    counts = {
        "candidates": 0, "sampled": 0, "scored": 0,
        "alerts": 0, "skipped": 0,
    }
    blueprints = _find_unscored_blueprints(conn, niche_id, lookback_days)
    counts["candidates"] = len(blueprints)
    if not blueprints:
        return counts

    # 1-of-N sampling: take every Nth from the recent list. With
    # sample_rate=20 and 100 candidates → 5 sampled.
    sampled = blueprints[::sample_rate] if sample_rate > 1 else blueprints
    counts["sampled"] = len(sampled)
    print(f"  {niche_id}: {len(blueprints)} unscored → sampling {len(sampled)} (1-of-{sample_rate})")

    for bp in sampled:
        if dry_run:
            print(f"  [DRY] {bp['blueprint_id'][:8]} hook={bp['hook_text'][:50]!r}")
            continue

        result = compute_drift(bp["hook_text"], niche_id)
        if not result.ok:
            counts["skipped"] += 1
            print(f"  skip {bp['blueprint_id'][:8]}: {result.reason_code}")
            continue

        if _persist_score(
            conn, bp["blueprint_id"], niche_id, bp["hook_text"], result,
        ):
            counts["scored"] += 1
            print(
                f"  {bp['blueprint_id'][:8]} drift={result.drift_score:.2f} "
                f"cost=${result.llm_cost_usd:.4f}"
            )
            if result.drift_score < ALERT_THRESHOLD:
                if _emit_alert(
                    conn, niche_id, bp["blueprint_id"],
                    result.drift_score, result.reasons,
                ):
                    counts["alerts"] += 1

    if not dry_run and counts["scored"] > 0:
        conn.commit()
    return counts


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

    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    totals = {"candidates": 0, "sampled": 0, "scored": 0, "alerts": 0, "skipped": 0}
    print(f"\nChecking persona drift (sample_rate=1-of-{args.sample_rate}, threshold={ALERT_THRESHOLD})")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            counts = _run_niche(
                conn, niche_id, args.sample_rate, args.lookback_days, args.dry_run,
            )
            for k, v in counts.items():
                totals[k] += v

    logger.info(
        "[drift] totals: candidates=%d sampled=%d scored=%d "
        "alerts=%d skipped=%d",
        totals["candidates"], totals["sampled"], totals["scored"],
        totals["alerts"], totals["skipped"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
