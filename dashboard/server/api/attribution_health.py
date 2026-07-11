"""Layer 5 attribution-health API endpoint (PR #Layer5, 2026-07-11).

Post-Markanimation observability endpoint. Answers "is attribution
present on our recent publishes?" per niche over a rolling window.

  GET /api/v1/attribution-health/stats?window_hours=24

Recognises 3 attribution signals per blueprint:

  1. ``source_channel_id`` populated (proposer-key: PR #762 persist)
  2. Any caption/description field contains ``"\U0001f3ac Original:"``
     (writer wire + payload_builder backstop marker)
  3. Any caption/description field contains ``"Footage:"``
     (YouTube description path)

A blueprint counts as "with_attribution" if ANY of the 3 signals fires.
Semantically equivalent to Layer 4's validate_caption_has_attribution
contract, but computed from persisted blueprint fields (not the live
platform APIs) so operators can see the funnel status regardless of
whether the publish attempt has run yet.

Ships observation-only. No alerts wired up in this PR — that's a
follow-up gated on the endpoint proving out in prod.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint(
    "attribution_health_api",
    __name__,
    url_prefix="/api/v1/attribution-health",
)


_MIN_WINDOW = 1
_MAX_WINDOW = 168  # 1 week — matches fb_survival_check upper bound

# Attribution health thresholds. Post-2026-07-11 audit tightening:
# raised from 95/90 → 100/99. Any single miss is a real audience-
# facing failure — we already retroactively fix them by hand, so
# the operator needs to see the miss BEFORE they'd otherwise notice.
# Old thresholds tolerated 1 in 20 uncredited before turning amber
# which defeats the purpose.
_HEALTHY_PCT = 100.0
_CAUTION_PCT = 99.0


@bp.route("/stats", methods=["GET"])
def stats():
    """Attribution-health stats per niche over a rolling window.

    Query params:
      window_hours — int, clamped [1, 168]. Default 24.

    Response shape::

        {"data": {
          "window_hours": 24,
          "generated_at": "2026-07-11T14:32:00Z",
          "threshold_healthy_pct": 95.0,
          "threshold_caution_pct": 90.0,
          "niches": [
            {"niche_id": "ai_creators", "total_published": 12,
             "with_attribution": 11, "attribution_pct": 91.7,
             "status": "caution"}, ...
          ],
          "overall": {"total_published": 60, "with_attribution": 57,
                      "attribution_pct": 95.0, "status": "healthy"}
        }}

    Fail-open: DB errors return zero rows, not 500. The card renders
    its zero-state rather than vanishing.
    """
    try:
        window_hours = int(request.args.get("window_hours", 24))
    except (TypeError, ValueError):
        return api_error("window_hours must be an integer", 400)

    window_hours = max(_MIN_WINDOW, min(_MAX_WINDOW, window_hours))

    from datetime import UTC, datetime

    from server.core.attribution_health import compute_stats

    try:
        niches, overall = compute_stats(window_hours=window_hours)
    except Exception as exc:  # noqa: BLE001 — fail-open per module docstring
        logger.warning("attribution_health stats compute failed: %s", exc)
        niches, overall = (
            [],
            {
                "total_published": 0,
                "with_attribution": 0,
                "attribution_pct": 0.0,
                "status": "unknown",
            },
        )

    return api_success(
        {
            "window_hours": window_hours,
            "generated_at": datetime.now(UTC).isoformat(),
            "threshold_healthy_pct": _HEALTHY_PCT,
            "threshold_caution_pct": _CAUTION_PCT,
            "niches": niches,
            "overall": overall,
        }
    )
