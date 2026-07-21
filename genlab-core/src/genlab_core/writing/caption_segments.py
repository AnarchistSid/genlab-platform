"""Structured caption-segment generation for the animated caption overlay.

Extends the writer output with a per-reel structured JSON: a list of
2-8 word segments plus 1-2 emphasis words per segment. Consumed by
PR 11's caption animator to produce word-by-word reveals with
color-pop emphasis.

Runs AFTER the hook generator (llm_hook_generator.generate_hook) —
takes the selected hook as input so segments extend rather than
duplicate the hook. Same fail-open pattern as generate_hook: returns
None when ANTHROPIC_API_KEY missing / api call fails / json unparseable.
Caller falls back to hook-only reel.

Output schema:

    {
      "segments": [
        {"text": "OpenAI just dropped", "emphasis_words": ["OpenAI"]},
        {"text": "the fastest AI video model", "emphasis_words": ["fastest"]},
        {"text": "and it's completely FREE", "emphasis_words": ["FREE"]}
      ],
      "hashtags": ["#ai", "#openai", "#creators"]
    }

Segment rules enforced at parse time:
  * 3-5 segments per reel (min 1 to avoid empty caption)
  * Each segment 2-8 words (per 2026 short-form research)
  * emphasis_words is a subset of the segment's own words
  * hashtags: 3-5 items, all start with '#'

Rules ENFORCEMENT is deliberately lenient — the LLM occasionally
returns slight violations (5 emphasis words when we asked for 2). The
parser clamps + trims rather than rejecting the whole payload.

Related sprint context:
- PR 11 (upcoming) — caption_animator consumes these segments
- Bandit dimension ``caption_style`` picks reveal style (word-by-word,
  hormozi_yellow, etc.) — orthogonal to segment content
- ``caption_emphasis_color`` arm picks the color for emphasis words
- ``caption_pacing`` arm picks ms per segment
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


_MIN_SEGMENTS = 1
_MAX_SEGMENTS = 5
_MIN_SEGMENT_WORDS = 2
_MAX_SEGMENT_WORDS = 8
_MAX_EMPHASIS_WORDS = 2
_MIN_HASHTAGS = 0  # LLM can return no hashtags; caller decides fallback
_MAX_HASHTAGS = 5


class CaptionSegment(BaseModel):
    """One caption chunk to reveal at a time.

    ``text`` is the raw chunk (2-8 words).
    ``emphasis_words`` are the 1-2 chunk-internal words to color-pop.
    """

    text: str = Field(min_length=1, max_length=120)
    emphasis_words: list[str] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _text_stripped(cls, v: str) -> str:
        return v.strip()

    @field_validator("emphasis_words")
    @classmethod
    def _emphasis_words_stripped(cls, v: list[str]) -> list[str]:
        # Preserve LLM order; strip whitespace; drop empties.
        out: list[str] = []
        for w in v:
            s = (w or "").strip()
            if s:
                out.append(s)
        return out

    def word_count(self) -> int:
        return len(self.text.split())


class CaptionSegmentsResult(BaseModel):
    """Parsed writer output for the caption animator.

    All fields optional so partial LLM output still validates. Caller
    checks ``is_usable()`` before consuming.
    """

    segments: list[CaptionSegment] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)

    def is_usable(self) -> bool:
        return _MIN_SEGMENTS <= len(self.segments) <= _MAX_SEGMENTS

    def total_words(self) -> int:
        return sum(s.word_count() for s in self.segments)


def _prompt_for_segments(niche_id: str, title: str, summary: str, hook_text: str) -> str:
    """Build the LLM system+user prompt for segment generation.

    Kept as a pure function so tests can pin the prompt shape without
    calling the API.
    """
    return f"""You are a short-form video captioning assistant for the "{niche_id}" niche.

Given a trending video's title, summary, and the first-frame hook that's
already selected, break the commentary into 3-5 short caption segments
for word-by-word on-screen reveal.

Video title: {title}
Video summary: {summary[:300]}
Hook (first frame, already displayed): "{hook_text}"

Output STRICTLY as a JSON object with this exact shape:
{{
  "segments": [
    {{"text": "2-8 word chunk", "emphasis_words": ["1-2 words"]}},
    {{"text": "2-8 word chunk", "emphasis_words": ["1-2 words"]}},
    {{"text": "2-8 word chunk", "emphasis_words": ["1-2 words"]}}
  ],
  "hashtags": ["#tag1", "#tag2", "#tag3"]
}}

