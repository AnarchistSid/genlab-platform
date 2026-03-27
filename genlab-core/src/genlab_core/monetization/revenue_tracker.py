"""Affiliate revenue tracking and reporting.

Provides functions to record revenue events and query aggregate revenue
data for the dashboard API.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def record_revenue(
    niche_id: str,
    network: str,
    revenue_amount: float,
    *,
    currency: str = "INR",
    clicks: int = 0,
    conversions: int = 0,
    product_id: str | None = None,
    blueprint_id: str | None = None,
    report_date: date | None = None,
) -> None:
    """Record an affiliate revenue event."""
    import psycopg

    report_date = report_date or date.today()
    try:
        import os
        dsn = os.environ.get("DATABASE_URL", "dbname=genlab")
        conn = psycopg.connect(dsn)
        with conn:
            conn.execute(
                """INSERT INTO affiliate_revenue
                   (niche_id, network, revenue_amount, currency, clicks, conversions,
                    product_id, blueprint_id, date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (niche_id, network, revenue_amount, currency, clicks, conversions,
                 product_id, blueprint_id, report_date),
            )
        conn.close()
    except Exception:
        logger.exception("[revenue] Failed to record revenue event")


def get_revenue_summary(
    window_days: int = 30,
    niche_id: str | None = None,
) -> dict[str, Any]:
    """Get aggregate revenue data for the dashboard."""
    import psycopg
    from psycopg.rows import dict_row

    since = date.today() - timedelta(days=window_days)
    try:
        import os
        dsn = os.environ.get("DATABASE_URL", "dbname=genlab")
        conn = psycopg.connect(dsn, row_factory=dict_row)
        cur = conn.cursor()

        # Set RLS context
        cur.execute("SELECT set_config('app.niche_id', %s, true)",
                    (niche_id or "",))

        # Per-niche totals
        cur.execute("""
            SELECT niche_id,
                   SUM(revenue_amount) as total_revenue,
                   SUM(clicks) as total_clicks,
                   SUM(conversions) as total_conversions,
                   COUNT(*) as records
            FROM affiliate_revenue
            WHERE date >= %s
            GROUP BY niche_id ORDER BY total_revenue DESC
        """, (since,))
        by_niche = cur.fetchall()

        # Per-network totals
        cur.execute("""
            SELECT network,
                   SUM(revenue_amount) as total_revenue,
                   SUM(clicks) as total_clicks,
                   SUM(conversions) as total_conversions
            FROM affiliate_revenue
            WHERE date >= %s
            GROUP BY network ORDER BY total_revenue DESC
        """, (since,))
        by_network = cur.fetchall()

        # Daily trend
        cur.execute("""
            SELECT date, SUM(revenue_amount) as revenue, SUM(clicks) as clicks
            FROM affiliate_revenue
            WHERE date >= %s
            GROUP BY date ORDER BY date
        """, (since,))
        daily = cur.fetchall()

        conn.close()

        return {
            "window_days": window_days,
            "by_niche": [dict(r) for r in by_niche],
            "by_network": [dict(r) for r in by_network],
            "daily_trend": [
                {"date": str(r["date"]), "revenue": float(r["revenue"] or 0),
                 "clicks": int(r["clicks"] or 0)}
                for r in daily
            ],
            "total_revenue": sum(float(r.get("total_revenue") or 0) for r in by_niche),
            "total_clicks": sum(int(r.get("total_clicks") or 0) for r in by_niche),
            "total_conversions": sum(int(r.get("total_conversions") or 0) for r in by_niche),
        }
    except Exception:
        logger.exception("[revenue] Failed to get summary")
        return {
            "window_days": window_days,
            "by_niche": [],
            "by_network": [],
            "daily_trend": [],
            "total_revenue": 0,
            "total_clicks": 0,
            "total_conversions": 0,
        }
