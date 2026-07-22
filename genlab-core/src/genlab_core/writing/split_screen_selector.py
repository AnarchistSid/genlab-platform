"""Split-screen variant selector — pure function, no I/O.

Layer 3 S6 (2026-07-22). Selects when a story is a good fit for the
``split_screen`` variant: content whose title signals a two-sided frame
("X vs Y", "before vs after", "reacting to X", "X compared to Y").

## Why this variant matters

Split-screen framing exploits a strong native attention pattern on
short-form video: comparison is inherently participatory. Viewers can't
help evaluating both sides. That kills the scroll-decision at the same
place question_reveal does, but via a different mechanism — split_screen
is comparative rather than resolutional.

The renderer at ``frame_compositor._compose_split_screen`` stacks two
video segments vertically (1080x960 each in the 1080x1920 target frame,
top and bottom labeled). If the pipeline only sourced one clip for the
story, the second slot mirrors the first — the writer's hook + labels
still deliver the two-sided cognitive frame even without a genuine
paired source.

## Selection criteria

1. **Duration 15-90s** — under 15s: no time for setup + comparison.
   Over 90s: attention drops between the two halves.
2. **Title contains an explicit comparison signal**:
   - "X vs Y" / "X vs. Y" (with word boundaries so "elvs" doesn't match)
   - "before" + "after" (any order, within ~30 chars of each other)
   - "reacting to" / "reaction to" / "reacts to"
   - "compared to" / "vs"
3. **NOT already series_part / question_reveal / watch_till_end** —
   split_screen has LOWER priority than the three variants that ship
   as writer+wire pairs.

## Priority position

Below question_reveal + watch_till_end because those have earlier
mechanistic signals (explicit questions / compilation words). Above
single_clip default. Wire priority chain enforced at push_to_backlog.

## What this doesn't do

- Doesn't source a genuine paired clip. The variant_payload uses
  ``clip_b_video_id == clip_a_video_id`` as a self-reference — the
  compositor renders the same underlying video with different
  labels/positions to create the split-screen effect. A future
  pair-fetcher can populate a real ``clip_b`` from the same niche's
  content_pool.
- Doesn't yet feed labels back into the compositor via variant_payload
  — the FrameCompositor uses generic left/right defaults for now. When
  the writer emits explicit ``left_label`` + ``right_label`` from the
  LLM output, S6b payload will grow.
"""

from __future__ import annotations

import logging
import re

from genlab_core.writing.question_reveal_selector import is_question_reveal_eligible
from genlab_core.writing.series_detector import detect_series
from genlab_core.writing.watch_till_end_selector import is_watch_till_end_eligible

logger = logging.getLogger(__name__)


_MIN_DURATION_SECONDS = 15
_MAX_DURATION_SECONDS = 90

# Comparison-signal patterns. Anchored to word boundaries so partial
# matches don't fire (e.g. "elvs" doesn't match "vs"; "reverse" doesn't
# match "reacts"). Case-insensitive.
#
# 2026-07-22: `\bvs\b` catches "vs" and "vs." (period is a non-word char
# so \b terminates the match before the period).
_VS_PATTERN = re.compile(r"\bvs\.?\b", re.IGNORECASE)
_REACTION_PATTERN = re.compile(
    r"\b(reacting to|reaction to|reacts to|reaction:)\b", re.IGNORECASE
)
_COMPARISON_PATTERN = re.compile(r"\bcompared to\b", re.IGNORECASE)
# "before" and "after" within 40 chars of each other on the same title.
# Order matters less than proximity — creators write "before-and-after"
# and "after / before" both.
_BEFORE_AFTER_PATTERN = re.compile(
    r"\b(before)\b[^.!?]{0,40}\b(after)\b|\b(after)\b[^.!?]{0,40}\b(before)\b",
    re.IGNORECASE,
)


def _has_split_screen_signal(title: str) -> bool:
    """True if the title carries any of the split-screen comparison signals."""
    if _VS_PATTERN.search(title):
        return True
    if _REACTION_PATTERN.search(title):
        return True
    if _COMPARISON_PATTERN.search(title):
        return True
    if _BEFORE_AFTER_PATTERN.search(title):
        return True
    return False


