"""Per-platform bandit-divergence API endpoint (PR AG, 2026-06-29).

  GET /api/v1/learning/bandit-platform-divergence?niche_id=<niche>&min_n_plays=5
      Per-(niche, base_arm) row showing base posterior vs per-platform
      posteriors + the deltas. Powers the Mission Control
      ``BanditPlatformDivergenceCard``. Surfaces whether PR #641's
      per-platform bandit split is producing meaningful divergence vs
      collapsing back to the base arm.

The data source is :func:`server.core.bandit_platform_divergence_pg.fetch_per_platform_divergence`,
which reads ``bandit_arms`` and groups platform-variants under their base
arm via :func:`genlab_core.learning.bandit_platform_split.parse_platform_arm_id`.

## Why a dedicated endpoint, not merged into /learning/*

  * Shape divergence — the existing learning endpoints (hook classifier
    status, drift detector) are per-niche scalars, not (niche, base_arm)
    rows. The matrix shape doesn't compose into the scalar shape.
  * Keeps ``/learning/*`` endpoints' single-purpose contract intact while
    this endpoint owns the divergence card end-to-end.

## SR-F (niche allowlist)

  Restricted operators see only their allowlisted niches' rows. The
  ``niche_id=`` query param is a DISPLAY filter, not a security
  boundary — even if a restricted operator sends ``niche_id=sports``,
  the SR-F filter still applies. (Belt + suspenders: also wins when
  the operator's allowlist excludes 'sports' — they'll get an empty
  payload rather than the rows they tried to request.)
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("bandit_platform_divergence_api", __name__, url_prefix="/api/v1/learning")


# Closed set — matches the 5 niches in the niches registry. Used to
# 400 unknown niche_ids early (vs returning an empty list which would
# confuse the operator into thinking their niche has no divergence).
# Same shape as the bandit_hour_posteriors._VALID_NICHE_IDS.
_VALID_NICHE_IDS = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})


@bp.route("/bandit-platform-divergence", methods=["GET"])
def bandit_platform_divergence():
    """Per-(niche, base_arm) per-platform divergence rows.

    Query params:
      niche_id (optional) — filter to one niche. Default: all niches.
      min_n_plays (optional) — cold-start floor. Default: 5. Clamped [1, 1000].

    Response shape::

      {
        "data": {
          "rows": [
            {niche_id, base_arm_id,
             base_mean, base_n_plays,
             instagram_mean, instagram_n_plays,
             youtube_mean, youtube_n_plays,
             ig_yt_delta, ig_delta_from_base, yt_delta_from_base}, ...
          ],
          "platforms": ["instagram", "youtube"],
          "thresholds": {"meaningful_delta_pct": 5.0, "cold_start_below_n": 5},
          "window": "all_time"
        }
      }

    400 on invalid min_n_plays. 404 on unknown niche_id (when supplied).
    Fail-OPEN: returns ``{rows: [], ...}`` on DB error so the card
    renders its zero-state instead of vanishing.
    """
    niche_id = (request.args.get("niche_id") or "").strip() or None
    if niche_id is not None and niche_id not in _VALID_NICHE_IDS:
        return api_error(
            error=f"Unknown niche_id: {niche_id}. Valid: {sorted(_VALID_NICHE_IDS)}",
            code=404,
        )

    try:
        min_n_plays = int(request.args.get("min_n_plays", 5))
    except (ValueError, TypeError):
        return api_error(error="Invalid min_n_plays parameter")

    # Defence in depth — library also clamps but reject garbage early.
    min_n_plays = max(1, min(1000, min_n_plays))

    try:
        from server.core.bandit_platform_divergence_pg import (
            THRESHOLDS,
            fetch_per_platform_divergence,
        )

        rows = fetch_per_platform_divergence(niche_id=niche_id, min_n_plays=min_n_plays)
    except ImportError:
        # Graceful degrade — return empty-but-shaped payload so the
        # frontend can render zero-state rather than 500.
        return api_success(
            data={
                "rows": [],
                "platforms": ["instagram", "youtube"],
                "thresholds": {
                    "meaningful_delta_pct": 5.0,
                    "cold_start_below_n": 5,
                },
                "window": "all_time",
            }
        )
    except Exception as exc:
        logger.error("bandit-platform-divergence endpoint error: %s", exc, exc_info=True)
        return api_error(error="Internal server error", code=500)

    # SR-F: post-filter rows to operator's allowlist. Restricted
    # operators should not see other niches' bandit posteriors.
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None:
        rows = [r for r in rows if r.get("niche_id") in _allowed]

    # Pull the canonical platform list from the library helper so the
    # response shape stays in lock-step with parse_platform_arm_id.
    try:
        from genlab_core.learning.bandit_platform_split import list_split_platforms

        platforms = list(list_split_platforms())
    except ImportError:
        platforms = ["instagram", "youtube"]

    return api_success(
        data={
            "rows": rows,
            "platforms": platforms,
            "thresholds": THRESHOLDS,
            "window": "all_time",
        }
    )
