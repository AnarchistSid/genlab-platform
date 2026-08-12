"""YouTube Shorts SEO description builder.

## Motivation

The current YouTube publish path (`platforms/youtube.py`) builds the
description as:

    #Shorts
    <IG caption>
    <hashtags>

IG captions are optimized for feed engagement (short body + strong CTA
+ compact hashtag block). YouTube Shorts recommendation and search
benefit from a DIFFERENT description shape:

  * Longer body (search parses first 100-150 tokens for keyword match)
  * Niche-topical hashtag anchors (Shorts classifier buckets content
    into a niche vertical based on hashtag co-occurrence)
  * Curiosity-line early (feeds the description-preview snippet that
    shows above the fold in the Shorts side-panel)

## What this module does

Given a blueprint's already-generated content, produces a
Shorts-optimized description string with structure:

    #Shorts

    <hook — curiosity-line at eyeball-anchor position>

    <caption body — the existing IG caption stripped of trailing tags>

    Watch until the end. Follow for daily <niche> clips.

    <existing story-specific hashtags>
    <niche-topical anchor hashtags — 3-5 per niche>

    <source credit line if not already present>

Zero LLM cost — pure composition.

## Consumer wire (flag-gated)

Called by `platforms/youtube.py._publish_reel()` in place of the ad-hoc
description construction. Behind `GENLAB_YT_SHORTS_SEO_ENABLED` env
flag so the legacy behavior remains the default until operator flips.

## Fail-open

Every failure path returns the legacy shape (`#Shorts\\n\\n<caption>\\n\\n<tags>`).
Never raises.

## Niche anchor hashtags

Curated per niche below. Small enough to hard-code (rarely change).
Deliberate ordering: broadest → narrowest, so YouTube's classifier
sees the vertical category tag first (`#Gaming` before `#EldenRing`).
Operators PR-update the map when new sub-niches become relevant.
"""

from __future__ import annotations

import logging
import re
from typing import Final

logger = logging.getLogger(__name__)

_HASHTAG_LIMIT: Final[int] = 15
"""YouTube's Shorts hashtag cap in the description. Beyond 15 the
whole hashtag block gets ignored by the classifier — this is the
same rule as the regular YouTube description hashtag cap."""

# Per-niche anchor hashtag pools. Broadest → narrowest ordering so
# the classifier reads the vertical category before the sub-topic.
# Kept small (3-6 anchors) so story-specific hashtags still fit under
# the 15-tag cap.
_NICHE_ANCHOR_HASHTAGS: Final[dict[str, tuple[str, ...]]] = {
    "gaming": ("#Gaming", "#GamingShorts", "#GameClips", "#Esports"),
    "sports": ("#Sports", "#SportsShorts", "#SportsHighlights", "#SportsMoments"),
    "movies": ("#Movies", "#MovieClips", "#FilmTok", "#Trailers", "#Cinema"),
    "anime": ("#Anime", "#AnimeShorts", "#AnimeClips", "#AnimeEdit", "#Manga"),
    "ai_creators": ("#AI", "#AITools", "#TechShorts", "#AITutorial", "#Automation"),
}
_FOLLOW_CTA_TEMPLATES: Final[dict[str, str]] = {
    "gaming": "Watch until the end. Subscribe for daily gaming clips.",
    "sports": "Watch until the end. Subscribe for daily sports highlights.",
    "movies": "Watch until the end. Subscribe for daily movie clips.",
    "anime": "Watch until the end. Subscribe for daily anime moments.",
    "ai_creators": "Watch until the end. Subscribe for daily AI updates.",
}
_DEFAULT_ANCHORS: Final[tuple[str, ...]] = ("#Shorts",)
_DEFAULT_CTA: Final[str] = "Watch until the end. Subscribe for more."


def _is_enabled() -> bool:
    """Env kill switch. Default OFF so import + wire is a no-op
    until operator flips."""
    from genlab_core.settings import env_true

    return env_true("GENLAB_YT_SHORTS_SEO_ENABLED")


