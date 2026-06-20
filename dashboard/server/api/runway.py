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


def _classify_runway(days: int) -> str:
    """Map runway days → status badge string used by the dashboard banner."""
    if days <= RUNWAY_CRITICAL_DAYS:
        return "critical"
    if days <= RUNWAY_WARN_DAYS:
        return "low"
    return "ok"


def _empty_runway_result() -> dict[str, int | str]:
    """Per-niche placeholder when a niche has no VISUAL_READY blueprints."""
    return {
        "runway_days": 0,
        "total_visual_ready": 0,
        "approved": 0,
        "status": "critical",  # 0 days = critical
    }


@bp.route("/runway")
def get_runway_status():
    """Returns runway days for each niche. Powers dashboard warning banners.

    PR #396: previously called ``client.blueprints.all(formula=...)`` 5
    times (once per niche). Now fetches ALL VISUAL_READY blueprints ONCE
    and groups by ``niche_id`` in Python. Same final shape; 5× fewer
    BacklogClient round-trips per `/runway` request.
    """
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("BacklogClient init failed: %s", exc)
        return api_error(error="backlog_unavailable", code=502)

    try:
        # ONE fetch of all VISUAL_READY blueprints across all niches.
        # Server-side filtering by status (formula); niche grouping in
        # Python below is O(N) over the result list (typically 50-200
        # rows total across all niches).
        all_vr = client.blueprints.all(formula="{status}='VISUAL_READY'")
    except Exception as exc:
        logger.error("Runway fetch (VISUAL_READY all-niches) failed: %s", exc)
        return api_error(error="runway_fetch_failed", code=502)

    # Pre-initialise every niche so the response shape is stable even
    # when a niche has zero VISUAL_READY blueprints.
    result: dict[str, dict] = {nid: _empty_runway_result() for nid in NICHE_IDS}

    for record in all_vr:
        fields = record.get("fields", {}) if isinstance(record, dict) else {}
        niche_id = (fields.get("niche_id") or "").strip()
        if niche_id not in result:
            # Stray niche_id we don't track — ignore silently (defensive
            # against historical rows with deprecated niche IDs).
            continue
        result[niche_id]["total_visual_ready"] += 1
        if (fields.get("action_taken") or "").strip() == "approved":
            result[niche_id]["approved"] += 1
            result[niche_id]["runway_days"] += 1  # 1 post/day = 1 item/day

    # Classify status badges based on final approved counts.
    for payload in result.values():
        payload["status"] = _classify_runway(payload["runway_days"])

    return api_success(data=result)
