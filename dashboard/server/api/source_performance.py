"""Source-performance dashboard endpoint (Phase 2, L1, 2026-06-30).

Surfaces the per-source bandit arms data shipped in PR #571 + activated
in PR #571b (commit fa317889). The data is already accumulating in the
``bandit_arms`` table under ``arm_id LIKE 'source:%'`` — without this
endpoint the operator cannot answer "does YouTube showcase outperform
news?" without a manual SQL query.

  GET /api/v1/source-performance?niche_id=ai_creators&top_n=20

Returns top arms by reward_mean (Beta posterior alpha / (alpha+beta))
with confidence intervals so the operator can see which sources are
genuinely better vs which just had a lucky single play.

## Why a NEW endpoint, not reuse source-discovery proposals

Different signal:

  * source-discovery proposals → ranks SOURCE YouTube CHANNELS (the
    channels we pulled clips FROM) by per-clip engagement. Useful for
    "which channels should we add to config/sources.yaml?"
  * source-performance → ranks the source NAMES the bandit uses
    (e.g. ``reddit:aivideo``, ``youtube_trending``) by reward mean.
    Useful for "should we down-weight youtube_trending in favor of
    youtube_search?"

The bandit arms are written by ``record_source_outcome`` in
``genlab_core.learning.source_performance``. This endpoint is a
read-only window into those arms.

## Confidence intervals

Beta(α, β) Wilson-style 95% CI is computationally expensive; we use
the closed-form Beta distribution std dev: ``sqrt(αβ / ((α+β)²(α+β+1)))``
and report ``mean ± 1.96 × std`` clamped to [0, 1]. Good enough for
operator UI; not a publication-grade interval.

## SR-F enforcement

niche_id required; 403 when outside operator allowlist; same shape as
other niche-scoped endpoints (source_discovery, scheduling_pauses).

## Cold-start tolerance

Arms with n_plays < 10 surface in the response but the frontend
renders them in a separate "insufficient data" section so the operator
doesn't act on noise.
"""

from __future__ import annotations

import logging
import math

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint(
    "source_performance_api",
    __name__,
    url_prefix="/api/v1/source-performance",
)

_VALID_NICHE_IDS = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})


def _beta_std(alpha: float, beta: float) -> float:
    """Closed-form Beta distribution standard deviation.

    σ² = αβ / ((α+β)² × (α+β+1))

    Returns 0.0 when α+β <= 1 (degenerate; cold-start single observation
    has no meaningful spread to report).
    """
    s = alpha + beta
    if s <= 1.0:
        return 0.0
    num = alpha * beta
    den = (s * s) * (s + 1.0)
    if den <= 0.0:
        return 0.0
    return math.sqrt(num / den)


def _row_to_dict(perf) -> dict:
    """Serialise a SourcePerformance dataclass to JSON with CI bounds.

    The ``arm_id`` field carries the per-platform suffix when present
    (e.g. ``source:youtube_trending__instagram``) so consumers can
    distinguish aggregated vs per-platform arms without re-deriving.
    The ``platform`` field is None for aggregated arms (legacy PR #571
    rows) and the platform name for per-platform arms.
    """
    std = _beta_std(perf.alpha, perf.beta)
    # 1.96σ ≈ 95% normal-approx CI. Clamp to [0, 1] because Beta has
    # support on [0, 1] — a left tail going negative is a normal-approx
    # artifact that confuses operators.
    ci_lower = max(0.0, perf.reward_mean - 1.96 * std)
    ci_upper = min(1.0, perf.reward_mean + 1.96 * std)
    platform = getattr(perf, "platform", None)
    arm_id = f"source:{perf.source}__{platform}" if platform else f"source:{perf.source}"
    return {
        "arm_id": arm_id,
        "source": perf.source,
        "platform": platform,
        "niche_id": perf.niche_id,
        "n_plays": int(perf.n_plays),
        "alpha": round(float(perf.alpha), 4),
        "beta": round(float(perf.beta), 4),
        "reward_mean": round(float(perf.reward_mean), 4),
        "reward_ci_lower": round(ci_lower, 4),
        "reward_ci_upper": round(ci_upper, 4),
    }


