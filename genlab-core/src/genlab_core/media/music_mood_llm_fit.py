"""Content-tone → music-mood matcher via LLM.

Motivation: the music_mood bandit picks a mood based on posterior
reward per mood arm. That's a POST-HOC signal — the bandit only
knows a mood worked AFTER we shipped and got engagement. Cold-start
+ per-mood-per-niche means many arms have thin posteriors.

This module adds a PRE-SELECTION signal: given a story's hook + title
+ summary, an LLM classifies the content's emotional tone and
suggests the best-fit moods from the niche's declared music library.

## Consumer wire (deferred to follow-up)

Called from ``transformation_selector.select_transformation_dimensions``
for the music_mood dimension. Two integration policies possible:

1. **Steer-and-log** — LLM suggestion becomes a tie-break when
   bandit posteriors are close (within 10% of each other). Log both
   for accountability.
2. **Prior seed** — LLM suggestion boosts alpha += 0.5 for the
   suggested mood at selection time (temporary boost, not persisted
   to bandit_arms).

Ship policy tonight: STANDALONE MODULE ONLY. No selector integration
until operator sees the LLM's suggestions on real blueprints for
a few days and validates the prompt.

## Cost

Claude Haiku 4.5, ~150 tokens per call, ~$0.0002 per blueprint.
At 5 blueprints/day = $0.03/month. Trivial.

## Fail-open

Any error (no API key, network, LLM refusal, unparseable output)
returns None. Caller falls back to bandit-only selection —
identical to pre-fix behavior.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

_HAIKU_MODEL: Final[str] = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class MoodSuggestion:
    """LLM's suggestion for the best-fit music mood.

    Fields
    ------
    top_mood : str
        The single best-fit mood from the available set.
    reasoning : str
        Short (< 100 char) explanation. For operator visibility.
    confidence : float
        LLM's self-reported confidence 0.0-1.0.
    """

    top_mood: str
    reasoning: str
    confidence: float


_SYSTEM_PROMPT = """\
You are the music director for a viral video content pipeline.

Given a story's hook, title, and summary, pick the ONE music mood
from the available list that best fits the content's emotional tone.

Music-content mismatch is one of the biggest failure modes for
short-form video — dramatic anime fight scene with whimsical music
gets skipped; sports news with romantic music reads as parody.

Selection criteria (in order):
1. Emotional tone match (dramatic content -> dramatic mood)
2. Tempo match (fast-cut action -> high-energy mood)
3. Genre convention (sports highlights typically hype/aggressive;
   trailer reveals typically cinematic/mysterious)

Respond with JSON ONLY:
  {"mood": "<exact_mood_from_available_list>",
   "reasoning": "<one-sentence justification, <100 chars>",
   "confidence": <0.0-1.0>}

Nothing else. No preamble, no markdown fences.
"""


def _is_llm_enabled() -> bool:
    """Read env flag. Default OFF so importing this module is a no-op
    for callers not yet ready to consume."""
    from genlab_core.settings import env_true

    return env_true("GENLAB_MUSIC_MOOD_LLM_FIT_ENABLED")


def _extract_json_object(raw: str) -> dict | None:
    """Locate the first JSON object in an LLM response. Tolerates
    markdown fences that a well-instructed LLM should not emit but
    sometimes does anyway."""
    text = raw.strip()
    if text.startswith("```"):
        # Drop opening fence (with optional lang tag) + closing fence
        lines = text.split("\n")
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1])
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    import json as _json

    try:
        obj = _json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except (ValueError, _json.JSONDecodeError):
        return None


def suggest_mood(
    niche_id: str,
    hook: str,
    title: str,
    summary: str,
    available_moods: list[str],
) -> MoodSuggestion | None:
    """Ask an LLM to pick the best-fit music mood from a set.

    Returns None on any failure (flag off, no API key, LLM refusal,
    unparseable output, mood not in available list). Never raises.
    """
    if not _is_llm_enabled():
        return None

    if not available_moods:
        return None

    if not any((hook, title, summary)):
        # No content context — nothing to classify. Cheaper to skip
        # than to pay for an LLM call with no signal.
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("[music_mood_llm_fit] anthropic package not installed")
        return None

    user_prompt = (
        f"NICHE: {niche_id}\n\n"
        f"HOOK: {hook[:200]}\n\n"
        f"TITLE: {title[:200]}\n\n"
        f"SUMMARY: {summary[:500]}\n\n"
        f"AVAILABLE MOODS: {', '.join(available_moods)}\n\n"
        "Pick the best-fit mood."
    )

    try:
        try:
            from genlab_core.llm.cache import with_prompt_cache
        except ImportError:
            def with_prompt_cache(x: str) -> str:  # type: ignore[misc]
                return x

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=150,
            temperature=0.0,
            system=with_prompt_cache(_SYSTEM_PROMPT),
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        logger.warning(
            "[music_mood_llm_fit] Anthropic call failed for niche=%s: %s",
            niche_id, exc,
        )
        return None

    try:
        from genlab_core.intelligence.cost_accumulator import (
            record_anthropic_usage,
        )
        record_anthropic_usage(_HAIKU_MODEL, response)
    except Exception:
        pass

    raw = response.content[0].text.strip() if response.content else ""
    parsed = _extract_json_object(raw)
    if not parsed:
        logger.warning(
            "[music_mood_llm_fit] unparseable LLM output for niche=%s: %r",
            niche_id, raw[:200],
        )
        return None

    mood = str(parsed.get("mood", "")).strip()
    reasoning = str(parsed.get("reasoning", "")).strip()[:120]
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    if mood not in available_moods:
        logger.warning(
            "[music_mood_llm_fit] LLM picked mood=%r not in available=%r",
            mood, available_moods,
        )
        return None

    logger.info(
        "[music_mood_llm_fit] niche=%s picked=%s conf=%.2f",
        niche_id, mood, confidence,
    )
    return MoodSuggestion(
        top_mood=mood,
        reasoning=reasoning or "no_reasoning",
        confidence=confidence,
    )


__all__ = ["MoodSuggestion", "suggest_mood"]
