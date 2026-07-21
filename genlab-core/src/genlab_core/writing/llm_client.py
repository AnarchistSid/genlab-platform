"""Thin adapter bridging write_video_content's llm_client interface to Anthropic
with automatic fallback to OpenAI when Anthropic credit is exhausted or
sustained rate-limits.

Usage:
    from genlab_core.writing.llm_client import AnthropicLLMClient
    from genlab_core.cost.model_router import get_model

    client = AnthropicLLMClient(model=get_model("write_sports_content"))
    result = client.complete(system="...", user="...", max_tokens=600, temperature=0.8)

2026-07-21 fallback (item #1 of exhaustive-fix backlog):

The 2026-07-18 → 2026-07-21 outage was caused in part by Anthropic API
credit exhaustion (16 alerts/hour, blueprint creation dropped from
5/day to 0-3/day across all niches). ``genlab_core.llm.router.llm_call``
already has a Haiku → GPT-4o-mini → GPT-4.1-nano fallback chain — but
NOTHING calls it. Every real writer site uses ``AnthropicLLMClient``
directly, so the router fallback was dead code.

Fix: build the fallback INTO ``AnthropicLLMClient.complete()`` so all
callers benefit transparently, no wire-refactor needed. Behavior:

* Anthropic call succeeds → return normally (unchanged).
* Anthropic raises ``BadRequestError`` matching "credit balance is
  too low" OR any ``RateLimitError`` → fall through to OpenAI
  GPT-4o-mini using the same system+user prompts. Return contract
  preserved (str).
* OpenAI also fails → re-raise the original Anthropic exception so
  the caller's error handling still sees the primary provider's
  message.

Circuit breaker: after 3 Anthropic exhaustion failures in a row, skip
Anthropic for 10 minutes and go straight to OpenAI. Prevents burning
the ~50ms Anthropic call on every writer invocation when we already
know the provider is dark. Reset on first successful Anthropic call.

Env flag ``GENLAB_LLM_FALLBACK_ENABLED`` (default "1" — ON) so
operator can disable if OpenAI budget also runs dry.
"""

from __future__ import annotations

import logging
import os

# 2026-07-21 refactor: fallback machinery moved to genlab_core.llm.fallback
# so all 8 Anthropic-direct sites share ONE circuit breaker (otherwise
# each site would need 3 exhaustions before its own CB opens = 24 total
# exhaustion attempts before any site stops trying).
#
# The `_call_openai_fallback` / `_is_exhaustion_error` / `_cb_*` /
# `_fallback_enabled` names are re-exported here so tests +
# call-site code that predates the extraction keep working. New sites
# should import directly from `genlab_core.llm.fallback`.
from genlab_core.llm.fallback import (
    call_openai_fallback as _call_openai_fallback,
)
from genlab_core.llm.fallback import (
    cb_is_open as _cb_is_open,
)
from genlab_core.llm.fallback import (
    cb_record_exhaustion as _cb_record_exhaustion,
)
from genlab_core.llm.fallback import (
    cb_record_success as _cb_record_success,
)
from genlab_core.llm.fallback import (
    fallback_enabled as _fallback_enabled,
)
from genlab_core.llm.fallback import (
    should_fallback as _is_exhaustion_error,
)

logger = logging.getLogger(__name__)


