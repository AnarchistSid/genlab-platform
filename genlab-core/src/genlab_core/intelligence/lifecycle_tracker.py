"""Content lifecycle tracker — records engagement at each collection window.

Stores per-post engagement snapshots at 6h, 24h, 48h, 168h windows,
enabling analysis of engagement velocity and decay patterns.

This data feeds future predictions: "posts that get X likes in 6h
typically reach Y total reach by 48h."
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def record_lifecycle_snapshot(
    post_id: str,
    platform: str,
    niche_id: str,
    window: str,
    metrics: dict[str, Any],
) -> None:
    """Record an engagement snapshot for a post at a specific collection window.

    Called by metric_collector when it collects metrics at each window.
    Stores to the analytics table with window metadata.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return

    try:
        import psycopg  # noqa: F401 — kept for the except below

        from genlab_core.storage.tenant_context import pg_connect

        # SR-A/C/D Tier-2 migration (2026-06-17).
        with pg_connect(dsn, niche_id=niche_id) as conn:
            conn.execute(
                "INSERT INTO analytics (niche_id, post_id, platform, metric_type, value, collected_at, window, extra) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT DO NOTHING",
                (
                    niche_id,
                    post_id,
                    platform,
                    f"lifecycle_{window}",
                    metrics.get("reach", metrics.get("views", 0)),
                    datetime.now(UTC),
                    window,
                    __import__("json").dumps(
                        {
                            **metrics,
                            "window": window,
                            "snapshot_at": datetime.now(UTC).isoformat(),
                        }
                    ),
                ),
            )
    except Exception as exc:
        logger.debug("lifecycle snapshot failed for %s/%s: %s", post_id, window, exc)


def get_lifecycle_data(post_id: str) -> list[dict[str, Any]]:
    """Get all lifecycle snapshots for a post, ordered by window."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return []

    try:
        import psycopg  # noqa: F401 — kept for the except below
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        # ``get_lifecycle_data`` is a cross-niche read (post_id is the
        # filter, not niche_id) — uses "all" for admin-mode RLS so
        # all niches' rows are visible. The SELECT itself doesn't
        # filter by niche; this is the legitimate cross-tenant query.
        with pg_connect(dsn, niche_id="all", row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT window, value as reach, collected_at, extra "
                "FROM analytics "
                "WHERE post_id = %s AND metric_type LIKE 'lifecycle_%%' "
                "ORDER BY collected_at",
                (post_id,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        # WARNING: silent [] made DB failure look like "no snapshots yet"
        # (a real state for posts <6h old). Downstream analyze_velocity
        # then returns wrong classification → wrong learning signal.
        logger.warning("get_lifecycle_data failed for post_id=%s: %s", post_id, exc)
        return []


def analyze_velocity(post_id: str) -> dict[str, Any] | None:
    """Analyze engagement velocity for a post.

    Returns:
        {"velocity": "viral" | "steady" | "flat" | "bombing",
         "6h_reach": int, "48h_reach": int, "growth_rate": float}
    """
    snapshots = get_lifecycle_data(post_id)
    if len(snapshots) < 2:
        return None

    reaches = {s["window"]: s["reach"] for s in snapshots}
    reach_6h = reaches.get("6h", 0)
    reach_48h = reaches.get("48h", reach_6h)

    if reach_6h == 0:
        return {"velocity": "flat", "6h_reach": 0, "48h_reach": reach_48h, "growth_rate": 0}

    growth_rate = (reach_48h - reach_6h) / reach_6h if reach_6h > 0 else 0

    if growth_rate > 5:
        velocity = "viral"
    elif growth_rate > 1:
        velocity = "steady"
    elif growth_rate > 0:
        velocity = "flat"
    else:
        velocity = "bombing"

    return {
        "velocity": velocity,
        "6h_reach": reach_6h,
        "48h_reach": reach_48h,
        "growth_rate": round(growth_rate, 2),
    }