def _extract_hashtags(text: str) -> tuple[str, list[str]]:
    """Split a body string into (body_without_hashtags, hashtags).

    Only strips a trailing hashtag block (a run of #tags at the very
    end, optionally separated by blank lines). Inline hashtags mid-
    sentence are left in place — those are content, not metadata.
    """
    if not text:
        return "", []
    lines = text.rstrip().split("\n")
    trailing: list[str] = []
    while lines:
        stripped = lines[-1].strip()
        if not stripped:
            lines.pop()
            continue
        tokens = stripped.split()
        if tokens and all(t.startswith("#") for t in tokens):
            trailing = tokens + trailing
            lines.pop()
        else:
            break
    return "\n".join(lines).rstrip(), trailing


def _dedupe_case_insensitive(tags: list[str]) -> list[str]:
    """Preserve first-seen order, drop later duplicates (case-insensitive)."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def build_shorts_description(
    *,
    hook: str,
    caption: str,
    hashtags: list[str],
    niche_id: str,
    source_credit: str = "",
    max_length: int = 5000,
) -> str:
    """Compose a YouTube Shorts-optimized description.

    Args:
        hook: the video's hook line (used as curiosity-anchor).
        caption: the general caption (typically the IG caption, may
            already contain trailing hashtags and source credit).
        hashtags: story-specific hashtag list. Deduped against niche
            anchors; capped at _HASHTAG_LIMIT.
        niche_id: 'gaming', 'sports', 'movies', 'anime', 'ai_creators'.
        source_credit: audience-facing attribution line (may be empty).
            Only appended if not already substring-present.
        max_length: YouTube's description cap (5000 chars, hard-cut).

    Returns:
        Assembled description string. Falls back to the legacy shape
        (`#Shorts\\n\\n<caption>\\n\\n<hashtags>`) when the flag is off
        so callers can call unconditionally and get old behavior.
    """
    if not _is_enabled():
        return _legacy_description(caption, hashtags, max_length)

    try:
        return _build_enriched(
            hook=hook,
            caption=caption,
            hashtags=hashtags,
            niche_id=niche_id,
            source_credit=source_credit,
            max_length=max_length,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[yt_shorts_seo] build failed for niche=%s (%s) — falling back to legacy",
            niche_id, exc,
        )
        return _legacy_description(caption, hashtags, max_length)


def _legacy_description(
    caption: str,
    hashtags: list[str],
    max_length: int,
) -> str:
    """Byte-identical to platforms/youtube.py:425-430 pre-SEO shape."""
    hashtags_str = " ".join(h for h in (hashtags or []) if h)
    parts = ["#Shorts"]
    if caption:
        parts.append(caption.strip())
    if hashtags_str:
        parts.append(hashtags_str)
    return "\n\n".join(parts)[:max_length]


def _build_enriched(
    *,
    hook: str,
    caption: str,
    hashtags: list[str],
    niche_id: str,
    source_credit: str,
    max_length: int,
) -> str:
    caption_body, caption_trailing_tags = _extract_hashtags(caption or "")

    # Merge hashtags: story-provided + inline-trailing-from-caption + niche anchors
    all_tags: list[str] = []
    all_tags.extend(t for t in (hashtags or []) if t and t.startswith("#"))
    all_tags.extend(caption_trailing_tags)
    all_tags.extend(_NICHE_ANCHOR_HASHTAGS.get(niche_id, ()))
    all_tags = _dedupe_case_insensitive(all_tags)[:_HASHTAG_LIMIT]

    cta = _FOLLOW_CTA_TEMPLATES.get(niche_id, _DEFAULT_CTA)

    parts: list[str] = ["#Shorts"]

    hook_clean = (hook or "").strip()
    if hook_clean:
        parts.append(hook_clean)

    if caption_body:
        parts.append(caption_body)

    parts.append(cta)

    if all_tags:
        parts.append(" ".join(all_tags))

    if source_credit:
        credit_clean = source_credit.strip()
        # Idempotent — mirrors the same substring guard used by _credit()
        # in push_to_backlog. Two publishes of the same blueprint must
        # not append the credit twice.
        if credit_clean and credit_clean not in "\n\n".join(parts):
            parts.append(credit_clean)

    assembled = "\n\n".join(parts)

    # Preserve YouTube's 5000-char cap. Cut at word boundary if possible.
    if len(assembled) > max_length:
        cut = assembled[:max_length].rsplit(" ", 1)[0]
        assembled = cut
    return assembled


__all__ = [
    "build_shorts_description",
    "_NICHE_ANCHOR_HASHTAGS",  # exported for tests + config visibility
]
