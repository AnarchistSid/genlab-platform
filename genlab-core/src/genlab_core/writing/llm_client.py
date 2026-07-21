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
import time

logger = logging.getLogger(__name__)


# Fallback observability + circuit-breaker state. Module-level so state
# persists across writer invocations within one process (typical: 5-30
# writer calls per pipeline run per niche).
_ANTHROPIC_EXHAUSTION_COUNT: int = 0
_ANTHROPIC_CB_OPEN_UNTIL: float = 0.0
_CB_THRESHOLD: int = 3
_CB_COOLDOWN_S: int = 600  # 10 min

# Sentinel strings that identify Anthropic-side credit exhaustion vs
# other errors. Anthropic returns this as BadRequestError (400) with
# a body message. Rate limits are RateLimitError (429). Both should
# trigger fallback.
_ANTHROPIC_EXHAUSTION_MARKERS = (
    "credit balance is too low",
    "credit balance too low",
    "insufficient credits",
    "insufficient_quota",
)


def _is_exhaustion_error(exc: Exception) -> bool:
    """True when the exception clearly indicates Anthropic exhaustion
    or rate-limit (fallback-worthy). False for auth / network / other
    errors that OpenAI can't help with."""
    exc_name = type(exc).__name__
    # RateLimitError from anthropic SDK: always fallback-worthy
    if exc_name in ("RateLimitError", "APIStatusError"):
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _ANTHROPIC_EXHAUSTION_MARKERS)


def _cb_is_open() -> bool:
    """True if the Anthropic circuit breaker is currently open (skip
    the Anthropic attempt, go straight to OpenAI)."""
    return time.time() < _ANTHROPIC_CB_OPEN_UNTIL


def _cb_record_exhaustion() -> None:
    """Increment exhaustion counter. Open the breaker if we hit
    threshold. Idempotent within a single process."""
    global _ANTHROPIC_EXHAUSTION_COUNT, _ANTHROPIC_CB_OPEN_UNTIL
    _ANTHROPIC_EXHAUSTION_COUNT += 1
    if _ANTHROPIC_EXHAUSTION_COUNT >= _CB_THRESHOLD:
        _ANTHROPIC_CB_OPEN_UNTIL = time.time() + _CB_COOLDOWN_S
        logger.warning(
            "[llm-fallback] Anthropic circuit breaker OPEN for %ds after "
            "%d consecutive exhaustion errors — routing writer calls "
            "directly to OpenAI",
            _CB_COOLDOWN_S,
            _ANTHROPIC_EXHAUSTION_COUNT,
        )


def _cb_record_success() -> None:
    """Reset the counter + close the breaker on any successful call."""
    global _ANTHROPIC_EXHAUSTION_COUNT, _ANTHROPIC_CB_OPEN_UNTIL
    if _ANTHROPIC_EXHAUSTION_COUNT > 0 or _ANTHROPIC_CB_OPEN_UNTIL > 0:
        logger.info(
            "[llm-fallback] Anthropic recovered — resetting circuit "
            "breaker (was %d consecutive exhaustions)",
            _ANTHROPIC_EXHAUSTION_COUNT,
        )
    _ANTHROPIC_EXHAUSTION_COUNT = 0
    _ANTHROPIC_CB_OPEN_UNTIL = 0.0


def _fallback_enabled() -> bool:
    """Env-flag opt-in (default ON). Operator can disable if OpenAI
    budget also runs dry, so we don't cascade a second outage."""
    return os.environ.get("GENLAB_LLM_FALLBACK_ENABLED", "1").strip() != "0"


def _call_openai_fallback(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    api_key: str,
) -> str:
    """Call OpenAI GPT-4o-mini as writer fallback. Mirror shape of
    ``genlab_core.llm.router._call_openai``. Reuses cost accumulator
    so fallback spend is visible on the same ledger.
    """
    import openai  # noqa: PLC0415 — lazy so tests without openai still import

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    try:
        from genlab_core.intelligence.cost_accumulator import record_openai_usage

        record_openai_usage("gpt-4o-mini", response)
    except Exception:  # noqa: BLE001 — cost tracking never blocks
        pass
    return response.choices[0].message.content


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
