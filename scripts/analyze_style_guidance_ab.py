#!/usr/bin/env python3
"""Phase 4.C session 2 — style guidance A/B analyzer.

Compares reward_48h means between blueprints that were written
with style-guidance injection (treatment) vs without (control).
Persists a per-niche summary the operator can read to decide
whether to raise the rollout %.

## Data source

Blueprint has ``extra->>'style_guidance_injected'`` = 'true' or
'false' (persisted by writer wire). Join to
publishing_analytics for reward_48h; group by injection state.

## Metrics

  * n_control / n_treatment — sample sizes
  * mean_control / mean_treatment — reward_48h averages
  * lift_pct — (treatment - control) / control × 100
  * roadmap gate: "15%+ higher after 4 weeks"

## Usage

    uv run python scripts/analyze_style_guidance_ab.py
    uv run python scripts/analyze_style_guidance_ab.py --lookback-days 14
    uv run python scripts/analyze_style_guidance_ab.py --niche gaming

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("analyze_style_guidance_ab")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--niche", default=None)
    ap.add_argument("--lookback-days", type=int, default=28)
    return ap.parse_args(argv)


def _analyze_niche(conn, niche_id: str, lookback_days: int) -> dict:
    """Return dict of {n_control, n_treatment, mean_control,
    mean_treatment, lift_pct}. All values None when no data."""
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE (b.extra->>'style_guidance_injected')::boolean IS FALSE)::int AS n_c,
              COUNT(*) FILTER (WHERE (b.extra->>'style_guidance_injected')::boolean IS TRUE)::int AS n_t,
              AVG(pf.reward_48h) FILTER (WHERE (b.extra->>'style_guidance_injected')::boolean IS FALSE)::float AS mean_c,
              AVG(pf.reward_48h) FILTER (WHERE (b.extra->>'style_guidance_injected')::boolean IS TRUE)::float AS mean_t
            FROM pending_feedback pf
            JOIN publishing_analytics pa ON pa.post_id = pf.post_id
            JOIN blueprints b ON b.id = pa.blueprint_id
            WHERE pf.niche_id = %s
              AND pf.reward_48h IS NOT NULL
              AND b.extra ? 'style_guidance_injected'
              AND pf.updated_at >= NOW() - (%s || ' days')::INTERVAL
            """,
            (niche_id, lookback_days),
        ).fetchone()
    except Exception as exc:
        logger.warning("[ab] query failed niche=%s: %s", niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {
            "n_control": 0, "n_treatment": 0,
            "mean_control": None, "mean_treatment": None,
            "lift_pct": None,
        }
    n_c = int(row.get("n_c") if hasattr(row, "get") else row[0] or 0)
    n_t = int(row.get("n_t") if hasattr(row, "get") else row[1] or 0)
    mean_c = row.get("mean_c") if hasattr(row, "get") else row[2]
    mean_t = row.get("mean_t") if hasattr(row, "get") else row[3]
    lift = None
    if (
        mean_c is not None and mean_c > 0
        and mean_t is not None and n_c > 0 and n_t > 0
    ):
        lift = (float(mean_t) - float(mean_c)) / float(mean_c) * 100
    return {
        "n_control": n_c, "n_treatment": n_t,
        "mean_control": float(mean_c) if mean_c is not None else None,
        "mean_treatment": float(mean_t) if mean_t is not None else None,
        "lift_pct": lift,
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

    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    print(
        f"\nStyle-guidance A/B (lookback={args.lookback_days}d, "
        f"roadmap gate: lift >= 15%%)"
    )
    print(f"{'niche':12} {'n_ctrl':>7} {'n_treat':>8} "
          f"{'mean_ctrl':>10} {'mean_treat':>11} {'lift %':>9}")
    print("-" * 70)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            r = _analyze_niche(conn, niche_id, args.lookback_days)
            mc = f"{r['mean_control']:.3f}" if r["mean_control"] is not None else "—"
            mt = f"{r['mean_treatment']:.3f}" if r["mean_treatment"] is not None else "—"
            lp = f"{r['lift_pct']:+.1f}" if r["lift_pct"] is not None else "—"
            print(
                f"{niche_id:12} {r['n_control']:>7} {r['n_treatment']:>8} "
                f"{mc:>10} {mt:>11} {lp:>9}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