Rules:
- 3 to 5 segments total (never fewer than 3)
- Each segment: 2 to 8 words
- Emphasis words: 1 or 2 punchy words per segment (must be words that
  actually appear in that segment's text)
- Do NOT repeat the hook text in the segments
- Match the {niche_id} niche's tone — the segments continue the hook
- Total reading time should fit 15-25 seconds (comfortable pacing)
- Hashtags: 3-5 items, each starts with '#'

Return ONLY the JSON object — no preamble, no code fence, no commentary."""


def _extract_json_blob(text: str) -> str | None:
    """Extract the first {...} JSON blob from LLM output.

    Handles the common cases where the LLM adds preamble text or wraps
    the JSON in a ```json code fence despite instructions.
    """
    if not text:
        return None
    # Strip common code-fence wrappers
    stripped = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", stripped, re.DOTALL)
    if m:
        return m.group(1)
    # Fall back to the first { .. } outermost blob
    start = stripped.find("{")
    if start == -1:
        return None
    # Naive brace matching — sufficient for LLM outputs that don't
    # contain quoted strings with escaped braces.
    depth = 0
    for i, ch in enumerate(stripped[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    return None


def _clamp_segments(
    segments: list[CaptionSegment],
) -> list[CaptionSegment]:
    """Enforce word-count + emphasis-word rules from raw LLM output.

    Lenient enforcement: LLM occasionally returns 10-word segments or
    5 emphasis words. We clamp rather than reject the whole payload —
    partial output is more useful than nothing.
    """
    out: list[CaptionSegment] = []
    for seg in segments[:_MAX_SEGMENTS]:
        # Word-count clamp — cut to _MAX_SEGMENT_WORDS if needed.
        words = seg.text.split()
        if len(words) < _MIN_SEGMENT_WORDS:
            # Too short → drop this segment
            continue
        if len(words) > _MAX_SEGMENT_WORDS:
            words = words[:_MAX_SEGMENT_WORDS]
            seg = CaptionSegment(
                text=" ".join(words),
                emphasis_words=seg.emphasis_words,
            )

        # Emphasis-words clamp: at most _MAX_EMPHASIS_WORDS, and every
        # emphasis word must appear in the segment (case-insensitive).
        lower_words = {w.lower().strip(",.!?;:") for w in words}
        valid_emphasis: list[str] = []
        for e in seg.emphasis_words:
            core = e.lower().strip(",.!?;:")
            if core and core in lower_words:
                valid_emphasis.append(e)
            if len(valid_emphasis) >= _MAX_EMPHASIS_WORDS:
                break
        seg = CaptionSegment(text=seg.text, emphasis_words=valid_emphasis)
        out.append(seg)
    return out


def _clamp_hashtags(hashtags: list[str]) -> list[str]:
    """Drop malformed hashtags, cap at _MAX_HASHTAGS.

    Normalizes to exactly one leading '#' (so '##ai' → '#ai').
    """
    out: list[str] = []
    for h in hashtags:
        s = (h or "").strip()
        if not s:
            continue
        # Strip any number of leading '#' then re-add exactly one.
        core = s.lstrip("#")
        if not core:
            continue
        s = "#" + core
        out.append(s)
        if len(out) >= _MAX_HASHTAGS:
            break
    return out


def parse_llm_response(raw_text: str) -> CaptionSegmentsResult | None:
    """Parse LLM JSON output into a validated result.

    Returns None when:
      * No JSON blob found in the response
      * JSON is malformed
      * Payload doesn't have expected keys OR ends up unusable after
        clamping

    Kept public so PR 11 can round-trip tests use canned LLM outputs.
    """
    blob = _extract_json_blob(raw_text)
    if not blob:
        logger.debug("[caption_segments] no JSON blob in LLM response")
        return None

    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        logger.debug("[caption_segments] JSON parse failed: %s", exc)
        return None

    if not isinstance(data, dict):
        return None

    raw_segments = data.get("segments") or []
    parsed_segments: list[CaptionSegment] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text", "")
        emphasis = raw.get("emphasis_words") or []
        if not isinstance(text, str) or not isinstance(emphasis, list):
            continue
        try:
            parsed_segments.append(CaptionSegment(text=text, emphasis_words=emphasis))
        except Exception as exc:
            logger.debug("[caption_segments] segment validation failed: %s", exc)

    clamped = _clamp_segments(parsed_segments)
    hashtags = _clamp_hashtags(data.get("hashtags") or [])

    result = CaptionSegmentsResult(segments=clamped, hashtags=hashtags)
    if not result.is_usable():
        logger.debug(
            "[caption_segments] parsed but unusable: %d segments",
            len(result.segments),
        )
        return None
    return result


def generate_caption_segments(
    story: dict[str, Any],
    niche_id: str,
    hook_text: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
) -> CaptionSegmentsResult | None:
    """Ask Claude to break commentary into caption segments.

    Returns None on: missing API key, anthropic not installed, API
    error, unparseable response, or LLM output that fails the clamp
    checks. Caller (PR 11) falls back to hook-only reel.

    Args:
        story: dict with 'title' and 'summary' keys (matches
            llm_hook_generator's shape).
        niche_id: e.g. 'gaming'
        hook_text: the already-selected hook, so segments extend
            rather than duplicate it.
        model: Anthropic model ID. Defaults to Haiku 4.5 for cost.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        logger.debug("[caption_segments] anthropic not installed — skipping")
        return None

    title = (story.get("title") or "").strip()
    summary = (story.get("summary") or "").strip()
    hook = (hook_text or "").strip()
    if not title or not hook:
        return None

    # 2026-07-14 (injection defense audit): base_writing sanitizes the
    # title/summary into LOCAL variables at :190-193 but doesn't write
    # back to `story`, so this downstream LLM call reads the RAW YouTube-
    # supplied values. Sanitize + injection-check here too — matches
    # the "each LLM consumer sanitizes for its own use" pattern already
    # in llm_hook_generator.py:395. A malicious video title
    # ("IGNORE PREVIOUS INSTRUCTIONS...") reaching this LLM call
    # without a sanitize pass could hijack the segments output.
    try:
        from genlab_core.cache.text_sanitizer import (
            check_for_injection,
            sanitize_text,
        )

        title = sanitize_text(title, max_length=500)
        summary = sanitize_text(summary, max_length=1000)
        for field_name, value in (("title", title), ("summary", summary)):
            hits = check_for_injection(value)
            if hits:
                logger.warning(
                    "[caption_segments] injection heuristic hit in %s: %s",
                    field_name,
                    hits,
                )
                if field_name == "title":
                    title = ""
                else:
                    summary = ""
    except Exception as exc:  # noqa: BLE001 — sanitize never blocks
        logger.warning("[caption_segments] sanitize step failed: %s", exc)

    if not title:  # sanitize+injection-check dropped the title
        return None

    prompt = _prompt_for_segments(niche_id, title, summary, hook)

    # 2026-07-21: OpenAI fallback on Anthropic exhaustion. Shares CB
    # state with all other Anthropic-direct sites via
    # `genlab_core.llm.fallback`. Caption segments are audience-facing
    # so keeping them alive during Anthropic outages preserves content
    # quality (fallback captions ≠ template captions).
    from genlab_core.llm.fallback import (
        call_openai_fallback,
        cb_is_open as _cb_is_open,
        cb_record_exhaustion as _cb_record_exhaustion,
        cb_record_success as _cb_record_success,
        fallback_enabled as _fallback_enabled,
        should_fallback as _should_fallback,
    )

    _openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    raw_text: str | None = None

    if _fallback_enabled() and _cb_is_open() and _openai_key:
        logger.debug("[caption_segments] CB open — routing to OpenAI")
        try:
            raw_text = call_openai_fallback("", prompt, 800, 0.7, _openai_key)
        except Exception as exc:
            logger.warning("[caption_segments] OpenAI fallback failed: %s", exc)
            return None
    else:
        try:
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=model,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as anthropic_exc:
            if _fallback_enabled() and _should_fallback(anthropic_exc) and _openai_key:
                _cb_record_exhaustion()
                logger.warning(
                    "[caption_segments] Anthropic %s → OpenAI fallback: %s",
                    type(anthropic_exc).__name__,
                    str(anthropic_exc)[:120],
                )
                try:
                    raw_text = call_openai_fallback("", prompt, 800, 0.7, _openai_key)
                except Exception as openai_exc:
                    logger.warning(
                        "[caption_segments] OpenAI fallback ALSO failed: %s",
                        openai_exc,
                    )
                    return None
            else:
                # WARNING (not DEBUG) — silent failure here hid the OpenAI-
                # spend-invisible bug for weeks. Same class-of-bug as YT #578.
                logger.warning("[caption_segments] API call failed: %s", anthropic_exc)
                return None
        else:
            _cb_record_success()
            # 2026-07-14 (cost audit F3): this Anthropic call site was
            # bypassing cost accounting (spend invisible on dashboard).
            # Same wire as router.py:_call_anthropic and every other
            # messages.create() site in the codebase.
            try:
                from genlab_core.intelligence.cost_accumulator import (
                    record_anthropic_usage,
                )

                record_anthropic_usage(model, resp)
            except Exception:  # noqa: BLE001
                pass

            # Extract text content from Anthropic response.
            try:
                raw_text = resp.content[0].text  # type: ignore[union-attr]
            except (AttributeError, IndexError):
                logger.debug("[caption_segments] unexpected response shape")
                raw_text = None

    # Guard against extraction failures on either path.
    if not raw_text:
        # Consolidated fall-through — reached when Anthropic response
        # parse failed OR OpenAI returned empty. Matches the historical
        # unexpected-shape return-None path.
        logger.debug("[caption_segments] no text extracted from response")
        return None

    return parse_llm_response(raw_text)


__all__ = [
    "CaptionSegment",
    "CaptionSegmentsResult",
    "generate_caption_segments",
    "parse_llm_response",
]
