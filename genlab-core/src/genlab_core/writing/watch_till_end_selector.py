"""Watch-till-end variant selector — pure function, no I/O.

Layer 3 S3 (2026-07-17). Selects when a story is a good fit for the
``watch_till_end`` variant: hook engineered to promise a specific
payoff at the end of the video, driving retention through the whole clip.

## Why this variant matters

YouTube Shorts + Instagram Reels rank videos on completion rate. A
compilation of "top 10 plays" with a generic hook ("These plays are
INSANE") gets a middle-of-the-list opening — viewers scroll after
seeing #1 or #2. A watch-till-end hook ("The #3 gets better every
time you watch it") creates a specific reason to hit the end.

Historical baseline: our compilation-type clips average ~35% completion.
Reels with a well-crafted retention hook average 60-75% completion (per
2026-07-17 audit synthesis). Same source content, dramatically
different signal to the algorithm.

## Selection criteria

1. **Duration 30-90s** — under 30s doesn't have room for setup+payoff;
   over 90s is beyond the short-form sweet spot.
2. **Title contains a compilation keyword** — "highlights", "compilation",
   "best of", "top N", "moments", "reactions", "clips", "montage",
   "recap". These identify content where the SOURCE has no built-in
   hook, so WE need to create the payoff-promise.
3. **NOT already series_part** — series takes priority (stronger algo
   signal). Enforced by calling ``detect_series()`` first.

## Contrast with series_part

- ``series_part``: detects an EXISTING property of the source (title
  says "Part 3"). Fires whenever detected.
- ``watch_till_end``: identifies content where WE need to invent the
  hook. Fires when the source has NO built-in hook + duration fits.

## What this doesn't do

- Doesn't measure ACTUAL video content for payoff-worthiness — that
  would require frame analysis. Trusts the title/duration proxies.
- Doesn't dynamically adjust criteria per niche. Future work: sports
  clips often have the "winning play at the end" structure regardless
  of title keywords; anime "top fights" always benefit.
- Doesn't handle bandit-driven variant selection. That's S5 — until
  then, this is a rule-based selector.
"""

from __future__ import annotations

import logging

from genlab_core.writing.series_detector import detect_series

logger = logging.getLogger(__name__)


# Duration bounds (inclusive). Under 30s: not enough runway. Over 90s:
# outside short-form sweet spot + retention naturally drops.
_MIN_DURATION_SECONDS = 30
_MAX_DURATION_SECONDS = 90

# Keywords that identify "compilation-type" content where the source has
# no built-in hook — WE need to create the payoff-promise. Lowercase
# comparison. Multi-word phrases (e.g. "best of") match as substrings.
_COMPILATION_KEYWORDS: tuple[str, ...] = (
    "highlights",
    "highlight ",  # "highlight reel" etc — with trailing space to avoid
    # matching "highlighted" or "highlighter"
    "compilation",
    "best of",
    "top ",  # "top 10", "top 5" — trailing space avoids "topic", "topple"
    "moments",
    "reactions",
    " clips",  # leading space avoids matching within longer words
    "montage",
    "recap",
    "reel",
    "supercut",
)


def _title_has_compilation_keyword(title: str) -> bool:
    """Case-insensitive substring match against compilation vocabulary.

    The keyword list uses leading/trailing spaces on ambiguous terms
    (``" clips"``, ``"top "``, ``"highlight "``) to avoid matching
    inside longer words that aren't compilations (``topple``, ``topic``,
    ``highlighted``, ``chipclips``).
    """
    lower = title.lower()
    return any(kw in lower for kw in _COMPILATION_KEYWORDS)


def is_watch_till_end_eligible(story: dict) -> bool:
    """Return True if the story is a good fit for watch_till_end variant.

    Called by both ``video_content_writer`` (for prompt injection) and
    ``push_to_backlog`` (for variant_type assignment). Deterministic
    given the story dict — never returns different results for the
    same input.

    Priority: if ``detect_series()`` fires on this story, return False
    unconditionally. Series_part is a stronger algorithmic signal;
    variants are exclusive on a blueprint.

    Fail-open: any exception (missing fields, type errors, series
    detector explosion) returns False. False = "no variant" = fall
    back to single_clip default. Never crashes the pipeline.
    """
    try:
        # Priority: series_part wins over watch_till_end. Same story
        # can't have both variant types (variant_type is exclusive).
        if detect_series(story) is not None:
            return False

        # Duration in range
        duration = story.get("duration_seconds")
        if duration is None:
            # Try alternate field names — different fetchers populate
            # different keys. YouTube uses `duration_seconds`; some
            # legacy paths use `duration` or `length`.
            duration = story.get("duration") or story.get("length")
        if duration is None:
            return False

        try:
            duration = float(duration)
        except (ValueError, TypeError):
            return False

        if not (_MIN_DURATION_SECONDS <= duration <= _MAX_DURATION_SECONDS):
            return False

        # Title has a compilation-type keyword
        title = story.get("title") or ""
        if not title:
            return False

        return _title_has_compilation_keyword(title)

    except Exception as exc:
        logger.debug(
            "[watch_till_end_selector] eligibility check failed for story=%s: %s",
            story.get("story_id", "<no-id>"),
            exc,
        )
        return False


def format_watch_till_end_prompt_section() -> str:
    """Return the writer prompt section for the watch_till_end variant.

    Injected into ``video_content_writer.write_video_content`` alongside
    ``series_context_hint`` / ``style_hint`` / ``content_angle_hint``.
    Uses the same "STYLE MANDATE"-shaped assertive framing that the
    existing style_hint uses — a mandate the LLM should follow strictly.

    Not parameterized on story fields because the writer already knows
    what the video is about from earlier prompt sections. This just
    reframes HOW the hook should be structured.
    """
    return (
        "\nWATCH-TILL-END MANDATE: This is compilation/reaction content\n"
        "  where the payoff is at the END of the video. Your hook must\n"
        "  PROMISE something specific worth waiting for — a claim,\n"
        "  mystery, or reveal that the viewer will only see if they\n"
        "  watch through to the end.\n"
        "\n"
        "  FRAMEWORKS (pick whichever fits the story naturally):\n"
        "    - COUNTDOWN: 'The #3 clip gets better every time you watch'\n"
        "    - SPECIFIC-TIMESTAMP: 'Wait for what happens at 0:47'\n"
        "    - RANKED-REVEAL: 'You won't see the actual best play until 0:32'\n"
        "    - SUPERLATIVE-DEFERRED: 'The last one changed how I see this'\n"
        "\n"
        "  AVOID:\n"
        "    - Front-loading the payoff (defeats the point)\n"
        "    - Vague teases without specifics ('You won't believe...')\n"
        "    - Generic curiosity-bait ('This is INSANE')\n"
        "\n"
        "  The hook must feel EARNED by the video's structure — if you\n"
        "  can't identify a genuine payoff moment worth waiting for,\n"
        "  return an empty hook to signal skip. Do NOT invent a\n"
        "  fictional payoff.\n"
    )


__all__ = [
    "format_watch_till_end_prompt_section",
    "is_watch_till_end_eligible",
]
