"""LLM-generated engagement questions for YouTube first-comment slot.

## Why this exists

Early comment activity is one of the top-3 algorithmic signals for
YouTube Shorts recommendation. A pinned first comment that INVITES
replies drives that activity — bare video posts get ~0.5% comment
rate, videos with a pinned engagement question hit 2-5%.

`monetization/cta_engine.py:531-545` already populates
`youtube_first_comment` when the blueprint has an affiliate product
attached (URL + product_name). But:

  * Only ~30-50% of blueprints have affiliate products
  * The other 50-70% ship with EMPTY `youtube_first_comment`
  * YouTube publish path checks `if payload.first_comment_text and
    video_id` (`platforms/youtube.py:550`) — empty means no pinned
    comment at all
  * That's a completely wasted algorithmic surface

## What this module does

Given a blueprint's already-generated content (hook, title, summary,
niche), calls Claude Haiku to produce ONE short question (30-100
chars) that a viewer might genuinely want to answer.

## Prompt discipline

Two failure modes we explicitly avoid in the system prompt:

  * "Engagement-bait" questions ("Do you agree?", "What do you
    think?") — YouTube's classifier down-ranks these as low-quality
  * Trivia questions with an obvious right answer — they don't
    invite genuine replies

Prompt frames the LLM as a "community manager who reads every
comment" and gives 2 concrete good/bad examples so the model has
a clear target shape.

## Cost

Claude Haiku 4.5, ~120 tokens per call, ~$0.00015/blueprint.
At 5 blueprints/day = ~$0.02/month. Trivial.

## Fail-open

Every failure path returns None:

  * Flag off (default)
  * No API key
  * Empty content context
  * LLM refusal / unparseable output
  * API exception

Never raises. Caller (cta_engine) leaves youtube_first_comment empty
— identical to pre-fix behavior.
"""

from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)

_HAIKU_MODEL: Final[str] = "claude-haiku-4-5-20251001"
_MAX_QUESTION_LEN: Final[int] = 200
"""YouTube comment cap is 10,000 chars; we constrain to 200 so the
question fits on a single mobile-view line and doesn't feel like
copy-paste boilerplate."""

_SYSTEM_PROMPT = """\
You are a community manager for a viral video channel. Every video's
pinned first comment is a question that invites replies.

Given a video's hook, title, and summary, write ONE short question
(30-150 characters) that:

* Asks about the viewer's experience or opinion on a SPECIFIC detail
  from the video (not a generic "what do you think?")
* Is answerable in one line — not open-ended-essay bait
* Feels like a genuine question a fan would ask, not clickbait
* Uses natural language — no exclamation marks, no all-caps

FAILURE MODES to avoid:

* Generic engagement bait: "Do you agree?", "What do you think?",
  "Comment your thoughts!" — YouTube's classifier down-ranks these
  as low-quality
* Trivia with an obvious right answer: "What year did this happen?"
  when the video already says the year
* Rhetorical questions that don't want an answer: "Isn't this
  amazing?"
* Questions that promote products or CTAs

GOOD EXAMPLES:

* Video about a game-winning shot: "which one was more surprising —
  the shot or the reaction from the bench?"
* Video about a movie trailer: "does the trailer make you want to
  see it or does it feel like it spoiled too much?"
* Video about an AI tool: "which use-case would you try first with
  this — writing, coding, or research?"

Respond with JSON ONLY:
  {"question": "<the question, 30-150 chars, ends with ?>"}

Nothing else. No preamble, no markdown fences.
"""


def _is_enabled() -> bool:
    """Env kill switch. Default OFF."""
    from genlab_core.settings import env_true

    return env_true("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED")


def _extract_json_object(raw: str) -> dict | None:
    """Locate the first JSON object, tolerating markdown fences."""
    import re

    text = raw.strip()
    if text.startswith("```"):
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


def generate_engagement_question(
    *,
    niche_id: str,
    hook: str = "",
    title: str = "",
    summary: str = "",
) -> str | None:
    """Generate a viewer-facing engagement question for a video's
    pinned first comment.

    Returns None on any failure (flag off, no API key, LLM refusal,
    unparseable output, question too short/long, doesn't end with ?).
    Never raises.
    """
    if not _is_enabled():
        return None

    if not any((hook, title, summary)):
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("[first_comment_question] anthropic package not installed")
        return None

    user_prompt = (
        f"NICHE: {niche_id}\n\n"
        f"HOOK: {hook[:200]}\n\n"
        f"TITLE: {title[:200]}\n\n"
        f"SUMMARY: {summary[:500]}\n\n"
        "Write the pinned first-comment question."
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
            max_tokens=200,
            temperature=0.7,  # some creative variance for organic feel
            system=with_prompt_cache(_SYSTEM_PROMPT),
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        logger.warning(
            "[first_comment_question] Anthropic call failed niche=%s: %s",
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
            "[first_comment_question] unparseable output niche=%s: %r",
            niche_id, raw[:200],
        )
        return None

    question = str(parsed.get("question", "")).strip()

    # Sanity checks — the LLM may occasionally return generic bait
    # despite the prompt. Reject bad shapes so we don't ship trash.
    if not question:
        return None
    if len(question) < 20:
        logger.debug(
            "[first_comment_question] rejected short question niche=%s: %r",
            niche_id, question,
        )
        return None
    if len(question) > _MAX_QUESTION_LEN:
        logger.debug(
            "[first_comment_question] rejected long question niche=%s len=%d",
            niche_id, len(question),
        )
        return None
    if not question.rstrip().endswith("?"):
        logger.debug(
            "[first_comment_question] rejected no-? question niche=%s: %r",
            niche_id, question,
        )
        return None

    # Reject the exact engagement-bait phrases we explicitly told the
    # LLM to avoid. Second-line-of-defense — the prompt should stop
    # these but temperature=0.7 means occasional slips.
    lower = question.lower()
    _BAD_PATTERNS: tuple[str, ...] = (
        "what do you think",
        "do you agree",
        "comment your thoughts",
        "let me know",
        "isn't this amazing",
        "isn't it amazing",
    )
    for pattern in _BAD_PATTERNS:
        if pattern in lower:
            logger.debug(
                "[first_comment_question] rejected bait-phrase niche=%s: %r",
                niche_id, question,
            )
            return None

    logger.info(
        "[first_comment_question] generated niche=%s len=%d question=%r",
        niche_id, len(question), question,
    )
    return question


__all__ = ["generate_engagement_question"]