# Valid platforms for the ?platform= filter — mirrors the active
# publishing surface (5 platforms). Strict allowlist so a typo'd
# ?platform=instagra returns 400 instead of silently returning empty
# results (which is what an unknown platform would do via the LIKE
# match in the library layer).
_VALID_PLATFORMS = frozenset({"facebook", "instagram", "youtube", "threads", "twitter", "tiktok"})


@bp.route("", methods=["GET"])
def list_source_performance():
    """Per-niche per-source bandit reward summary.

    Query params:
      niche_id (required) — must be one of the 5 valid niche IDs
      top_n (optional, default 20, clamped [1, 100])
      platform (optional, 2026-06-30) — when present, returns ONLY the
        per-platform source arms (``source:<source>__<platform>``) for
        that platform. When absent, returns ALL source arms (legacy
        PR #571 behaviour). Must be one of: facebook, instagram,
        youtube, threads, twitter, tiktok.

    Response shape::

        {
          "data": {
            "niche_id": "ai_creators",
            "top_n": 20,
            "platform": "instagram" | null,
            "sources": [
              {
                "arm_id": "source:reddit:aivideo__instagram",
                "source": "reddit:aivideo",
                "platform": "instagram" | null,
                "niche_id": "ai_creators",
                "n_plays": 42,
                "alpha": 12.3,
                "beta": 31.7,
                "reward_mean": 0.2795,
                "reward_ci_lower": 0.1456,
                "reward_ci_upper": 0.4134
              },
              ...
            ]
          }
        }

    400 when niche_id missing/invalid OR platform invalid. 403 when
    niche outside operator allowlist. Fail-OPEN at the library layer:
    DB error → empty list + success response (the card renders
    zero-state, not an error toast).
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id required")
    if niche_id not in _VALID_NICHE_IDS:
        return api_error(
            error=f"Unknown niche: {niche_id}. Valid: {sorted(_VALID_NICHE_IDS)}",
            code=400,
        )

    try:
        top_n = int(request.args.get("top_n", 20))
    except (ValueError, TypeError):
        return api_error(error="top_n must be an integer")
    top_n = max(1, min(100, top_n))

    # Optional platform filter — empty string treated as "no filter"
    # so the dashboard can pass-through ``?platform=`` without
    # special-casing the empty case.
    platform = (request.args.get("platform") or "").strip() or None
    if platform is not None and platform not in _VALID_PLATFORMS:
        return api_error(
            error=(f"Unknown platform: {platform}. Valid: {sorted(_VALID_PLATFORMS)}"),
            code=400,
        )

    # SR-F allowlist enforcement
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None and niche_id not in _allowed:
        return api_error(
            error=(
                f"Source-performance for niche '{niche_id}' "
                f"is not in your allowlist (allowed: {sorted(_allowed)}). "
                f"See SR-F."
            ),
            code=403,
        )

    # Lazy import so a pre-PR-571 core (no source_performance module)
    # returns empty payload rather than 500
    try:
        from genlab_core.learning.source_performance import list_source_performance as _list
    except ImportError:
        return api_success(
            data={
                "niche_id": niche_id,
                "top_n": top_n,
                "platform": platform,
                "sources": [],
                "warning": "source_performance module unavailable on this core build",
            }
        )

    try:
        # Library accepts platform kwarg as of 2026-06-30 (PR ships
        # alongside this endpoint update). Call by keyword so a
        # rollback that reverts the library signature surfaces as a
        # TypeError caught in the try-block, not as a silently-wrong
        # positional arg.
        perfs = _list(niche_id, platform=platform)
    except TypeError:
        # Pre-PR core that doesn't yet support the platform kwarg —
        # fall back to the legacy signature so the endpoint doesn't
        # 500 during a partial rollout.
        try:
            perfs = _list(niche_id)
        except Exception as exc:
            logger.error(
                "[source_performance] legacy fallback list failed for niche=%s: %s",
                niche_id,
                exc,
                exc_info=True,
            )
            return api_error(error="Internal server error", code=500)
    except Exception as exc:
        logger.error(
            "[source_performance] list failed for niche=%s platform=%s: %s",
            niche_id,
            platform,
            exc,
            exc_info=True,
        )
        return api_error(error="Internal server error", code=500)

    sources = [_row_to_dict(p) for p in perfs[:top_n]]

    return api_success(
        data={
            "niche_id": niche_id,
            "top_n": top_n,
            "platform": platform,
            "sources": sources,
        }
    )
