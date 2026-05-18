"""
genlab_core.platform.platform_rules — Universal platform adaptation rules.

These 8 rules are platform algorithm requirements, not niche preferences.
They apply to every niche on every platform. CriticalRush currently enforces
only 2 of 8, meaning 4 rules cause daily measurable algorithm penalties:

  Instagram URL penalty:   Captions with URLs get reach reduction
  Instagram hashtag gap:   Missing 3-5 hashtags misses discovery reach
  Instagram CTA gap:       No CTA at caption end -> lower save/share rates
  X safe-zone gap:         9:16 video without safe-zone crop obscured by UI

This module is a pre-niche enforcement layer. Every niche's adaptation stage
calls enforce_platform_rules() BEFORE applying its own niche-specific strategy,
so niche code never needs to re-implement these platform fundamentals.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# US date regex: M/D/YY, MM/DD/YY, M/D/YYYY, MM/DD/YYYY
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")

_MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# URL pattern for stripping
_URL_RE = re.compile(r"https?://\S+")

# Competitor platform mentions to strip from Facebook captions
# (FB flags these as spam/cross-promotion)
_COMPETITOR_HASHTAGS_RE = re.compile(
    r"#(?:tiktok|twitter|youtube|instagram|threads|snapchat|twitch)\b",
    re.IGNORECASE,
)

# Max hashtags for Facebook (more than 5 triggers spam detection)
_FB_MAX_HASHTAGS = 5

# Default CTA templates (niche can override via cta parameter)
_DEFAULT_CTAS = [
    "Save this for later!",
    "Follow for more updates.",
    "Share with someone who needs to see this.",
]

# YouTube Shorts title max length
_YT_SHORTS_MAX_CHARS = 40


@dataclass
class AdaptedContent:
    """Content after universal rule enforcement."""

    title: str
    caption: str
    hashtags: list[str]
    first_comment: str  # X: links go here, not in tweet body
    warnings: list[str] = field(default_factory=list)


def enforce_platform_rules(
    platform: str,
    title: str,
    caption: str,
    hashtags: list[str] | None = None,
    url: str | None = None,
    cta: str | None = None,
) -> AdaptedContent:
    """
    Enforce all universal platform rules for a given platform.

    Rules extracted from BlackboxBrief's adapt_for_platforms.py:

    YOUTUBE:
      - Title must be question format for Shorts shelf eligibility
      - Title must be <= 40 chars

    INSTAGRAM:
      - No URLs in caption body (algorithm penalty)
      - 3-5 hashtags required for discovery
      - Caption must end with a CTA for engagement

    X/TWITTER:
      - No external URLs in tweet body (move to first_comment)

    FACEBOOK:
      - Short captions get engagement question appended

    UNIVERSAL:
      - US date format (MM/DD/YYYY) converted to spelled-out format
    """
    adapted = AdaptedContent(
        title=title,
        caption=caption,
        hashtags=list(hashtags or []),
        first_comment="",
    )

    platform = platform.lower()

    if platform == "youtube":
        _enforce_youtube_rules(adapted)
    elif platform == "instagram":
        _enforce_instagram_rules(adapted, url=url, cta=cta)
    elif platform in ("twitter", "x", "x_standard", "x_premium"):
        _enforce_twitter_rules(adapted, url=url)
    elif platform == "facebook":
        _enforce_facebook_rules(adapted)
    elif platform == "threads":
        # Threads uses Instagram backend — same URL and hashtag rules
        _enforce_instagram_rules(adapted, url=url, cta=cta)

    # Universal: date format (applies to all platforms)
    _enforce_date_format(adapted)

    if adapted.warnings:
        for w in adapted.warnings:
            logger.warning("[PLATFORM_RULES][%s] %s", platform, w)

    return adapted


def _enforce_youtube_rules(adapted: AdaptedContent) -> None:
    """YouTube: question-format title, <= 40 chars for Shorts shelf."""
    title = adapted.title.strip()
    if not title:
        return

    # Convert to question format if not already a question
    if not title.rstrip().endswith("?"):
        # Try to make it a real question by prepending question words
        lower = title.lower()
        if any(lower.startswith(w) for w in ("this ", "these ", "the ", "a ")):
            # "This AI tool changed everything" -> "Did this AI tool just change everything?"
            title = title.rstrip("?.!,").strip()
            title = f"Did {title[0].lower()}{title[1:]}?"
        elif any(lower.startswith(w) for w in ("just ", "already ")):
            title = f"Did this {title.rstrip('?.!,').strip()}?"
        else:
            # Fallback: strip trailing punctuation and add ?
            title = title.rstrip("?.!,").strip() + "?"
        adapted.warnings.append(
            "Title converted to question format for Shorts shelf eligibility"
        )

    # Enforce max length
    if len(title) > _YT_SHORTS_MAX_CHARS:
        # Truncate smartly at word boundary
        truncated = title[:_YT_SHORTS_MAX_CHARS - 1]
        last_space = truncated.rfind(" ")
        if last_space > 20:
            truncated = truncated[:last_space]
        title = truncated.rstrip("?.!,").strip() + "?"
        adapted.warnings.append(
            f"Title truncated to {_YT_SHORTS_MAX_CHARS} chars"
        )

    adapted.title = title


def _enforce_instagram_rules(
    adapted: AdaptedContent,
    url: str | None = None,
    cta: str | None = None,
) -> None:
    """Instagram: strip URLs, ensure 3-5 hashtags, add CTA."""
    caption = adapted.caption

    # 1. Strip all URLs from caption body (Instagram penalizes external links)
    if _URL_RE.search(caption):
        caption = _URL_RE.sub("", caption)
        # Clean up whitespace left by URL removal
        caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
        adapted.warnings.append("External URL stripped from caption")

    # 2. Ensure 3-5 hashtags
    if len(adapted.hashtags) < 3:
        adapted.warnings.append(
            f"Only {len(adapted.hashtags)} hashtags provided (need 3-5)"
        )

    # 3. CTA at end of caption — only append if the caption truly has no
    #    CTA-shaped phrase. The LLM writer already includes one from
    #    NICHE_VOICE (e.g. "Peak or mid?", "Drop your hot take 👇"); we must
    #    detect those, not just the specific ``cta`` string or
    #    ``_DEFAULT_CTAS``, otherwise every caption gets a duplicate CTA.
    cta_text = cta or _DEFAULT_CTAS[0]
    candidates = list(_DEFAULT_CTAS)
    if cta:
        candidates.append(cta)
    caption_lower = caption.lower()
    tail = caption.rstrip()[-160:]  # CTAs almost always live at the end
    tail_lower = tail.lower()
    has_known_cta = any(c.lower() in caption_lower for c in candidates)
    # Generic CTA-shape detection: end with question, finger-pointing emoji,
    # or an imperative verb commonly used as a CTA.
    has_cta_shape = (
        tail.endswith("?")
        or any(emoji in tail for emoji in ("👇", "👉", "🔥"))
        or bool(re.search(
            r"\b(follow|tag|comment|save|share|drop|rate|sub or dub|peak or mid|"
            r"w or l|caught up|watch or skip|hot take|are you watching|"
            r"have you seen|what do you think)\b",
            tail_lower,
        ))
    )
    has_cta = has_known_cta or has_cta_shape
    if not has_cta and caption:
        caption = f"{caption}\n\n{cta_text}"
        adapted.warnings.append("CTA appended to caption")

    adapted.caption = caption


def _enforce_twitter_rules(
    adapted: AdaptedContent, url: str | None = None
) -> None:
    """X/Twitter: move URLs from body to first_comment."""
    caption = adapted.caption

    # Extract all URLs from tweet body
    urls_found = _URL_RE.findall(caption)
    if urls_found:
        # Strip URLs from body
        caption = _URL_RE.sub("", caption)
        caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
        # Move the first URL to first_comment
        adapted.first_comment = urls_found[0]
        adapted.warnings.append("URL moved from tweet body to first comment")

    # If caller provided a URL and none was in body, set it as first_comment
    if not adapted.first_comment and url:
        adapted.first_comment = url

    adapted.caption = caption


def _enforce_facebook_rules(adapted: AdaptedContent) -> None:
    """Facebook: strip URLs, cap hashtags, remove competitor mentions, engagement Q."""
    caption = adapted.caption

    # 1. Strip all URLs (FB flags external links as spam/promotional)
    if _URL_RE.search(caption):
        caption = _URL_RE.sub("", caption)
        caption = re.sub(r"\n{3,}", "\n\n", caption).strip()
        adapted.warnings.append("External URL stripped from Facebook caption")

    # 2. Remove competitor platform hashtags (#tiktok, #twitter, etc.)
    if _COMPETITOR_HASHTAGS_RE.search(caption):
        caption = _COMPETITOR_HASHTAGS_RE.sub("", caption)
        caption = re.sub(r"  +", " ", caption).strip()
        adapted.warnings.append("Competitor platform mentions removed from Facebook caption")

    # Also strip competitor hashtags from the hashtags list
    adapted.hashtags = [
        h for h in adapted.hashtags
        if not _COMPETITOR_HASHTAGS_RE.match(h if h.startswith("#") else f"#{h}")
    ]

    # 3. Cap hashtags at _FB_MAX_HASHTAGS
    if len(adapted.hashtags) > _FB_MAX_HASHTAGS:
        adapted.hashtags = adapted.hashtags[:_FB_MAX_HASHTAGS]
        adapted.warnings.append(f"Facebook hashtags capped at {_FB_MAX_HASHTAGS}")

    # 4. Short captions get engagement question
    if caption and len(caption) < 200:
        caption = (
            f"{caption}\n\n"
            "What do you think about this? Let us know in the comments."
        )
        adapted.warnings.append("Engagement question added to short caption")

    adapted.caption = caption


def _enforce_date_format(adapted: AdaptedContent) -> None:
    """Universal: convert MM/DD/YYYY to spelled-out format."""
    adapted.title = _US_DATE_RE.sub(_convert_us_date, adapted.title)
    adapted.caption = _US_DATE_RE.sub(_convert_us_date, adapted.caption)


def _convert_us_date(match: re.Match) -> str:
    """Convert M/D/YY date to spelled-out format (January 3, 2026)."""
    month_num = int(match.group(1))
    day = int(match.group(2))
    year_str = match.group(3)

    if month_num < 1 or month_num > 12 or day < 1 or day > 31:
        return match.group(0)  # Not a valid date, leave as-is

    year = int(year_str)
    if year < 100:
        year += 2000

    month_name = _MONTH_NAMES[month_num]
    return f"{month_name} {day}, {year}"
