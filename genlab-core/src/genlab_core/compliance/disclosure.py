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
    # Threads: same shape as Twitter.
    "threads": "AI-assisted. #ai",
}


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
