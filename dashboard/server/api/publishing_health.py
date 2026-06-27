"""Publishing-health API endpoints (PR B, 2026-06-27).

  GET /api/v1/publishing/per-niche-platform-health?days=14
      Per-(niche, platform) success-rate matrix for the Mission Control
      PerPlatformHealthCard. Surfaces the fine-grained signal that
      ``/api/v1/metrics/publishing`` averages away. SR-F: post-filter
      to the operator's niche allowlist.

The complementary global health view (per-platform aggregates + trend)
remains at ``/api/v1/metrics/publishing``; this endpoint is matrix-
shaped on purpose for the card's row-per-niche / col-per-platform layout.

## Why a new endpoint, not /metrics/publishing?

  * Shape divergence — matrix vs. flat-trend (see core module docstring).
  * Existing endpoint already returns 6+ orthogonal payload sections;
    appending a 7th would push the cross-cutting JSON over the cliff
    of "no one knows what this endpoint actually does."
  * Lets ``/metrics/publishing`` keep its single-purpose contract while
    this endpoint owns the one card's data needs end-to-end.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("publishing_health_api", __name__, url_prefix="/api/v1/publishing")


@bp.route("/per-niche-platform-health", methods=["GET"])
def per_niche_platform_health():
    """Per-(niche, platform) success-rate matrix.

    Query params:
      days — int, clamped [1, 90]. Default 14.

    Response shape:
      {data: {
        rows: [
          {niche_id, platform, success_count, failed_count,
           success_rate_pct, last_success_at, last_failure_at,
           last_failure_error}, ...
        ],
        thresholds: {red_below_pct, yellow_below_pct},
        window_days: int,
      }}

    SR-F: rows from niches outside the operator's allowlist are
    filtered out before serialisation. Restricted operators see only
    their niches' rows; admins see all.

    Fail-OPEN: library returns ``[]`` on DB error; this endpoint
    returns ``{rows: [], ...}`` rather than 500 so the card renders
    its zero-state ("Insufficient data" everywhere) instead of
    vanishing.
    """
    try:
        days = int(request.args.get("days", 14))
    except (ValueError, TypeError):
        return api_error(error="Invalid days parameter")

    # Defence in depth — library also clamps but reject garbage early.
    days = max(1, min(90, days))

    try:
        from server.core.publishing_health_per_niche import (
            THRESHOLDS,
            fetch_per_niche_platform_health,
        )

        rows = fetch_per_niche_platform_health(days=days)
    except ImportError:
        # Graceful degrade — return empty-but-shaped payload so the
        # frontend can render zero-state rather than 500.
        return api_success(
            data={
                "rows": [],
                "thresholds": {"red_below_pct": 50, "yellow_below_pct": 80},
                "window_days": days,
            }
        )
    except Exception as exc:
        logger.error("per-niche-platform-health endpoint error: %s", exc, exc_info=True)
        return api_error(error="Internal server error", code=500)

    # SR-F: post-filter rows to operator's allowlist. Restricted
    # operators should not see other niches' publish data.
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None:
        rows = [r for r in rows if r.get("niche_id") in _allowed]

    return api_success(
        data={
            "rows": rows,
            "thresholds": THRESHOLDS,
            "window_days": days,
        }
    )
