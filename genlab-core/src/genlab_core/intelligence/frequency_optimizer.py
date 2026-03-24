"""Posting frequency optimization — analyzes whether 1/day is optimal.

Examines engagement patterns to recommend posting frequency per niche.
For new channels (< 30 days), recommends higher frequency to build
algorithmic favor. For established channels, analyzes diminishing returns.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def analyze_frequency(niche_id: str) -> dict[str, Any]:
    """Analyze posting frequency and recommend optimal rate.

    Returns:
        {
            "current_rate": float (posts per day),
            "recommended_rate": int (1 or 2),
            "reason": str,
            "days_active": int,
            "avg_reach_per_post": float,
            "total_posts": int,
        }
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return {"current_rate": 1, "recommended_rate": 1, "reason": "No database"}

    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            # Get publishing history
            row = conn.execute(
                "SELECT COUNT(*) as total, "
                "COUNT(DISTINCT published_at::date) as days_active, "
                "MIN(published_at)::date as first_day, "
                "MAX(published_at)::date as last_day "
                "FROM publishing_analytics "
                "WHERE niche_id = %s AND status = 'SUCCESS' AND platform = 'instagram'",
                (niche_id,),
            ).fetchone()

            total_posts = row["total"] if row else 0
            days_active = row["days_active"] if row else 0

            if days_active == 0:
                return {
                    "current_rate": 0,
                    "recommended_rate": 2,
                    "reason": "No posts yet — start with 2/day to build algorithmic momentum",
                    "days_active": 0,
                    "avg_reach_per_post": 0,
                    "total_posts": 0,
                }

            current_rate = round(total_posts / max(days_active, 1), 1)

            # Get average reach
            reach_row = conn.execute(
                "SELECT ROUND(AVG(COALESCE((extra->>'reach')::numeric, 0)))::int as avg_reach "
                "FROM analytics "
                "WHERE niche_id = %s AND niche_id NOT LIKE 'rls_test%%'",
                (niche_id,),
            ).fetchone()

            avg_reach = reach_row["avg_reach"] if reach_row and reach_row["avg_reach"] else 0

            # Recommendation logic
            if days_active < 30:
                # New channel: post more to build algorithmic favor
                recommended = 2
                reason = f"Channel is {days_active} days old — post 2/day to build algorithmic momentum (Instagram favors consistent, frequent posters in the first 30 days)"
            elif avg_reach > 1000:
                # Established with good reach: 1/day is optimal
                recommended = 1
                reason = f"Good reach ({avg_reach}/post avg) — 1/day maintains quality while avoiding audience fatigue"
            else:
                # Low reach: try 2/day to increase discovery
                recommended = 2
                reason = f"Low reach ({avg_reach}/post avg) — increase to 2/day to maximize discovery chances"

            return {
                "current_rate": current_rate,
                "recommended_rate": recommended,
                "reason": reason,
                "days_active": days_active,
                "avg_reach_per_post": avg_reach,
                "total_posts": total_posts,
            }

    except Exception as exc:
        logger.warning("frequency analysis failed for %s: %s", niche_id, exc)
        return {"current_rate": 1, "recommended_rate": 1, "reason": f"Analysis failed: {exc}"}
