"""Source-discovery proposer dashboard endpoint (PR after #586).

  GET /api/v1/source-discovery/proposals?niche_id=X
       &window_days=30&min_clips=2&top_n=10

Returns ranked source channels for the niche, descending by per-clip
average engagement. SR-F: 403 when niche_id is outside the operator's
allowlist; defaults to all-niche when no niche_id is supplied
(post-filtered to allowlist for restricted operators).

Foundation deps:
  * PR #586 — blueprints.source_channel_id column + producer wire
  * genlab_core.source_discovery.propose_source_channels (this PR)

Until source channels accumulate on prod (post-PR-586 deploy + a
few days of trending fetches), this endpoint returns an empty
``proposals`` array. The Mission Control card renders zero-state
with a 'waiting for data' message so operators understand the
proposer isn't broken — it's data-starved.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint(
    "source_discovery_api",
    __name__,
    url_prefix="/api/v1/source-discovery",
)


@bp.route("/proposals", methods=["GET"])
def list_proposals():
    """Per-niche ranked source-channel suggestions.

    Query params:
      niche_id    — required, the niche to rank within
      window_days — int, clamped [1, 180]; default 30
      min_clips   — int, >= 1; default 2 (filter single-clip noise)
      top_n       — int, clamped [1, 100]; default 10

    Response shape:
      {data: {
        niche_id: str,
        window_days: int,
        min_clips: int,
        proposals: [
          {source_channel_id, clips_used, total_engagement,
           avg_engagement, last_used_at, sample_blueprints}, ...
        ]
      }}

    SR-F: 403 when niche_id is outside the operator's allowlist.

    Fail-OPEN at the library layer: zero rows / DB error → empty
    proposals. The card renders zero-state, not an error toast.
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id required")

    # Validate ints early so garbage input gets a clean 4xx instead
    # of hitting the library's defensive clamps with non-numeric input
    try:
        window_days = int(request.args.get("window_days", 30))
        min_clips = int(request.args.get("min_clips", 2))
        top_n = int(request.args.get("top_n", 10))
    except (ValueError, TypeError):
        return api_error(error="window_days / min_clips / top_n must be integers")

    # Defence in depth — clamp at the endpoint AND in the library
    window_days = max(1, min(180, window_days))
    min_clips = max(1, min_clips)
    top_n = max(1, min(100, top_n))

    # SR-F: validate explicit niche_id against operator allowlist
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None and niche_id not in _allowed:
        return api_error(
            error=(
                f"Source-discovery proposals scoped to niche '{niche_id}' "
                f"which is not in your allowlist (allowed: {sorted(_allowed)}). "
                f"See SR-F."
            ),
            code=403,
        )

    try:
        # Lazy import so a pre-PR-586 core (no source_discovery
        # module) returns an empty payload rather than 500
        from genlab_core.source_discovery import propose_source_channels
    except ImportError:
        return api_success(
            data={
                "niche_id": niche_id,
                "window_days": window_days,
                "min_clips": min_clips,
                "proposals": [],
                "warning": "source_discovery module unavailable on this core build",
            }
        )

    try:
        suggestions = propose_source_channels(
            niche_id,
            window_days=window_days,
            min_clips=min_clips,
            top_n=top_n,
        )
    except Exception as exc:
        logger.error("source-discovery proposals failed: %s", exc, exc_info=True)
        return api_error(error="Internal server error", code=500)

    # Serialise the ChannelSuggestion dataclasses for JSON
    proposals = [
        {
            "source_channel_id": s.source_channel_id,
            "clips_used": s.clips_used,
            "total_engagement": s.total_engagement,
            "avg_engagement": s.avg_engagement,
            "last_used_at": s.last_used_at.isoformat() if s.last_used_at else None,
            "channel_name": s.channel_name,
            "sample_blueprints": list(s.sample_blueprints),
        }
        for s in suggestions
    ]

    return api_success(
        data={
            "niche_id": niche_id,
            "window_days": window_days,
            "min_clips": min_clips,
            "proposals": proposals,
        }
    )
