"""Media-kit API endpoint.

Routes:
    GET /api/v1/media-kit/<niche_id>  -- per-niche printable sponsor kit

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

    payload = {
        "niche_id": niche_id,
        "tier": tier,
        "nearest_threshold_days": nearest_days,
        "audience": audience,
        "monetised_platforms": monetised_platforms,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return api_success(data=payload)
