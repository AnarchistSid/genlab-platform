"""Anthropic prompt caching helper — central wrapper for system prompts.

SYSTEM-RESEARCH §8 U-01: hot LLM call sites have static system prompts
that Anthropic prompt caching can serve at ~10% of input cost. The 5-
niche writing/hooks/persona/critique pipelines all reuse identical
per-niche system prompts within a 5-minute window — calls 2-N within a
niche become cache reads at 10% input cost.

Motivation (2026-06-27 credit-exhaustion incident)
-------------------------------------------------
On 2026-06-27 a credit exhaustion at Anthropic interrupted the
gaming / sports / movies pipelines. Adding prompt caching at the well-
defined "shared system prompt + few-shot" call sites cuts input-token
cost by ~90% on cached calls, which compresses the daily LLM spend by
roughly 5-10× and gives the same credit balance proportionally more
runway between top-ups.

How caching works
-----------------
Per Anthropic's spec, marking a system-prompt block with
``cache_control: {"type": "ephemeral"}`` makes the block cacheable for
~5 minutes. The next call within that TTL that hits the same cache key
(model + exact bytes of the cached prefix) pays ~10% of input cost on
those tokens. **Minimum cacheable block**: 1024 tokens for Haiku (4096
for Sonnet/Opus). Below the minimum, the cache write costs 1.25× the
input rate and never gets served — net-negative.

This module gates caching on a char-length threshold (≥4000 chars =
~1000 tokens with the 4-chars/token English heuristic) so short prompts
fall through to the plain-string path and avoid the negative cache-
write penalty.

Usage
-----
Two callsite shapes:

1. **System prompt as list-of-dict** (preferred — works with all
   Anthropic ``messages.create`` callers)::

       from genlab_core.llm.prompt_cache import with_prompt_cache

       client.messages.create(
           model="claude-haiku-4-5-20251001",
           system=with_prompt_cache(SYSTEM_PROMPT),
           messages=[{"role": "user", "content": user_text}],
           ...
       )

   When ``SYSTEM_PROMPT`` is below the threshold ``with_prompt_cache``
   returns the plain string unchanged (zero behavior change).

2. **Manual block construction** (for callers building multi-block
   system arrays — rare)::

       block = build_cache_block(SHARED_PREFIX)
       client.messages.create(system=[block, dynamic_block], ...)

The Anthropic SDK accepts both ``system="..."`` (str) and
``system=[{...}]`` (list) — list form is required to attach
``cache_control``.

Cost-observability addendum
---------------------------
``cost_accumulator.record_anthropic_usage`` is extended to also read
``cache_creation_input_tokens`` and ``cache_read_input_tokens`` fields
from the Anthropic response, so the dashboard can measure the actual
cache hit rate per model. Without that wiring the savings would be
invisible (the existing ``usage.input_tokens`` excludes cached reads,
so cost _drops_ silently but operators can't tell whether it's from
caching or from reduced volume).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# Char-length threshold below which caching is NOT enabled.
#
# Anthropic requires the cached prefix to be ≥1024 tokens (Haiku) to
# actually take effect — anything smaller is billed at the cache-write
# rate (1.25×) for nothing. ~4 chars/token is the common English
# heuristic; we pad to 4000 chars (≈1000 tokens) so the threshold is
# safely above the 1024-token floor.
#
# Operators can override via ``GENLAB_PROMPT_CACHE_MIN_CHARS`` (e.g.
# raise it after audit data shows short prompts paying for unused
# writes, or lower it after Anthropic ships shorter-prefix support).
DEFAULT_MIN_CACHE_CHARS: int = 4000

# Env var to disable caching globally — emergency switch in case the
# Anthropic cache layer misbehaves (5-min TTL drift, cache poisoning,
# unexpected billing) and we need to fall back to plain-string mode
# without redeploying code.
ENV_DISABLE = "GENLAB_PROMPT_CACHE_DISABLED"
ENV_MIN_CHARS = "GENLAB_PROMPT_CACHE_MIN_CHARS"


def _is_caching_disabled() -> bool:
    """Return True only when the kill-switch env is explicitly set to ``"1"``.

    Matches the opt-in / kill-switch pattern used across genlab_core
    (post_rca, rationale_classifier, niche_fit_ranker etc.). ``"true"``
    / ``"yes"`` are NOT accepted — keeps the override gesture unambiguous.
    """
    return os.environ.get(ENV_DISABLE, "0").strip() == "1"


def _resolve_min_chars(min_chars: int | None) -> int:
    """Resolve the min-char threshold from env, then arg, then default."""
    if min_chars is not None:
        return int(min_chars)
    raw = os.environ.get(ENV_MIN_CHARS)
    if not raw:
        return DEFAULT_MIN_CACHE_CHARS
    try:
        value = int(raw)
        if value >= 0:
            return value
        logger.warning(
            "[prompt_cache] %s=%r negative, using default %d",
            ENV_MIN_CHARS,
            raw,
            DEFAULT_MIN_CACHE_CHARS,
        )
    except (TypeError, ValueError):
        logger.warning(
            "[prompt_cache] %s=%r not parseable as int, using default %d",
            ENV_MIN_CHARS,
            raw,
            DEFAULT_MIN_CACHE_CHARS,
        )
    return DEFAULT_MIN_CACHE_CHARS


def build_cache_block(text: str) -> dict[str, Any]:
    """Build a single Anthropic system-prompt block with cache_control set.

    Returned shape matches Anthropic's API spec::

        {"type": "text", "text": "<prompt>", "cache_control": {"type": "ephemeral"}}

    Caller is responsible for ensuring ``text`` is long enough to be
    cacheable; use ``with_prompt_cache`` for the gated-by-length path.
    """
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def with_prompt_cache(
    system_text: str,
    *,
    min_chars: int | None = None,
) -> str | list[dict[str, Any]]:
    """Return a system arg suitable for ``messages.create(system=...)``.

    When ``system_text`` meets the length threshold AND the kill-switch
    env is not set, returns ``[{cache_control block}]`` — a list of one
    block with ``cache_control: {"type": "ephemeral"}``. Otherwise
    returns ``system_text`` unchanged (plain string).

    The plain-string fallback is intentional: caching short prompts is
    net-negative (cache writes cost 1.25× the input rate), so callers
    can pass through this helper unconditionally and only pay for
    caching when it will actually pay back.

    Args:
        system_text: The system prompt text (may be empty).
        min_chars: Override the char-length threshold. Default uses
            ``DEFAULT_MIN_CACHE_CHARS`` (4000) or the
            ``GENLAB_PROMPT_CACHE_MIN_CHARS`` env override.

    Returns:
        ``list[dict]`` when caching applies, else the original ``str``.

    Example::

        from genlab_core.llm.prompt_cache import with_prompt_cache

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            system=with_prompt_cache(my_long_system_prompt),
            messages=[{"role": "user", "content": user_input}],
        )

    None-safety: a ``None`` system_text is treated as empty string and
    returns ``""`` so callers don't need to add a guard. (The Anthropic
    SDK accepts empty system strings.)
    """
    if system_text is None:
        return ""

    if _is_caching_disabled():
        return system_text

    threshold = _resolve_min_chars(min_chars)
    if len(system_text) < threshold:
        return system_text

    return [build_cache_block(system_text)]


def extract_cache_usage(response: Any) -> tuple[int, int]:
    """Pull (cache_creation_tokens, cache_read_tokens) from an Anthropic response.

    Returns ``(0, 0)`` when the fields are absent — older SDK responses,
    cache-disabled calls, or test mocks won't have them and the caller
    should treat absence as "no caching happened" without raising.

    The two fields measure:
      - ``cache_creation_input_tokens``: tokens written to cache this
        call. Billed at 1.25× input rate. Non-zero on the FIRST call
        that primes the cache.
      - ``cache_read_input_tokens``: tokens served from cache this call.
        Billed at 0.10× input rate. Non-zero on subsequent calls within
        the 5-minute TTL that hit the same cache key.

    A healthy cache hit rate has writes ≈ 1 per 5-min window per
    cache key + reads ≈ N-1 for N calls in that window. The dashboard
    should surface ``cache_read / (cache_read + input)`` as the hit
    rate metric.
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return (0, 0)
        creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        return (int(creation), int(read))
    except Exception as exc:  # noqa: BLE001 — defensive, never raise to caller
        logger.debug("[prompt_cache] extract_cache_usage failed: %s", exc)
        return (0, 0)
