"""Revenue summary API — affiliate click analytics.

Routes:
    GET /api/v1/revenue/summary       -- total clicks + breakdowns + estimated revenue
    GET /api/v1/revenue/click-trends  -- daily affiliate click counts (14 days)
"""

import logging
import os

from flask import Blueprint, request
from genlab_core.monetization.affiliate_economics import get_affiliate_economics
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("revenue_api", __name__, url_prefix="/api/v1/revenue")

# Economic assumptions (avg order / commission / conversion rate) live in
# genlab-core/config/affiliate_economics.yaml — shared with the daily
# proxy_revenue_aggregator so the dashboard's real-time estimate and the
# persisted proxy rows in affiliate_revenue match to the rupee.
# Audit ref: R-32.


@bp.route("/summary")
def revenue_summary():
    """Return affiliate click stats and rough revenue estimates."""
    try:
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return api_error(error="DATABASE_URL not configured", code=503)

        from psycopg.rows import dict_row

        with pg_connect(
            dsn, row_factory=dict_row, niche_id=request.args.get("niche_id", "all") or "all"
        ) as conn:
            # Total clicks per window
            clicks_today = conn.execute(
                "SELECT COUNT(*) AS cnt FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '1 day'"
            ).fetchone()["cnt"]

            clicks_7d = conn.execute(
                "SELECT COUNT(*) AS cnt FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '7 days'"
            ).fetchone()["cnt"]

            clicks_30d = conn.execute(
                "SELECT COUNT(*) AS cnt FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days'"
            ).fetchone()["cnt"]

            # Clicks by product (last 30d)
            by_product_rows = conn.execute(
                "SELECT product_id, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "GROUP BY product_id ORDER BY cnt DESC"
            ).fetchall()

            # Clicks by niche (last 30d)
            by_niche_rows = conn.execute(
                "SELECT niche_id, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "GROUP BY niche_id ORDER BY cnt DESC"
            ).fetchall()

            # Clicks by network (last 30d)
            by_network_rows = conn.execute(
                "SELECT network, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "GROUP BY network ORDER BY cnt DESC"
            ).fetchall()

            # Estimated revenue per niche (clicks × conversion × avg_order × commission)
            commission_rows = conn.execute(
                "SELECT niche_id, network, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "GROUP BY niche_id, network"
            ).fetchall()

        by_product = {r["product_id"]: r["cnt"] for r in by_product_rows}
        by_niche = {r["niche_id"]: r["cnt"] for r in by_niche_rows}
        by_network = {r["network"]: r["cnt"] for r in by_network_rows}

        # Commission pct assumptions per network (rough defaults)
        economics = get_affiliate_economics()
        estimated_revenue_inr = 0.0
        for r in commission_rows:
            niche = r["niche_id"] or "unknown"
            network = r["network"] or "unknown"
            cnt = r["cnt"]
            estimated_revenue_inr += economics.estimate_revenue(
                niche_id=niche, network=network, clicks=cnt
            )

        # Actual reported revenue from affiliate_revenue table
        actual_revenue_30d = 0.0
        actual_by_niche = {}
        try:
            rev_rows = conn.execute(
                "SELECT niche_id, SUM(revenue_amount) AS total, SUM(clicks) AS clicks, "
                "SUM(conversions) AS conv FROM affiliate_revenue "
                "WHERE date >= NOW()::date - 30 GROUP BY niche_id"
            ).fetchall()
            for r in rev_rows:
                actual_by_niche[r["niche_id"]] = {
                    "revenue": float(r["total"] or 0),
                    "clicks": int(r["clicks"] or 0),
                    "conversions": int(r["conv"] or 0),
                }
                actual_revenue_30d += float(r["total"] or 0)
        except Exception:
            pass  # table may not exist yet in some environments

        return api_success(
            data={
                "clicks": {
                    "today": clicks_today,
                    "last_7d": clicks_7d,
                    "last_30d": clicks_30d,
                },
                "by_product": by_product,
                "by_niche": by_niche,
                "by_network": by_network,
                "estimated_revenue_inr_30d": round(estimated_revenue_inr, 2),
                "actual_revenue_inr_30d": round(actual_revenue_30d, 2),
                "actual_by_niche": actual_by_niche,
                "note": (
                    "estimated = clicks × 2% conversion × avg_order × commission. "
                    "actual = reported from affiliate networks (when available)."
                ),
            }
        )

    except Exception as e:
        logger.error("[Revenue] Summary query failed: %s", e, exc_info=True)
        return api_error(error="Internal server error", code=500)


@bp.route("/click-trends", methods=["GET"])
def click_trends():
    """Daily affiliate click counts for the last 14 days."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_success(data=[])
    try:
        from psycopg.rows import dict_row

        with pg_connect(
            dsn, row_factory=dict_row, niche_id=request.args.get("niche_id", "all") or "all"
        ) as conn:
            rows = conn.execute(
                "SELECT created_at::date AS day, COUNT(*) AS clicks "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '14 days' "
                "GROUP BY day ORDER BY day"
            ).fetchall()
        return api_success(
            data=[{"date": r["day"].isoformat(), "clicks": r["clicks"]} for r in rows]
        )
    except Exception as exc:
        logger.warning("click-trends failed: %s", exc)
        return api_success(data=[])


@bp.route("/attribution")
def revenue_attribution():
    """Return click attribution by channel and platform."""
    try:
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return api_error(error="DATABASE_URL not configured", code=503)

        from psycopg.rows import dict_row

        with pg_connect(
            dsn, row_factory=dict_row, niche_id=request.args.get("niche_id", "all") or "all"
        ) as conn:
            # Clicks by channel (last 30d)
            by_channel = conn.execute(
                "SELECT channel_id, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "AND channel_id IS NOT NULL AND channel_id != '' "
                "GROUP BY channel_id ORDER BY cnt DESC"
            ).fetchall()

            # Clicks by platform_source (last 30d)
            by_platform = conn.execute(
                "SELECT platform_source, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "AND platform_source IS NOT NULL AND platform_source != '' "
                "GROUP BY platform_source ORDER BY cnt DESC"
            ).fetchall()

            # Top blueprints by clicks (last 30d)
            top_blueprints = conn.execute(
                "SELECT blueprint_id, product_id, niche_id, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "AND blueprint_id IS NOT NULL AND blueprint_id != '' "
                "GROUP BY blueprint_id, product_id, niche_id "
                "ORDER BY cnt DESC LIMIT 10"
            ).fetchall()

            # Daily click trend (last 7 days)
            daily_trend = conn.execute(
                "SELECT DATE(created_at) AS day, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '7 days' "
                "GROUP BY DATE(created_at) ORDER BY day"
            ).fetchall()

        return api_success(
            data={
                "by_channel": {r["channel_id"]: r["cnt"] for r in by_channel},
                "by_platform": {r["platform_source"]: r["cnt"] for r in by_platform},
                "top_blueprints": [
                    {
                        "blueprint_id": r["blueprint_id"],
                        "product": r["product_id"],
                        "niche": r["niche_id"],
                        "clicks": r["cnt"],
                    }
                    for r in top_blueprints
                ],
                "daily_trend": [{"date": str(r["day"]), "clicks": r["cnt"]} for r in daily_trend],
            }
        )

    except Exception as e:
        logger.error("[Revenue] Attribution query failed: %s", e, exc_info=True)
        return api_error(error="Internal server error", code=500)
