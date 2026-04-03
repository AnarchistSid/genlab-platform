"""Runway monitor API — queue depth and days-remaining per niche.

GET /api/v1/runway
    Returns runway_days for each niche channel + status badges.
"""
from __future__ import annotations

import logging

from flask import Blueprint

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)

bp = Blueprint("runway_api", __name__, url_prefix="/api/v1")

NICHE_IDS = ["ai_creators", "gaming", "sports", "movies", "anime"]
RUNWAY_CRITICAL_DAYS = 3
RUNWAY_WARN_DAYS = 7


def _get_client():
    from server.core.graph_sync import get_sync_client
    return get_sync_client()


@bp.route("/runway")
def get_runway_status():
    """Returns runway days for each niche. Powers dashboard warning banners."""
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("BacklogClient init failed: %s", exc)
        return api_error(error="backlog_unavailable", code=502)

    result = {}
    for niche_id in NICHE_IDS:
        try:
            vr = client.blueprints.all(
                formula=f"AND({{status}}='VISUAL_READY', {{niche_id}}='{niche_id}')",
            )
            # Only count approved items as truly publish-eligible
            approved = [
                r for r in vr
                if (r.get("fields", {}).get("action_taken") or "") == "approved"
            ]
            days = len(approved)  # 1 post/day = 1 item/day
            total_vr = len(vr)

            if days <= RUNWAY_CRITICAL_DAYS:
                status = "critical"
            elif days <= RUNWAY_WARN_DAYS:
                status = "low"
            else:
                status = "ok"

            result[niche_id] = {
                "runway_days": days,
                "total_visual_ready": total_vr,
                "approved": len(approved),
                "status": status,
            }
        except Exception as exc:
            logger.warning("Runway check failed for %s: %s", niche_id, exc)
            result[niche_id] = {
                "runway_days": -1,
                "total_visual_ready": -1,
                "approved": -1,
                "status": "error",
            }

    return api_success(data=result)
