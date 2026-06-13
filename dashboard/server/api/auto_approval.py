"""Auto-approval gate stats endpoints.

AUTO #1b (2026-06-13): Surfaces the per-niche confusion matrix for the
AutoApprovalGate. Read by the dashboard to show:

- "would auto-approve · 78%" badge per blueprint (via blueprints API)
- agreement-rate card on Mission Control (this module)

Operators consult this before flipping AUTO #2's opt-in flag. Once a
niche shows ≥30 samples + ≥90% agreement, enforcement becomes
defensible.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("auto_approval_api", __name__, url_prefix="/api/v1/auto-approval")

# Niche IDs the dashboard supports. Whitelisted to prevent SQL injection
# via the niche_id param (defense-in-depth — calibration_logger.stats also
# parameterizes). If a future niche is added, append here.
_VALID_NICHES = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})


@bp.route("/calibration-stats", methods=["GET"])
def calibration_stats():
    """Return the per-niche agreement rate for a rolling window.

    Query params:
        niche_id (required): one of ai_creators, gaming, sports, movies, anime
        window_days (optional, default 7): rolling window size

    Response:
        {
          "niche_id": "gaming",
          "window_days": 7,
          "sample_count": 18,
          "agreement_count": 16,
          "agreement_rate": 0.889,
          "confusion_matrix": {
            "true_positives": 12,
            "true_negatives": 4,
            "false_positives": 1,
            "false_negatives": 1
          },
          "ready_for_enforcement": false  // <30 samples
        }
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id query param required", code=400)
    if niche_id not in _VALID_NICHES:
        return api_error(
            error=f"niche_id must be one of {sorted(_VALID_NICHES)}",
            code=400,
        )

    try:
        window_days = int(request.args.get("window_days", "7"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    try:
        from genlab_core.scheduling.calibration_logger import stats

        s = stats(niche_id=niche_id, window_days=window_days)
    except Exception as exc:
        logger.exception("calibration-stats failed for %s", niche_id)
        return api_error(error=f"Stats query failed: {exc}", code=500)

    return api_success(
        data={
            "niche_id": s.niche_id,
            "window_days": window_days,
            "sample_count": s.sample_count,
            "agreement_count": s.agreement_count,
            "agreement_rate": round(s.agreement_rate, 3),
            "confusion_matrix": {
                "true_positives": s.true_positives,
                "true_negatives": s.true_negatives,
                "false_positives": s.false_positives,
                "false_negatives": s.false_negatives,
            },
            "ready_for_enforcement": s.ready_for_enforcement,
        }
    )
