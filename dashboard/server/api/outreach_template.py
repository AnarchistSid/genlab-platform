"""Outreach template generator.

Routes:
    GET /api/v1/sponsorship/outreach-template?niche=<niche_id>
       -- pre-drafted sponsor outreach text + subject + kit URL

## Why this endpoint exists

PR #481 surfaced WHICH niches are pitchable (tier signal). PRs #482
+ #483 produced the deliverable artifact (per-niche + portfolio kits).
This PR closes the loop: when the operator decides to pitch, the
endpoint generates the OUTREACH MESSAGE ITSELF — pre-drafted
subject + body + media-kit link, parameterised with the niche's
own audience numbers from monetisationprogress.

The operator then hits "Copy pitch" on the dashboard card,
clipboard fills with the template, paste into email / Slack /
LinkedIn DM. **Same value as an email-send button, 10× less infra:**
no CRM, no email auth, no deliverability concerns, no approval
queue. The operator already has their channels and contacts.

## Architecture

Pure decoration of existing data. The endpoint:
  1. Reuses ``_pg_fetch_progress(niche_id=...)`` for audience numbers
  2. Reuses ``_compute_tier`` from sponsorship_readiness so the tier
     surfaced in the email body matches the dashboard card
  3. Reuses ``_build_audience_summary`` from media_kit for the
     headline platform list
  4. Returns a template that the FRONTEND copies to clipboard —
     server doesn't send anything

No new tables, no migrations, no email infra.

## Template philosophy

The template is intentionally TERSE and OPERATOR-FILLABLE:

  - First line: niche name + headline number ("Blackbox Brief
    @ 1.2K YouTube subscribers")
  - Hook: 1-2 sentences why this niche fits the brand
  - Stat block: 2-3 platforms with sizes
  - Call to action: schedule a call + kit link
  - Sign-off with the operator's [NAME] placeholder

Operator replaces [BRAND] and [NAME] manually. Anything more
parameterised would either need a CRM (out of scope) or look
templated (worse outreach than a 1-paragraph manual rewrite).

When ``tier=tracking``, the template steers toward "let's stay
in touch" rather than asking for a deal — protects against
operator accidentally pitching before the channel is sponsor-ready.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, request

# Reuse PR #481 + #482's primitives — single source of truth.
from server.api.media_kit import _build_audience_summary
from server.api.sponsorship_readiness import (
    _compute_platform_summary,
    _compute_tier,
)
from server.core.monetisation_progress_pg import fetch_progress as _pg_fetch_progress
from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("outreach_template_api", __name__, url_prefix="/api/v1/sponsorship")

# Same closed-set as media_kit's _VALID_NICHE_IDS — centralised per
# dashboard convention (one allowlist per endpoint to avoid
# accidental cross-endpoint coupling).
_VALID_NICHE_IDS = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})

# Display-name mapping for the template. Centralised here (not
# pulled from frontend registry) because the backend has no React
# imports. Mirrors src/niches/registry.ts:NICHES.
_NICHE_DISPLAY: dict[str, dict[str, str]] = {
    "ai_creators": {"name": "Blackbox Brief", "tagline": "AI creator clips"},
    "gaming": {"name": "CriticalRush", "tagline": "Gaming highlights"},
    "sports": {"name": "ClutchWire", "tagline": "Sports moments"},
    "movies": {"name": "SpliceReel", "tagline": "Film + entertainment"},
    "anime": {"name": "FrameDrift", "tagline": "Anime clips"},
}


def _format_audience_phrase(entry: dict[str, Any]) -> str:
    """Turn one audience entry into a short prose phrase.

    "1.2K subscribers on YouTube" or "9.5K followers on Instagram".
    Used in the template's stat block. Falls back to bare numbers
    on metric_names we don't have nice phrasing for.
    """
    value = entry.get("current_value")
    platform = (entry.get("platform") or "").lower()
    metric = entry.get("metric_name") or "audience"
    formatted = _format_number(value)
    # Per-platform display name (some platforms have official names
    # the template should use instead of lowercased keys).
    platform_label = {
        "youtube": "YouTube",
        "instagram": "Instagram",
        "facebook": "Facebook",
        "x_twitter": "X",
        "twitter": "X",
        "threads": "Threads",
        "tiktok": "TikTok",
    }.get(platform, platform.capitalize())
    return f"{formatted} {metric} on {platform_label}"


def _format_number(n: Any) -> str:
    """Compact format — mirrors the frontend's formatNumber for
    consistency between dashboard rendering and email-template text."""
    if n is None:
        return "—"
    try:
        n_float = float(n)
    except (TypeError, ValueError):
        return "—"
    abs_n = abs(n_float)
    if abs_n >= 1_000_000:
        return f"{n_float / 1_000_000:.1f}M"
    if abs_n >= 1_000:
        return f"{n_float / 1_000:.1f}K"
    return f"{int(n_float):,}" if n_float == int(n_float) else f"{n_float:.2f}"


def _build_template_body(
    niche_id: str,
    audience: list[dict[str, Any]],
    tier: str,
    kit_url: str,
) -> str:
    """Build the email/DM body text. Tier-aware copy.

    Operator fills in [BRAND] and [NAME] before sending. Template
    keeps it short and human — the goal is "operator hits Send in
    under 30 seconds after paste". Anything longer feels templated.
    """
    display = _NICHE_DISPLAY.get(niche_id, {"name": niche_id, "tagline": ""})
    name = display["name"]
    tagline = display["tagline"]

    # Stat block — first 3 platforms by size (already sorted DESC by
    # _build_audience_summary). Joined with " · " for inline reading.
    stat_phrases = [
        _format_audience_phrase(a) for a in audience[:3] if a.get("current_value") is not None
    ]
    stat_line = " · ".join(stat_phrases) if stat_phrases else "audience building"

    # Tier-specific CTA. eligible_now asks for a deal; lower tiers
    # ask for "stay in touch" so the operator doesn't accidentally
    # pitch before the channel is sponsor-ready.
    if tier == "eligible_now":
        cta = (
            "We're actively open to brand partnerships this quarter. "
            "Would a 15-minute call next week work to walk through fit?"
        )
    elif tier in ("within_2_months", "within_6_months"):
        cta = (
            "We're approaching brand-deal thresholds and starting to "
            "build a shortlist of partners for the next quarter. "
            "Would you be open to a brief intro call?"
        )
    else:
        # tracking — soft "stay in touch" rather than active pitch
        cta = (
            "We're still building audience but wanted to put "
            f"{name} on your radar for future quarters. Happy to share "
            "updates as we grow."
        )

    body = (
        f"Hi [BRAND],\n\n"
        f"I'm reaching out about {name} — our {tagline.lower()} channel. "
        f"Quick snapshot of where we are:\n\n"
        f"{stat_line}.\n\n"
        f"{cta}\n\n"
        f"Full media kit: {kit_url}\n\n"
        f"Best,\n"
        f"[NAME]"
    )
    return body


def _build_subject(niche_id: str, tier: str) -> str:
    """Subject line — short, accurate, NOT clickbait.

    eligible_now niches get "Sponsorship opportunity"; lower tiers
    get "Quick intro" so operator doesn't mislead the recipient
    about deal-readiness.
    """
    display = _NICHE_DISPLAY.get(niche_id, {"name": niche_id})
    name = display["name"]
    if tier == "eligible_now":
        return f"Sponsorship opportunity: {name}"
    return f"Quick intro: {name}"


@bp.route("/outreach-template")
def outreach_template():
    """Return a ready-to-send outreach template for one niche.

    Query params:
        niche (required) -- one of the 5 known niche_ids

    Response shape::

        {
          "niche_id": "ai_creators",
          "tier": "eligible_now" | "within_2_months" | ... ,
          "subject": "Sponsorship opportunity: Blackbox Brief",
          "body": "<full email body with [BRAND] + [NAME] placeholders>",
          "media_kit_url": "/media-kit/ai_creators",
          "audience_summary": [<top-3 platform headline metrics>]
        }

    404 when ``niche`` is missing or not in the closed set.
    """
    niche_id = (request.args.get("niche") or "").strip()
    if not niche_id:
        return api_error(
            error="Missing required query param: niche",
            code=400,
        )
    if niche_id not in _VALID_NICHE_IDS:
        return api_error(
            error=f"Unknown niche: {niche_id}. Valid: {sorted(_VALID_NICHE_IDS)}",
            code=404,
        )

    try:
        records = _pg_fetch_progress(niche_id=niche_id)
    except Exception as exc:
        logger.exception("[outreach_template] fetch_progress failed for niche=%s", niche_id)
        return api_error(error=str(exc), code=500)

    # Group rows by platform — same shape sponsorship_readiness expects
    platforms: dict[str, list[dict]] = {}
    for raw in records:
        rec = raw.get("fields", raw)
        platform = rec.get("platform") or "unknown"
        platforms.setdefault(platform, []).append(rec)

    platforms_summary = {
        plat: _compute_platform_summary(metrics) for plat, metrics in platforms.items()
    }
    all_metrics = [m for metrics in platforms.values() for m in metrics]
    tier, _ = _compute_tier(platforms_summary, all_metrics)
    audience = _build_audience_summary(platforms)

    kit_url = f"/media-kit/{niche_id}"
    subject = _build_subject(niche_id, tier)
    body = _build_template_body(niche_id, audience, tier, kit_url)

    payload = {
        "niche_id": niche_id,
        "tier": tier,
        "subject": subject,
        "body": body,
        "media_kit_url": kit_url,
        # Top-3 audience entries for the dashboard's preview pane.
        # The frontend uses these to render a small inline preview
        # next to the "Copy pitch" button without re-fetching.
        "audience_summary": audience[:3],
    }
    return api_success(data=payload)
