"""Storytime variant selector — pure function, no I/O.

Layer 3 S7 (2026-07-22, writer-only phase). Selects when a story has a
narrative arc shape that benefits from the storytime frame: setup +
escalation + payoff over a longer video window than the punchy hooks
watch_till_end / question_reveal target.

## Why this variant matters

Storytime content commits viewers to a full-video listen — audience sits
through the whole thing for the arc, unlike compilation content where
retention is measured moment-by-moment. Different reward shape than the
other four variants. Best-in-class creators (StoryCorps, "This American
Life"-style YT edits) show that 60-90s storytime formats can outperform
their variant peers on completion rate + reshare rate when the source
material genuinely IS narrative.

## Selection criteria

1. **Duration 60-120s** — narrative needs setup + escalation + payoff.
   Under 60s: no room for the arc structure. Over 120s: exits short-form
   platform sweet spot.
2. **Title carries a narrative signal**:
   - "the story of X" / "how X happened" / "why X changed"
   - "this is what happened when..." / "what really happened to X"
   - "the day X" / "the moment X" / "the time I / we / he / she"
3. **NOT already series_part / question_reveal / watch_till_end /
   split_screen** — storytime sits at the bottom of the priority chain
   above single_clip default.

## Priority position

Bottom of the chain because narrative-signal titles overlap heavily
with the higher variants' patterns. A "How did X happen?" title
correctly routes to question_reveal (structural question format wins).
"The story of the Top 10..." routes to watch_till_end. Storytime only
fires when NONE of the higher patterns match AND the title carries a
narrative arc marker.

## What this doesn't do (phase E deferred)

Phase E compositor work is genuinely 2-3h of new render code (TTS audio
generation via ElevenLabs cascade, timed word overlays layered on the
base composite, whisper_sync used as the timing source). That renderer
is enough scope to deserve fresh session judgment. This slice ships
selector + writer + wire so the tomorrow-morning fires start collecting
narrative-signal data for the eventual compositor calibration.
"""

from __future__ import annotations

import logging
import re

from genlab_core.writing.question_reveal_selector import is_question_reveal_eligible
from genlab_core.writing.series_detector import detect_series
from genlab_core.writing.split_screen_selector import is_split_screen_eligible
from genlab_core.writing.watch_till_end_selector import is_watch_till_end_eligible

logger = logging.getLogger(__name__)


_MIN_DURATION_SECONDS = 60
_MAX_DURATION_SECONDS = 120

# Narrative-signal patterns. Anchored with word boundaries so partial
# matches don't fire. Case-insensitive.
#
# Phrasal patterns (multi-word narrative openers):
#   - "the story of X"
#   - "how X happened" / "how X started" / "how X ended"
#   - "why X changed" / "why X failed" / "why X worked"
#   - "the day X" / "the moment X" / "the time I|we|he|she"
#   - "what really happened" / "what happened when"
_NARRATIVE_PATTERNS = re.compile(
    r"\b("
    r"the story of|"
    r"how .{2,40} (happened|started|ended|began|began)|"
    r"why .{2,40} (changed|failed|worked|stopped|died)|"
    r"the day \w+|"
    r"the moment \w+|"
    r"the time (i|we|he|she|they) |"
    r"what really happened|"
    r"what happened when|"
    r"this is what happened|"
    r"the truth about"
    r")\b",
    re.IGNORECASE,
)


def _has_storytime_signal(title: str) -> bool:
    """True if the title carries any of the narrative-arc signals."""
    return bool(_NARRATIVE_PATTERNS.search(title))


