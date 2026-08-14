#!/usr/bin/env python3
"""Phase 4.E session 3 — pool vs trending reward analyzer.

Compares reward_48h between blueprints originating from
content_ideas_pool (via origin='ideation_pool' in extra) and
blueprints from trending-video source (all others).

## Roadmap success criteria

  * 20%+ of published content originates from ideation pool by
    month 3
  * Ideation-pool content reward matches or beats trending-video
    reward

This script prints both signals so the operator can eyeball
progression toward the criteria weekly.

## Usage

    uv run python scripts/analyze_ideation_pool_reward.py
    uv run python scripts/analyze_ideation_pool_reward.py --lookback-days 30

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger("analyze_ideation_pool_reward")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--niche", default=None)
    ap.add_argument("--lookback-days", type=int, default=30)
    return ap.parse_args(argv)


def _analyze_niche(conn, niche_id: str, lookback_days: int) -> dict:
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE (b.extra->>'origin') = 'ideation_pool'
              )::int AS n_pool,
              COUNT(*) FILTER (
                WHERE (b.extra->>'origin') IS DISTINCT FROM 'ideation_pool'
              )::int AS n_trending,
              AVG(pf.reward_48h) FILTER (
                WHERE (b.extra->>'origin') = 'ideation_pool'
              )::float AS mean_pool,
              AVG(pf.reward_48h) FILTER (
                WHERE (b.extra->>'origin') IS DISTINCT FROM 'ideation_pool'
              )::float AS mean_trending
            FROM pending_feedback pf
            JOIN publishing_analytics pa ON pa.post_id = pf.post_id
            JOIN blueprints b ON b.id = pa.blueprint_id
            WHERE pf.niche_id = %s
              AND pf.reward_48h IS NOT NULL
              AND pf.updated_at >= NOW() - (%s || ' days')::INTERVAL
            """,
            (niche_id, lookback_days),
        ).fetchone()
    except Exception as exc:
        logger.warning("[analyzer] query failed niche=%s: %s", niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {
            "n_pool": 0, "n_trending": 0,
            "mean_pool": None, "mean_trending": None,
            "pool_share_pct": None, "lift_pct": None,
        }
    n_pool = int(row.get("n_pool") if hasattr(row, "get") else row[0] or 0)
    n_trending = int(row.get("n_trending") if hasattr(row, "get") else row[1] or 0)
    mean_pool = row.get("mean_pool") if hasattr(row, "get") else row[2]
    mean_trending = row.get("mean_trending") if hasattr(row, "get") else row[3]
    total = n_pool + n_trending
    pool_share = (n_pool / total * 100) if total > 0 else None
    lift = None
    if (
        mean_trending is not None and mean_trending > 0
        and mean_pool is not None and n_pool > 0 and n_trending > 0
    ):
        lift = (float(mean_pool) - float(mean_trending)) / float(mean_trending) * 100
    return {
        "n_pool": n_pool,
        "n_trending": n_trending,
        "mean_pool": float(mean_pool) if mean_pool is not None else None,
        "mean_trending": float(mean_trending) if mean_trending is not None else None,
        "pool_share_pct": pool_share,
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
        f"\nIdeation-pool vs trending reward (lookback={args.lookback_days}d)"
    )
    print(
        "roadmap gate 1: pool_share >= 20%%  |  roadmap gate 2: lift >= 0%%"
    )
    print(f"{'niche':12} {'n_pool':>7} {'n_trend':>8} "
          f"{'mean_pool':>10} {'mean_trend':>11} {'share %':>9} {'lift %':>8}")
    print("-" * 80)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for niche_id in niches:
            r = _analyze_niche(conn, niche_id, args.lookback_days)
            mp = f"{r['mean_pool']:.3f}" if r["mean_pool"] is not None else "—"
            mt = f"{r['mean_trending']:.3f}" if r["mean_trending"] is not None else "—"
            sh = f"{r['pool_share_pct']:.1f}" if r["pool_share_pct"] is not None else "—"
            lp = f"{r['lift_pct']:+.1f}" if r["lift_pct"] is not None else "—"
            print(
                f"{niche_id:12} {r['n_pool']:>7} {r['n_trending']:>8} "
                f"{mp:>10} {mt:>11} {sh:>9} {lp:>8}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
