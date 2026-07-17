"""Question-reveal variant selector — pure function, no I/O.

Layer 3 S4a (2026-07-17, writer-only). Selects when a story is a good
fit for the ``question_reveal`` variant: a hook shaped as an EXPLICIT
QUESTION that the video's payoff answers.

## Why this variant matters

Question-shaped hooks create a cognitive commitment — viewers who read
a specific question ("How did Curry hit that shot?") feel a small itch
to know the answer. That itch drives them past the first 3-5 second
scroll-decision window that determines whether a Short is watched at
all. YouTube's own Creator Insider has cited "question hooks" as one
of the highest-completion patterns for Shorts specifically.

## Selection criteria

1. **Duration 30-90s** — same short-form sweet spot as watch_till_end.
   Under 30s: no room for question setup + payoff. Over 90s: attention
   drops before reveal.
2. **Title starts with a question word** — Why / How / What / When /
   Where / Who / Can / Does / Is / Are — followed by word boundary.
3. **Title ends with a question mark** — explicit signal that the
   creator intended this as a question format. Ambiguous "How X"
   titles without "?" often aren't real questions.
4. **NOT already series_part** — series takes priority (stronger algo
   signal). Enforced by calling ``detect_series()`` first.

## Priority position

Question_reveal has a MORE specific trigger than watch_till_end
(structured question format vs compilation keyword). Priority chain:
``series_part > question_reveal > watch_till_end > single_clip``.

## What this doesn't do (deferred to S4b)

- Doesn't produce a separate REVEAL text field. The writer's HOOK
  becomes the question; the reveal-timing overlay is compositor
  work that requires ffmpeg drawtext with `enable='between(t,X,Y)'`.
- Doesn't yet steer the LLM to structure the video's captions around
  the payoff. That's a richer prompt change dependent on S4b's
  reveal-field contract.

For now: this selector + writer prompt injection produces a
question-shaped HOOK. Reward signal will still tell us if Q&A framing
outperforms baseline — even without the delayed reveal overlay.
"""

from __future__ import annotations

import logging
import re

from genlab_core.writing.series_detector import detect_series

logger = logging.getLogger(__name__)


_MIN_DURATION_SECONDS = 30
_MAX_DURATION_SECONDS = 90

# Question-word prefix: title must START with one of these + word boundary.
# Anchored with ^\s* to allow leading whitespace but nothing else.
_QUESTION_PREFIX = re.compile(
    r"^\s*(why|how|what|when|where|who|can|does|is|are|will|should)\b",
    re.IGNORECASE,
)

# Title must end with "?" (after optional trailing whitespace). Explicit
# creator intent that this is a question.
_TRAILING_QUESTION_MARK = re.compile(r"\?\s*$")


def is_question_reveal_eligible(story: dict) -> bool:
    """Return True if the story is a good fit for question_reveal variant.

    Called by both writer (for prompt injection) and push_to_backlog
    (for variant_type assignment). Deterministic; never returns
    different results for the same input.

    Priority: series_part wins unconditionally. Question_reveal wins
    over watch_till_end (checked at wire-level, not here).

    Fail-open: any exception returns False (safe default = single_clip).
    """
    try:
        # Priority: series > question_reveal
        if detect_series(story) is not None:
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

        # Title must be present + shaped as a question
        title = story.get("title") or ""
        if not title:
            return False

        if not _QUESTION_PREFIX.match(title):
            return False
        if not _TRAILING_QUESTION_MARK.search(title):
            return False

        return True

    except Exception as exc:
        logger.debug(
            "[question_reveal_selector] eligibility check failed for story=%s: %s",
            story.get("story_id", "<no-id>"),
            exc,
        )
        return False


def format_question_reveal_prompt_section() -> str:
    """Return the writer prompt section for the question_reveal variant.

    Injected into ``video_content_writer.write_video_content`` alongside
    the other MANDATE-shaped hints. Steers the LLM to craft a hook that
    is an EXPLICIT question the video answers — leaning into the source
    title's own question-format signal.

    Not yet parameterized on story fields. When S4b lands the compositor
    timed-text overlay, this section will grow to require a separate
    ``reveal`` field in the JSON output. For now: hook is the question,
    caption body is the reveal.
    """
    return (
        "\nQUESTION-REVEAL MANDATE: This video's source title poses an\n"
        "  explicit question that the payoff answers. Your hook MUST be\n"
        "  shaped as the same question — a specific, pointed question a\n"
        "  viewer would need to hit play to answer.\n"
        "\n"
        "  FRAMEWORKS:\n"
        "    - MYSTERY: 'How did Curry hit that shot from 40 feet?'\n"
        "    - PARADOX: 'Why is Anthropic locking their own AI in a vault?'\n"
        "    - MECHANISM: 'What actually makes this attack unblockable?'\n"
        "    - CONFLICT: 'Who really decided to shelve LG's rollable phone?'\n"
        "\n"
        "  RULES:\n"
        "    - Must end with '?' — literal question, not implied.\n"
        "    - Must reference something SPECIFIC from the video, not vague.\n"
        "    - The answer must actually be IN the video — don't tease\n"
        "      information the viewer won't get.\n"
        "\n"
        "  AVOID:\n"
        "    - Rhetorical questions with obvious answers ('Is this insane?')\n"
        "    - Yes/no framing (answered without watching)\n"
        "    - 'You won't believe why...' clickbait\n"
        "\n"
        "  Your caption body should tease HOW the answer unfolds without\n"
        "  giving it away — the video is the reveal. If the source doesn't\n"
        "  actually deliver on a specific answerable question, return an\n"
        "  empty hook to signal skip.\n"
    )


__all__ = [
    "format_question_reveal_prompt_section",
    "is_question_reveal_eligible",
]