def is_storytime_eligible(story: dict) -> bool:
    """Return True if the story is a good fit for storytime variant.

    Called by both writer (for prompt injection) and push_to_backlog
    (for variant_type assignment). Deterministic; never returns
    different results for the same input.

    Priority: series_part > question_reveal > watch_till_end >
    split_screen > storytime > single_clip. All 4 higher-priority
    selectors checked first — if any fires, storytime is not eligible.

    Fail-open: any exception returns False (safe default = single_clip).
    """
    try:
        # Priority chain — storytime only fires when no higher variant matches.
        if detect_series(story) is not None:
            return False
        if is_question_reveal_eligible(story):
            return False
        if is_watch_till_end_eligible(story):
            return False
        if is_split_screen_eligible(story):
            return False

        # Duration bounds — narrative needs setup + escalation + payoff.
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

        # Title must be present + carry a narrative-arc marker.
        title = story.get("title") or ""
        if not title:
            return False

        return _has_storytime_signal(title)

    except Exception as exc:
        logger.debug(
            "[storytime_selector] eligibility check failed for story=%s: %s",
            story.get("story_id", "<no-id>"),
            exc,
        )
        return False


def build_storytime_payload(story: dict) -> dict:
    """Return the ``variant_payload`` JSONB for a storytime blueprint.

    The PAYLOAD_CONTRACTS-required field is ``narration_text``. Phase E
    compositor will consume this to drive TTS audio generation and
    whisper-timed word overlays. For the writer-only slice (this file),
    we seed with the story's summary/description_snippet — the writer's
    JSON output can override via a top-level ``narration_text`` field
    when the LLM produces one.

    Optional field ``tts_provider`` documented by PAYLOAD_CONTRACTS is
    populated by the compositor at render time (chooses from the
    ElevenLabs → OpenAI TTS → Edge-TTS → gTTS cascade per CLAUDE.md).

    Empty story or missing narration source: returns an empty dict —
    push_to_backlog wire treats missing required keys as reason to fall
    back to single_clip.
    """
    narration = (
        story.get("narration_text")
        or story.get("summary")
        or story.get("description_snippet")
        or ""
    )
    if not narration or len(narration.strip()) < 40:
        return {}

    return {
        "narration_text": narration.strip()[:1500],
        # tts_provider left unset — compositor picks at render time.
    }


def format_storytime_prompt_section() -> str:
    """Return the writer prompt section for the storytime variant.

    Injected into ``video_content_writer.write_video_content`` when the
    story is storytime-eligible. Steers the LLM to draft content that
    respects the narrative arc rather than collapsing to a single hook.
    """
    return (
        "\nSTORYTIME MANDATE: This video's source title signals a narrative\n"
        "  arc (a specific event, moment, or transformation). The compositor\n"
        "  will render TTS narration + timed word captions over the source\n"
        "  clip — your hook + caption need to earn a 60-120s watch commitment,\n"
        "  not just a 3-second scroll-decision.\n"
        "\n"
        "  FRAMEWORKS:\n"
        "    - ORIGIN: 'The day GitHub Copilot changed how devs write code'\n"
        "    - REVELATION: 'What really happened when OpenAI paused GPT-5'\n"
        "    - TRANSFORMATION: 'How this indie studio went from 0 to 1M in 90 days'\n"
        "    - REVERSAL: 'The moment I stopped trusting my own AI stack'\n"
        "\n"
        "  RULES:\n"
        "    - Hook must promise the ARC, not the answer. Tease the setup so\n"
        "      viewers stay for the escalation.\n"
        "    - Caption must lay out the beats — early setup, mid-video\n"
        "      escalation, payoff at the end. Don't spoil the payoff in the\n"
        "      caption body.\n"
        "    - Narrative details must be SPECIFIC to the source video —\n"
        "      generic advice or listicles don't earn the storytime frame.\n"
        "\n"
        "  AVOID:\n"
        "    - Compilation-style listicles (those belong in watch_till_end)\n"
        "    - Explicit question hooks (those belong in question_reveal)\n"
        "    - Reaction / comparison framings (those belong in split_screen)\n"
        "    - Payoff-in-the-hook clickbait ('here's what happened: X!')\n"
        "\n"
        "  OUTPUT: If the writer JSON schema is extended in the future,\n"
        "  include a top-level ``narration_text`` field with the story's\n"
        "  narrative beats — phase E compositor will feed it to TTS.\n"
    )


__all__ = [
    "build_storytime_payload",
    "format_storytime_prompt_section",
    "is_storytime_eligible",
]