class AnthropicLLMClient:
    """Adapter: .complete(system, user, max_tokens, temperature) -> str

    Lazily initialises the Anthropic SDK client on first call so that
    importing this module never triggers network I/O or requires the
    ``anthropic`` package to be installed (graceful degradation).
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or "claude-haiku-4-5-20251001"
        self._client = None

    @property
    def is_available(self) -> bool:
        """True when an API key is configured."""
        return bool(self._api_key)

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # noqa: F811 — lazy import

            self._client = anthropic.Anthropic(api_key=self._api_key)

    # U-01 (2026-06-17): minimum prompt size (in chars, ~4 chars/token)
    # below which prompt caching is NOT enabled. Anthropic's docs state
    # caching requires the cached prefix to be ≥1024 tokens (Haiku) /
    # ≥2048 tokens (Sonnet) to actually take effect — anything smaller
    # is billed at the cache-write rate (1.25×) for nothing. ~4 chars/token
    # is the common English heuristic; pad to 1500 chars (=~375 tokens) for
    # the smallest gate, since Haiku's 1024 threshold needs ~4000 chars and
    # we want a safety margin. The pad is intentionally conservative:
    # 4000-char threshold means short-prompt callers (engagement reply,
    # hook generator's per-platform variants) skip caching and pay the
    # normal input rate — exactly the desired behaviour.
    _CACHE_THRESHOLD_CHARS: int = 4000

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """Call the Anthropic Messages API and return the assistant text.

        U-01: when ``system`` exceeds ``_CACHE_THRESHOLD_CHARS`` the system
        prompt is sent as a list-of-dict with ``cache_control: {"type":
        "ephemeral"}`` to enable Anthropic prompt caching. Subsequent
        calls within the 5-minute cache TTL pay only ~10% of normal input
        cost for the cached portion. The 5-niche writing pipeline runs
        ~30 candidates per niche with the same per-niche system prompt,
        so caching saves ~90% of input tokens on calls 2-30 within a
        niche.

        Falls back to the plain-string system format when below threshold
        — caching short prompts is net-negative (cache writes cost 1.25×
        the input rate).

        2026-07-21: automatic OpenAI GPT-4o-mini fallback on Anthropic
        credit-balance-too-low or sustained rate limits (see module
        docstring). Preserves the ``str`` return contract. Circuit
        breaker skips Anthropic entirely after 3 consecutive exhaustion
        errors for 10 min.

        Raises on network / auth errors that OpenAI also can't help with.
        """
        # Circuit breaker: if we've hit exhaustion 3× recently, go
        # straight to OpenAI. Reset when Anthropic recovers.
        if _fallback_enabled() and _cb_is_open():
            openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if openai_key:
                logger.debug(
                    "[llm-fallback] CB open — routing to OpenAI without "
                    "trying Anthropic"
                )
                return _call_openai_fallback(
                    system, user, max_tokens, temperature, openai_key
                )
            # No OpenAI key → fall through and try Anthropic anyway
            # (better to try + fail loud than silently return empty).

        self._ensure_client()
        if len(system) >= self._CACHE_THRESHOLD_CHARS:
            system_arg = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_arg = system

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_arg,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as anthropic_exc:
            # Only fall back on exhaustion / rate-limit — not on auth,
            # not on invalid-request-format, not on network. Those all
            # need operator attention and OpenAI won't help.
            if not (_fallback_enabled() and _is_exhaustion_error(anthropic_exc)):
                raise
            _cb_record_exhaustion()
            openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not openai_key:
                logger.warning(
                    "[llm-fallback] Anthropic exhausted but OPENAI_API_KEY "
                    "not set — re-raising original error (%s)",
                    type(anthropic_exc).__name__,
                )
                raise
            logger.warning(
                "[llm-fallback] Anthropic %s → falling back to OpenAI "
                "gpt-4o-mini: %s",
                type(anthropic_exc).__name__,
                str(anthropic_exc)[:120],
            )
            try:
                return _call_openai_fallback(
                    system, user, max_tokens, temperature, openai_key
                )
            except Exception as openai_exc:
                logger.warning(
                    "[llm-fallback] OpenAI fallback ALSO failed (%s) — "
                    "re-raising original Anthropic error",
                    openai_exc,
                )
                raise anthropic_exc from openai_exc

        # Success — close the breaker if it was open + reset the counter.
        _cb_record_success()

        # Track cost if accumulator is available in current context (U-03:
        # shared helper, now used at every Anthropic call site). Cache
        # hit/miss is reflected by ``usage.input_tokens`` (excludes cached
        # tokens) + the separate ``cache_creation_input_tokens`` /
        # ``cache_read_input_tokens`` fields that record_anthropic_usage
        # will surface in a future refinement. The cost SAVINGS land
        # automatically since cached tokens aren't billed as input tokens.
        from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

        record_anthropic_usage(self._model, response)

        return response.content[0].text
