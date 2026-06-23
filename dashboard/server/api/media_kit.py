"""Media-kit API endpoint.

Routes:
    GET /api/v1/media-kit/<niche_id>  -- per-niche printable sponsor kit
    GET /api/v1/media-kit/_all        -- portfolio kit covering all 5 niches

## Why this endpoint exists

PR #481 shipped sponsorship-readiness tiers as on-screen UX. This PR
extends the same data layer into an OPERATOR DELIVERABLE: a structured
JSON payload the frontend renders as a print-friendly one-pager. The
operator hits Cmd+P → "Save as PDF" → email to a brand. Zero new
infra (no headless browser, no PDF library).

## Architecture — pure decoration of existing data

Same shape as PR #481: reuses ``server.core.monetisation_progress_pg.fetch_progress``
for audience numbers and IMPORTS the tier-computation primitives from
the sponsorship endpoint so the kit and the dashboard card NEVER
disagree on tier.

Niche branding (display name, tagline, accent color) is intentionally
NOT echoed from the backend — the frontend has the canonical niche
registry already (``src/niches/registry.ts``). Echoing it from the
backend would risk drift; the contract is "frontend supplies branding,
backend supplies metrics".

## Failure mode

When monetisationprogress is empty for a niche (cold start), the
endpoint returns the niche shell with empty platform arrays and the
``tracking`` tier. The frontend page renders a "data pending"
placeholder rather than a 404 — same defensive shape as the
sponsorship card.

When the niche_id is not in the known 5, returns 404 — invalid input
deserves a clear error (vs the empty-data case which is valid state).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint

# Reuse PR #481's tier-computation helpers so kit and card NEVER drift.
from server.api.sponsorship_readiness import (
    _compute_platform_summary,
    _compute_tier,
)
from server.core.monetisation_progress_pg import fetch_progress as _pg_fetch_progress
from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("media_kit_api", __name__, url_prefix="/api/v1/media-kit")

# Closed-set of valid niche_ids — mirrors NICHE_IDS in the frontend
# registry. Centralised here (not imported from genlab-core) because the
# dashboard already keeps several closed-set niche allowlists locally
# (e.g., monetisation.py's _VALID_CHANNELS); follow established pattern.
_VALID_NICHE_IDS = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})

# Stable display order for the portfolio kit. Brings ai_creators
# (Blackbox Brief — highest-engagement niche per 2026-06-22 audit)
# first, followed by the rest in launch order. Mirrors the order
# used in genlab-core/config/niches_registry.yaml.
_STABLE_NICHE_ORDER = ("ai_creators", "gaming", "sports", "movies", "anime")


def _build_audience_summary(platforms: dict[str, list[dict]]) -> list[dict[str, Any]]:
    """Flatten the per-platform metrics into a media-kit-ready shape.

    For each platform, surface only the BIG NUMBERS a brand cares about:
    follower / subscriber counts and their 7-day deltas. Watch-hours and
    other internal-monetisation metrics are deliberately NOT surfaced
    here — brands don't pitch deals against "watch hours", they pitch
    against audience size.

    Returns a LIST of platform entries (NOT a dict) so the order
    survives JSON serialization regardless of Flask's
    ``JSON_SORT_KEYS`` config — the kit reads "strongest platform
    first" deterministically on the wire.
    """
    headline_metric_names = frozenset(
        {
            "subscribers",  # YouTube
            "followers",  # IG / FB / Threads / X
            "fans",  # Facebook (legacy name)
        }
    )

    summary: list[dict[str, Any]] = []
    for platform, metrics in platforms.items():
        # Pick the single most-headline-relevant metric. Brands want
        # the big number; we surface that one and ignore the rest.
        headline = next(
            (m for m in metrics if m.get("metric_name") in headline_metric_names),
            None,
        )
        if headline is None:
            # Fallback — first metric with a current_value. Better than
            # rendering an empty platform card.
            headline = next(
                (m for m in metrics if m.get("current_value") is not None),
                None,
            )
        if headline is None:
            continue
        summary.append(
            {
                "platform": platform,
                "metric_name": headline.get("metric_name", ""),
                "current_value": headline.get("current_value"),
                "delta_7d": headline.get("delta_7d"),
                "is_threshold_met": bool(headline.get("is_threshold_met", False)),
            }
        )

    # Sort descending by follower count — strongest platform first.
    summary.sort(
        key=lambda p: float(p.get("current_value") or 0),
        reverse=True,
    )
    return summary


def _build_niche_kit(
    niche_id: str,
    records: list[dict],
    *,
    include_top_posts: bool = True,
) -> dict[str, Any]:
    """Build the kit payload for a single niche from already-fetched rows.

    Extracted so both the per-niche route and the portfolio route can
    share the same body-building logic. Takes pre-fetched ``records``
    (filtered to this niche) so the portfolio route can issue ONE DB
    query and slice per-niche in memory rather than 5 separate queries.

    Args:
        niche_id: niche identifier
        records: per-niche monetisationprogress rows
        include_top_posts: when True (default), fetches top 3 recent
            posts from publishing_analytics and embeds them in the
            response under ``top_posts``. PR CC (2026-06-23) — brands
            judge content not numbers; embedding clickable top-post
            links closes the kit's prequalification gap.

    Returns the same shape as the per-niche endpoint's response body,
    minus the ``generated_at`` field (the route handler stamps that
    at its own response layer so the per-niche and portfolio routes
    use consistent timestamps).
    """
    # Group raw rows by platform
    platforms: dict[str, list[dict]] = {}
    for raw in records:
        rec = raw.get("fields", raw)
        platform = rec.get("platform") or "unknown"
        platforms.setdefault(platform, []).append(rec)

    # Reuse the sponsorship card's tier computation so the kit's tier
    # NEVER disagrees with the Mission Control card.
    platforms_summary = {
        plat: _compute_platform_summary(metrics) for plat, metrics in platforms.items()
    }
    all_metrics = [m for metrics in platforms.values() for m in metrics]
    tier, nearest_days = _compute_tier(platforms_summary, all_metrics)

    monetised_platforms = sorted(
        plat for plat, summary in platforms_summary.items() if summary["is_monetised"]
    )
    audience = _build_audience_summary(platforms)

    # PR CC: fetch top 3 recent posts from publishing_analytics.
    # Lazy import keeps the test-mocking surface clean (patching
    # the source module rather than this re-export).
    top_posts: list[dict[str, Any]] = []
    if include_top_posts:
        try:
            from server.core.top_posts_pg import fetch_top_posts

            top_posts = fetch_top_posts(niche_id, limit=3, lookback_days=30)
        except Exception as exc:
            # Top-posts is a kit-quality enhancement, not a kit-blocker.
            # Empty list on any failure — kit still renders.
            logger.debug(
                "[media_kit] top_posts fetch failed for niche=%s: %s",
                niche_id,
                exc,
            )

    return {
        "niche_id": niche_id,
        "tier": tier,
        "nearest_threshold_days": nearest_days,
        "audience": audience,
        "monetised_platforms": monetised_platforms,
        "top_posts": top_posts,
    }


@bp.route("/_all")
def get_media_kit_all():
    """Return a portfolio kit covering all 5 niches.

    Single endpoint for portfolio-style sponsor pitches — brands that
    want cross-channel deals (e.g., advertise across gaming + sports +
    movies in one campaign) need one document, not five. The frontend
    renders this at ``/media-kit/all`` as a multi-section printable
    document.

    Reuses ``_build_niche_kit`` so the portfolio sections are pixel-
    identical to the per-niche kits they shadow. One DB query (no
    niche filter) → slice per-niche in memory → build per-niche
    payloads. Strictly cheaper than 5 separate ``/<niche_id>`` calls.

    Response shape::

        {
          "niches": [
            {
              "niche_id": "ai_creators",
              "tier": "...",
              "nearest_threshold_days": ...,
              "audience": [...],
              "monetised_platforms": [...]
            },
            ...  // one entry per niche in NICHE_IDS, even cold-start
          ],
          "summary": {
            "eligible_now_count": <int>,    // how many pitchable
            "monetised_platforms_total": <int>
          },
          "generated_at": "<ISO-8601 UTC>"
        }

    Niches with zero monetisationprogress rows still appear as cold-
    start entries (tier=tracking, empty audience) so the printed
    document always has the full 5-niche shape — operator never has
    to apologise to a brand for "missing data".
    """
    try:
        records = _pg_fetch_progress()
    except Exception as exc:
        logger.exception("[media_kit] portfolio fetch_progress failed")
        return api_error(error=str(exc), code=500)

    # Slice records per-niche in memory (one DB query for the whole set)
    by_niche: dict[str, list[dict]] = {nid: [] for nid in _VALID_NICHE_IDS}
    for raw in records:
        rec = raw.get("fields", raw)
        nid = rec.get("niche_id")
        if nid in by_niche:
            by_niche[nid].append(rec)

    # Build per-niche kit payloads in a deterministic order so the
    # printable document layout is stable across requests.
    niches: list[dict[str, Any]] = []
    for nid in _STABLE_NICHE_ORDER:
        niches.append(_build_niche_kit(nid, by_niche.get(nid, [])))

    eligible_now_count = sum(1 for n in niches if n["tier"] == "eligible_now")
    monetised_platforms_total = sum(len(n["monetised_platforms"]) for n in niches)

    payload = {
        "niches": niches,
        "summary": {
            "eligible_now_count": eligible_now_count,
            "monetised_platforms_total": monetised_platforms_total,
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return api_success(data=payload)


@bp.route("/<niche_id>")
def get_media_kit(niche_id: str):
    """Return the per-niche media kit payload.

    Response shape::

        {
          "niche_id": "ai_creators",
          "tier": "eligible_now" | ... ,
          "nearest_threshold_days": <int> | null,
          "audience": [
            {
              "platform": "<platform>",
              "metric_name": "subscribers" | "followers",
              "current_value": <number>,
              "delta_7d": <number> | null,
              "is_threshold_met": <bool>
            },
            ...
          ],  // sorted descending by current_value (strongest first)
          "monetised_platforms": ["youtube", "facebook", ...],
          "generated_at": "<ISO-8601 UTC>"
        }

    404 when ``niche_id`` is not in the known 5.
    """
    if niche_id not in _VALID_NICHE_IDS:
        return api_error(
            error=f"Unknown niche_id: {niche_id}. Valid: {sorted(_VALID_NICHE_IDS)}",
            code=404,
        )

    try:
        records = _pg_fetch_progress(niche_id=niche_id)
    except Exception as exc:
        logger.exception("[media_kit] fetch_progress failed for niche=%s", niche_id)
        return api_error(error=str(exc), code=500)

    payload = _build_niche_kit(niche_id, records)
    payload["generated_at"] = datetime.now(UTC).isoformat()
    return api_success(data=payload)
