"""AI-content disclosure helpers (PR #566 stub, wired in PR #567).

Generates the per-platform disclosure text required by YouTube's
2024 AI-content policy, Meta's similar policy, TikTok's AI-content
label requirement, and X's broad-spectrum content labeling guidance.

## Why this exists

The platforms' 2024-era policies require creators to LABEL
AI-generated or AI-altered content. Failure to label has resulted
in:
  * YouTube: video removal + channel-level strike toward termination
  * Meta (IG/FB): reach throttling + 'AI labelled' badge added
                  automatically (worse if it looks like you tried
                  to hide it)
  * TikTok: video removal + creator-eligibility downgrade
  * X: visibility reduction + 'Synthetic and manipulated media' label

Our pipeline writes captions + hooks with Claude Haiku and renders
videos with FFmpeg+logo overlays — both qualify as AI-generated or
AI-altered. We MUST disclose.

## Public surface

  generate_ai_disclosure(platform: str) -> str
    Returns the disclosure string for the platform. Empty string
    when platform is unknown (caller proceeds without disclosure
    rather than crashing; pin tests catch missing platforms).

  AI_DISCLOSURE_BY_PLATFORM — the canonical mapping. Operators
    edit here when platforms update their policy wording.

## PR #566 status: STUB

This module ships the mapping + helper but is NOT yet called by
the publishers. PR #567 will wire it into publish_all_platforms
to append the disclosure text to every AI-written caption.

The mapping reads from operator-tunable YAML in PR #567 (today's
hardcoded strings become the default); operators can override per-
niche to match brand voice.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Per-platform disclosure text. Conservative defaults — short enough
# to fit in caption-length budgets, explicit enough to satisfy
# 2024-era platform policy. Operators can override per-niche in
# PR #567 via publishing.yaml.
AI_DISCLOSURE_BY_PLATFORM: dict[str, str] = {
    # YouTube: required for AI-generated narration + altered media.
    # YouTube also exposes a checkbox in the upload UI for "Altered
    # Content"; this is the description-side disclosure.
    "youtube": "AI-assisted summary. Footage from original creators (credited).",
    # Instagram: Meta's AI label is automatic for detected synthetic
    # media; explicit text below covers the gap when Meta misses it.
    "instagram": "AI-assisted post. #ai",
    # Facebook: same Meta policy as IG.
    "facebook": "AI-assisted post. #ai",
    # TikTok: their AI-generated label is the primary signal; this
    # caption-side note is belt-and-suspenders for cases TikTok's
    # detector misses (renders that don't trigger their classifier).
    "tiktok": "AI-assisted edit. #ai",
    # X: short by design (280-char budget). Hashtag is the durable
    # signal that survives reshares.
    "x_twitter": "AI-assisted. #ai",
    # R-46 legacy alias: the default platform list still uses
    # "twitter"; build_platform_specific's twitter branch passes
    # "twitter" not "x_twitter". Same disclosure either way.
    "twitter": "AI-assisted. #ai",
    # Threads: same shape as Twitter.
    "threads": "AI-assisted. #ai",
}


# Per-platform hard character budgets. None means "no cap that matters
# at typical caption length" — IG (2200), FB (63206), YT description
# (5000), Threads (500) all have plenty of room.
# Twitter is the only platform where the disclosure could push us
# over budget — 280-char hard cap.
PLATFORM_CHAR_BUDGETS: dict[str, int | None] = {
    "instagram": None,
    "youtube": None,
    "facebook": None,
    "tiktok": None,
    "threads": 500,
    "x_twitter": 280,
    "twitter": 280,  # legacy name still in some code paths
}


def apply_ai_disclosure(
    caption: str,
    platform: str,
    *,
    niche_id: str = "",
    blueprint_id=None,
) -> str:
    """PR #567 (2026-06-25): append the platform's AI disclosure
    text to ``caption`` if not already present. Idempotent +
    budget-aware + logs to compliance_events when an append happens.

    Returns the caption with disclosure appended, or unchanged if:
      * caption is empty
      * platform has no disclosure text configured
      * disclosure substring is already in the caption (idempotent —
        re-publishing a blueprint won't double-append)
      * disclosure alone exceeds the platform's char budget (fail-
        safe: return caption unchanged rather than ship a broken
        truncation)

    For platforms with a char budget (twitter: 280), the original
    caption is truncated FIRST to make room for ``" {disclosure}"``.
    Other platforms (IG/YT/FB/TT) have plenty of room — disclosure
    just appends.

    Logging: writes one ``ai_disclosure_added`` event to
    compliance_events per append (best-effort; never raises).
    Skipped publishes (already present, no disclosure for platform)
    do NOT log — only actual changes log.
    """
    if not caption or not platform:
        return caption
    disclosure = generate_ai_disclosure(platform)
    if not disclosure:
        return caption
    if disclosure in caption:
        # Idempotent — already there. No log because nothing changed.
        return caption

    suffix = f" {disclosure}"
    max_len = PLATFORM_CHAR_BUDGETS.get(platform.lower())

    if max_len is not None:
        if len(suffix) > max_len:
            # Disclosure alone won't fit — fail-safe.
            logger.warning(
                "[disclosure] disclosure for platform=%r exceeds budget %d; "
                "skipping append (caption unchanged)",
                platform,
                max_len,
            )
            return caption
        if len(caption) + len(suffix) > max_len:
            # Truncate caption to make room. rstrip prevents trailing
            # whitespace from the truncation site.
            budget = max_len - len(suffix)
            caption = caption[:budget].rstrip()

    result = caption + suffix

    # Best-effort log — never raises (log_compliance_event fail-opens)
    try:
        from genlab_core.compliance.events import log_compliance_event

        log_compliance_event(
            niche_id=niche_id or "all",
            event_type="ai_disclosure_added",
            decision="allow",
            blueprint_id=blueprint_id,
            platform=platform,
            reasons=["caption_disclosure_appended"],
            metadata={
                "disclosure_length": len(disclosure),
                "caption_truncated": len(caption) + len(suffix) > (max_len or 999_999),
            },
        )
    except Exception as exc:  # noqa: BLE001 — never propagate
        # Round-3 audit P3 cleanup (2026-06-26): WARN, not DEBUG.
        # The compliance_events row is the AUDIT TRAIL. Losing this
        # silently means a regulator-asked "did you log AI
        # disclosure?" cannot be answered. The compliance moat
        # depends on every disclosure-add being evidenced; DEBUG
        # defeats that evidence chain.
        logger.warning(
            "[disclosure] compliance_events log skipped for blueprint=%s "
            "platform=%s — AUDIT TRAIL has a gap: %s",
            blueprint_id,
            platform,
            exc,
        )

    return result


def generate_ai_disclosure(platform: str) -> str:
    """Return the per-platform disclosure text, or '' on unknown.

    Empty string on unknown platform is intentional: caller proceeds
    without disclosure rather than crashing. Pin tests (PR #567)
    catch missing platforms before they ship — this fail-safe just
    keeps the system running if a new platform gets added but
    disclosure isn't yet configured.
    """
    if not platform:
        return ""
    text = AI_DISCLOSURE_BY_PLATFORM.get(platform.lower(), "")
    if not text:
        logger.debug(
            "[disclosure] no AI disclosure text for platform=%r; returning empty",
            platform,
        )
    return text
