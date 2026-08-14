"""Phase 2.C — SLO forecasts endpoint.

Reads the slo_forecasts table (populated hourly by
run_slo_forecast.py) and returns per-(check_name, niche_id) current
forecast state for the SLOForecastCard on Mission Control.
"""
from __future__ import annotations

import logging
import os

from flask import Blueprint
from genlab_core.storage.tenant_context import pg_connect

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("slo_forecasts_api", __name__, url_prefix="/api/v1/monitoring")


@bp.route("/slo-forecasts", methods=["GET"])
def get_slo_forecasts():
    """Return all current SLO forecasts. Response shape:

        {
          "status": "ok",
          "data": [
            {
              "check_name": "zero_blueprints",
              "niche_id": "gaming",  // empty string = system-wide
              "current_rate": 0.28,
              "forecast_rate": 0.42,
              "trend_pct": 50.0,
              "verdict": "watch",
              "ttb_hours": 48.5,
              "computed_at": "2026-08-14T12:23:00+00:00"
            },
            ...
          ]
        }

    Frontend filters/sorts client-side (small dataset — few dozen
    rows max).
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return api_error(error="DATABASE_URL unset", code=503)

    try:
        with pg_connect(dsn, niche_id="all", connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT check_name, niche_id, current_rate,
                           forecast_rate, trend_pct, verdict,
                           ttb_hours, computed_at
                    FROM slo_forecasts
                    ORDER BY
                      CASE verdict
                        WHEN 'forecast_critical' THEN 0
                        WHEN 'forecast_warning' THEN 1
                        WHEN 'watch' THEN 2
                        ELSE 3
                      END,
                      trend_pct DESC
                    """,
                )
                rows = cur.fetchall() or []
    except Exception as exc:
        logger.warning("[slo_forecasts] query failed: %s", exc, exc_info=True)
        return api_error(error="SLO forecasts query failed", code=500)

    result = []
    for row in rows:
        if hasattr(row, "get"):
            r = dict(row)
        else:
            r = dict(zip(
                ["check_name", "niche_id", "current_rate", "forecast_rate",
                 "trend_pct", "verdict", "ttb_hours", "computed_at"],
                row,
            ))
        result.append({
            "check_name": r["check_name"],
            "niche_id": r["niche_id"] or None,
            "current_rate": float(r["current_rate"]),
            "forecast_rate": float(r["forecast_rate"]),
            "trend_pct": float(r["trend_pct"]),
            "verdict": r["verdict"],
            "ttb_hours": (
                float(r["ttb_hours"]) if r["ttb_hours"] is not None else None
            ),
            "computed_at": (
                r["computed_at"].isoformat()
                if hasattr(r["computed_at"], "isoformat")
                else str(r["computed_at"])
            ),
        })
    return api_success(data=result)