def is_split_screen_eligible(story: dict) -> bool:
    """Return True if the story is a good fit for split_screen variant.

    Called by both writer (for prompt injection) and push_to_backlog
    (for variant_type assignment). Deterministic; never returns
    different results for the same input.

    Priority: series_part > question_reveal > watch_till_end > split_screen.
    All three higher-priority selectors are checked first — if any fires,
    split_screen is not eligible.

    Fail-open: any exception returns False (safe default = single_clip).
    """
    try:
        # Priority chain — split_screen only fires when no higher variant matches.
        if detect_series(story) is not None:
            return False
        if is_question_reveal_eligible(story):
            return False
        if is_watch_till_end_eligible(story):
            return False

        # Duration bounds
        duration = story.get("duration_seconds")
        if duration is None:
            duration = story.get("duration") or story.get("length")
        if duration is None:
            return False
        try:
            duration = float(duration)
        except (ValueError, TypeError):
            return False
        if not (_MIN_DURATION_SECONDS <= duration <= _MAX_DURATION_SECONDS):
            return False

        # Title must be present + shaped as a comparison
        title = story.get("title") or ""
        if not title:
            return False

        return _has_split_screen_signal(title)

    except Exception as exc:
        logger.debug(
            "[split_screen_selector] eligibility check failed for story=%s: %s",
            story.get("story_id", "<no-id>"),
            exc,
        )
        return False


def build_split_screen_payload(story: dict) -> dict:
    """Return the ``variant_payload`` JSONB for a split_screen blueprint.

    Populates the PAYLOAD_CONTRACTS-required fields:
    ``clip_a_video_id`` + ``clip_b_video_id``. When the pipeline has
    sourced only one clip (the current default), both slots reference
    the SAME video_id — the compositor detects the self-reference and
    renders a mirrored / positionally-differentiated frame rather than
    playing the same content on both halves identically.

    Also populates optional fields the compositor can use:
    - ``left_label`` / ``right_label`` — text overlays for the two
      halves. Defaults to "BEFORE" / "AFTER" derived from the title's
      comparison shape. Writer can override via its JSON output.
    - ``layout`` — "vstack" (default) or "hstack". VStack keeps each
      half at 1080x960 in the 1080x1920 portrait target; hstack
      compresses each to 540x1920 which is too narrow to be readable.

    Empty story or missing video_id: returns an empty dict — caller
    (push_to_backlog wire) treats missing required keys as reason to
    fall back to single_clip.
    """
    video_id = story.get("video_id") or ""
    if not video_id:
        return {}

    title = (story.get("title") or "").lower()
    if "before" in title and "after" in title:
        left_label, right_label = "BEFORE", "AFTER"
    elif _REACTION_PATTERN.search(title):
        left_label, right_label = "REACTING", "SOURCE"
    else:
        left_label, right_label = "A", "B"

    return {
        "clip_a_video_id": video_id,
        "clip_b_video_id": video_id,  # self-reference — compositor handles
        "left_label": left_label,
        "right_label": right_label,
        "layout": "vstack",
    }


def format_split_screen_prompt_section() -> str:
    """Return the writer prompt section for the split_screen variant.

    Injected into ``video_content_writer.write_video_content`` when the
    story is split_screen-eligible. Steers the LLM to craft a hook that
    LEANS INTO the comparison signal instead of collapsing it to a
    single narrative.
    """
    return (
        "\nSPLIT-SCREEN MANDATE: This video's source title signals an\n"
        "  explicit comparison — X vs Y, before/after, or reaction to a\n"
        "  paired source. The render will show TWO halves (top + bottom)\n"
        "  of a 1080x1920 frame, and your hook needs to earn that framing.\n"
        "\n"
        "  FRAMEWORKS:\n"
        "    - HEAD-TO-HEAD: 'iPhone 17 vs Samsung S26: this is embarrassing'\n"
        "    - TRANSFORMATION: 'The before + after nobody predicted'\n"
        "    - REACTION: 'This tech reviewer is losing it in real-time'\n"
        "    - PARADOX: 'One paid version, one free — same result'\n"
        "\n"
        "  RULES:\n"
        "    - Hook must POINT to both sides — not describe just one.\n"
        "    - Must reference something SPECIFIC that distinguishes the two.\n"
        "    - Both sides must be genuinely comparable — don't force it\n"
        "      onto a source that's just one thing with commentary.\n"
        "\n"
        "  AVOID:\n"
        "    - 'You have to see this' vague setup (doesn't earn split frame)\n"
        "    - Comparisons the video doesn't actually deliver on\n"
        "    - Falling back to single-clip narrative — if the source is\n"
        "      genuinely one-sided, return an empty hook to signal skip.\n"
        "\n"
        "  Your caption body should NAME both sides so viewers know what\n"
        "  they're comparing before the split frame lands.\n"
    )


__all__ = [
    "build_split_screen_payload",
    "format_split_screen_prompt_section",
    "is_split_screen_eligible",
]
