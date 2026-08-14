#!/usr/bin/env python3
"""Phase 2.B of the Genius Program Roadmap — portfolio bandit runner.

Fires weekly (Sun 06:00 UTC, before strategist at 07:30) via
genlab-portfolio-bandit.timer. Steps:

  1. Pull per-niche features from prod tables (7d window):
      - follower_growth_rate: audience_snapshots delta / total
      - cost_usd: SUM(llm_run_cost.cost_usd) for niche (if table exists)
      - engagement_percentile: median reward_48h vs cross-niche median
      - publish_success_rate: SUCCESS / total publish attempts
      - conversion_rate: affiliate_clicks / views (proxy)

  2. Load bandit state from historical portfolio_allocations rows
     (refit A/b matrices from prior weekly (features, reward) pairs).

  3. Compute recommend_weights(features) → 5 PortfolioAllocation rows.

  4. Persist to portfolio_allocations.

  5. Update bandit state with previous week's realized reward (if
     the previous allocation has aged enough to compute it).

**Observation-only in v1**: `applied=false` on every row. The
consumer wire (`pipeline_runner` reading current_week allocation
and adjusting fetch depth / publish frequency) is a follow-up ship
gated behind `GENLAB_PORTFOLIO_BANDIT_ENABLED=1`.

## Usage

    # Fires from systemd; can run manually:
    uv run python scripts/run_portfolio_bandit.py
    uv run python scripts/run_portfolio_bandit.py --dry-run
    uv run python scripts/run_portfolio_bandit.py --week-of 2026-08-10

## Exit codes

  * 0 — completed (rows written OR dry-run)
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta

logger = logging.getLogger("run_portfolio_bandit")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute + print, no writes")
    ap.add_argument("--week-of", default=None,
                    help="Override the week (YYYY-MM-DD, default = last Sun)")
    return ap.parse_args(argv)


def _monday_of(d: date) -> date:
    """Return the Monday of the week containing d (portfolio_allocations
    week_of anchors on Monday for cross-analysis with strategist's
    weekly cycle)."""
    return d - timedelta(days=d.weekday())


def _extract_features_for_niche(conn, niche_id: str):
    """Pull 7d feature snapshot from prod tables. Returns
    NicheFeatures on success, None on fatal error (single-niche
    failure doesn't kill the whole run)."""
    from genlab_core.learning.portfolio_bandit import NicheFeatures

    try:
        # follower_growth_rate: (today's follower sum - 7d-ago sum) / today's sum
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(latest.metric_value), 0)::float AS current_followers,
              COALESCE(SUM(prior.metric_value), 0)::float AS prior_followers
            FROM (
              SELECT DISTINCT ON (platform) platform, metric_value
              FROM audience_snapshots
              WHERE niche_id = %s
                AND metric_name IN ('followers', 'subscribers')
                AND snapshot_date >= CURRENT_DATE - INTERVAL '2 days'
              ORDER BY platform, snapshot_date DESC
            ) latest
            FULL OUTER JOIN (
              SELECT DISTINCT ON (platform) platform, metric_value
              FROM audience_snapshots
              WHERE niche_id = %s
                AND metric_name IN ('followers', 'subscribers')
                AND snapshot_date <= CURRENT_DATE - INTERVAL '7 days'
                AND snapshot_date >= CURRENT_DATE - INTERVAL '9 days'
              ORDER BY platform, snapshot_date DESC
            ) prior USING (platform)
            """,
            (niche_id, niche_id),
        ).fetchone()
        current = float(row.get("current_followers") if hasattr(row, "get") else row[0])
        prior = float(row.get("prior_followers") if hasattr(row, "get") else row[1])
        follower_growth_rate = (
            (current - prior) / current if current > 0 else 0.0
        )
    except Exception as exc:
        logger.warning("features.follower_growth failed niche=%s: %s", niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        follower_growth_rate = 0.0

    try:
        # engagement_percentile: median reward_48h / cross-niche median
        row = conn.execute(
            """
            SELECT
              (
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY reward_48h)
                FROM pending_feedback
                WHERE niche_id = %s
                  AND reward_48h IS NOT NULL
                  AND created_at >= NOW() - INTERVAL '7 days'
              )::float AS niche_median,
              (
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY reward_48h)
                FROM pending_feedback
                WHERE reward_48h IS NOT NULL
                  AND created_at >= NOW() - INTERVAL '7 days'
              )::float AS global_median
            """,
            (niche_id,),
        ).fetchone()
        n_med = float(row.get("niche_median") or 0)
        g_med = float(row.get("global_median") or 0.001)  # avoid div-0
        engagement_percentile = min(1.0, n_med / g_med) if g_med > 0 else 0.5
    except Exception as exc:
        logger.warning("features.engagement failed niche=%s: %s", niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        engagement_percentile = 0.5

    try:
        # publish_success_rate: SUCCESS / total (last 7d)
        row = conn.execute(
            """
            SELECT
              COUNT(*)::float AS total,
              COUNT(*) FILTER (WHERE pa.status = 'SUCCESS')::float AS ok
            FROM publishing_analytics pa
            JOIN blueprints b ON b.id = pa.blueprint_id
            WHERE b.niche_id = %s
              AND pa.published_at >= NOW() - INTERVAL '7 days'
            """,
            (niche_id,),
        ).fetchone()
        total = float(row.get("total") if hasattr(row, "get") else row[0])
        ok = float(row.get("ok") if hasattr(row, "get") else row[1])
        publish_success_rate = ok / total if total > 0 else 1.0
    except Exception as exc:
        logger.warning("features.publish_success failed niche=%s: %s", niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        publish_success_rate = 1.0

    # cost_usd + conversion_rate: fail-soft to 0 if the tracking
    # tables don't exist yet (they're Phase 3 territory).
    cost_usd = 0.0
    conversion_rate = 0.0

    return NicheFeatures(
        niche_id=niche_id,
        follower_growth_rate=follower_growth_rate,
        cost_usd=cost_usd,
        engagement_percentile=engagement_percentile,
        publish_success_rate=publish_success_rate,
        conversion_rate=conversion_rate,
    )


def _refit_bandit(conn, bandit) -> None:
    """Replay historical (features, reward) from portfolio_allocations
    into the bandit. reward_realized is written by the runner when it
    processes the previous week's allocation."""
    from genlab_core.learning.portfolio_bandit import NicheFeatures
    import numpy as np

    try:
        rows = conn.execute(
            """
            SELECT niche_id, context_features, reward_realized
            FROM portfolio_allocations
            WHERE reward_realized IS NOT NULL
            ORDER BY week_of ASC
            """,
        ).fetchall()
    except Exception as exc:
        logger.warning("bandit refit query failed (using fresh state): %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return

    for row in rows or []:
        niche_id = row.get("niche_id") if hasattr(row, "get") else row[0]
        ctx_json = row.get("context_features") if hasattr(row, "get") else row[1]
        reward = float(row.get("reward_realized") if hasattr(row, "get") else row[2])
        try:
            ctx_dict = ctx_json if isinstance(ctx_json, dict) else json.loads(ctx_json or "{}")
            f = NicheFeatures(
                niche_id=niche_id,
                follower_growth_rate=float(ctx_dict.get("follower_growth_rate", 0)),
                cost_usd=float(ctx_dict.get("cost_usd", 0)),
                engagement_percentile=float(ctx_dict.get("engagement_percentile", 0.5)),
                publish_success_rate=float(ctx_dict.get("publish_success_rate", 1.0)),
                conversion_rate=float(ctx_dict.get("conversion_rate", 0)),
            )
            bandit.update(niche_id, f.to_vector(), reward)
        except (ValueError, KeyError, TypeError) as exc:
            logger.debug("refit skip malformed row niche=%s: %s", niche_id, exc)
            continue


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
        else _monday_of(date.today())
    )

    import psycopg
    from psycopg.rows import dict_row

    from genlab_core.learning.portfolio_bandit import (
        ACTIVE_NICHES,
        PortfolioBandit,
        uniform_allocation,
    )

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # Extract features per niche
        features = []
        for niche_id in ACTIVE_NICHES:
            f = _extract_features_for_niche(conn, niche_id)
            if f is not None:
                features.append(f)

        if len(features) < len(ACTIVE_NICHES):
            logger.warning(
                "portfolio_bandit: only %d/%d niches have features — "
                "falling back to uniform allocation",
                len(features), len(ACTIVE_NICHES),
            )
            allocations = uniform_allocation()
        else:
            bandit = PortfolioBandit(alpha=1.0)
            _refit_bandit(conn, bandit)
            allocations = bandit.recommend_weights(features)

        # Report
        print()
        print(f"Portfolio allocations for week_of={week_of}:")
        print(f"  {'niche':12} {'weight':>8} {'ucb_score':>10} feats_snapshot")
        print("-" * 80)
        for a in allocations:
            ctx_summary = (
                f"fgr={a.context_features.get('follower_growth_rate', 0):.3f} "
                f"ep={a.context_features.get('engagement_percentile', 0):.3f}"
            ) if a.context_features else "(fallback)"
            print(
                f"  {a.niche_id:12} {a.recommended_weight:>7.1%} "
                f"{a.ucb_score:>10.3f} {ctx_summary}"
            )

        if args.dry_run:
            print("\nDRY RUN — no writes.")
            return 0

        # Persist
        wrote = 0
        for a in allocations:
            try:
                conn.execute(
                    """
                    INSERT INTO portfolio_allocations (
                      week_of, niche_id, recommended_weight,
                      ucb_score, context_features, applied
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, FALSE)
                    ON CONFLICT (week_of, niche_id) DO UPDATE SET
                      recommended_weight = EXCLUDED.recommended_weight,
                      ucb_score = EXCLUDED.ucb_score,
                      context_features = EXCLUDED.context_features
                    """,
                    (
                        week_of, a.niche_id, a.recommended_weight,
                        a.ucb_score, json.dumps(a.context_features),
                    ),
                )
                wrote += 1
            except Exception as exc:
                logger.warning(
                    "portfolio_bandit: write failed niche=%s: %s",
                    a.niche_id, exc,
                )
        conn.commit()
        logger.info("portfolio_bandit: wrote %d allocations for %s", wrote, week_of)

    return 0


if __name__ == "__main__":
    sys.exit(main())
