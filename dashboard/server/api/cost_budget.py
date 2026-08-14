"""Phase 2.D — cost budget endpoint.

Reads the current throttle level + today's spend so operator can
see budget state on Mission Control.
"""
from __future__ import annotations

import logging

from flask import Blueprint

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("cost_budget_api", __name__, url_prefix="/api/v1/cost-budget")


@bp.route("/status", methods=["GET"])
def get_cost_budget_status():
    """Return current throttle level + today's LLM spend + thresholds.

    Response shape:

        {
          "status": "ok",
          "data": {
            "spend_today_usd": 0.34,
            "throttle_level": "none",
            "reduce_50_threshold": 5.0,
            "pause_threshold": 10.0,
            "emergency_threshold": 20.0
          }
        }
    """
    try:
        from genlab_core.cost.budget_gate import get_status
        s = get_status()
        return api_success(data={
            "spend_today_usd": round(s.spend_today_usd, 4),
            "throttle_level": s.throttle_level.value,
            "reduce_50_threshold": s.reduce_50_threshold,
            "pause_threshold": s.pause_threshold,
            "emergency_threshold": s.emergency_threshold,
        })
    except Exception as exc:
        logger.warning("[cost_budget] status failed: %s", exc, exc_info=True)
        return api_error(error="Cost budget query failed", code=500)
