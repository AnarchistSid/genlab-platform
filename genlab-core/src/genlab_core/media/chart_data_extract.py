"""Extract chart-worthy data from AI news summaries.

## Why this exists

Task #193 companion: ``chart_broll`` renders a chart from a title +
bar list. Somebody has to produce that bar list from the pipeline's
story dict. This module asks Claude Haiku for a compact JSON
extraction — visually-chartable numerical claims only, returning None
when no chartable data exists (fail-open).

## Contract

Input: story summary (str, 40+ chars) + title.
Output: ``ChartData(title, bars)`` or ``None``.

Returns None when:
  * LLM unavailable (no API key)
  * LLM returns malformed JSON
  * LLM returns fewer than 2 valid bars (single-bar chart is useless)
  * Any bar's value can't be parsed as float
  * LLM signals "no chartable data" (explicit null return)

The caller (base_visual_render) treats None as "no chart intro this
render" and falls through to standard behavior.

## Why Haiku not Sonnet

Cost. Cache. Speed. This is a structured-JSON extraction on a
250-500 char summary — Haiku handles that with ~99% success at 1/10
the cost of Sonnet. If accuracy proves inadequate in prod, swap the
model via ``AnthropicLLMClient(model=...)``.

## Not doing here

* Streaming / async — pipeline runs are batch-sequential.
* Fallback to regex extraction — the LLM fail-open already covers
  the "no chart today" case cleanly.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You extract chart-worthy numerical data from AI-industry news "
    "summaries. Return a compact JSON object OR the literal string "
    "'null' if no chartable data is present.\n\n"
    "Rules:\n"
    "1. Return null when the summary contains 0 or 1 concrete numbers, "
    "or when the numbers are dates, version numbers, or IDs (not "
    "comparable magnitudes).\n"
    "2. Return null when the numbers can't be visually compared as "
    "bars (e.g. 'launched in 2024' is one number, not a chart).\n"
    "3. When returning data, use this schema:\n"
    '   {"title": "<short chart title, ≤50 chars>", '
    '"bars": [{"label": "<short label, ≤20 chars>", '
    '"value": <numeric>, "unit": "<optional short unit>"}]}\n'
    "4. Include 3-6 bars maximum. Prefer 3-5.\n"
    "5. Labels should be entities (GPT-2, Series-C, 2024) not sentences.\n"
    "6. Values must be positive numbers of comparable order-of-magnitude. "
    "Do NOT mix percentages with dollar amounts. Do NOT include zero.\n"
    "7. Never wrap output in code fences. Never add commentary. Only the "
    "JSON object OR the literal string 'null'."
)


@dataclass(frozen=True)
class ChartData:
    """Extraction output — ready to pass to render_chart_broll."""
    title: str
    bars: list[tuple[str, float]]


def extract_chart_data(
    summary: str,
    story_title: str = "",
    *,
    client: Any = None,
    max_tokens: int = 400,
) -> ChartData | None:
    """Ask Haiku to extract chart-worthy data. Return None on any
    failure (fail-open).

    Args:
        summary: story body / description text (40+ chars minimum).
        story_title: optional headline for extra context.
        client: injected LLM client for tests. Default: lazy-init
            AnthropicLLMClient.
        max_tokens: cap on LLM output. 400 is generous for the
            expected ~150-token JSON response.

    Returns:
        ChartData or None.
    """
    if not summary or len(summary.strip()) < 40:
        return None

    if client is None:
        from genlab_core.writing.llm_client import AnthropicLLMClient

        client = AnthropicLLMClient()
        if not client.is_available:
            logger.debug("[chart_extract] no ANTHROPIC_API_KEY — skip")
            return None

    user_prompt = f"Story title: {story_title}\n\nSummary:\n{summary}\n"
    try:
        raw = client.complete(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=max_tokens,
            temperature=0.0,  # deterministic extraction
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[chart_extract] LLM call failed: %s", exc)
        return None

    return _parse_response(raw)


def _parse_response(raw: str) -> ChartData | None:
    """Parse Haiku's response. Handles common malformations:
      * bare 'null' → None
      * stray whitespace / trailing commentary → strip
      * code-fence wrappers (rare but possible) → unwrap
      * malformed JSON → None with warning
    """
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.lower() in ("null", '"null"'):
        return None
    # Strip accidental code fences (```json ... ```)
    fence_match = re.search(
        r"```(?:json)?\s*(\{.+?\})\s*```", cleaned, re.DOTALL,
    )
    if fence_match:
        cleaned = fence_match.group(1)
    # Prefer the FIRST balanced JSON object in the text — some models
    # inject a preamble/postamble despite the system-prompt ban.
    else:
        obj_match = re.search(r"\{.+\}", cleaned, re.DOTALL)
        if obj_match:
            cleaned = obj_match.group(0)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning(
            "[chart_extract] JSON parse failed: %s; raw=%r",
            exc, raw[:200],
        )
        return None

    if not isinstance(parsed, dict):
        return None
    title = str(parsed.get("title") or "").strip()
    if not title or len(title) > 100:
        return None
    raw_bars = parsed.get("bars")
    if not isinstance(raw_bars, list) or len(raw_bars) < 2:
        # <2 bars = not a chart. Fail-open.
        return None

    bars: list[tuple[str, float]] = []
    for item in raw_bars:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label or len(label) > 30:
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        bars.append((label, value))

    if len(bars) < 2:
        return None
    # Cap at 7 to match chart_broll's rendering ceiling.
    return ChartData(title=title, bars=bars[:7])
