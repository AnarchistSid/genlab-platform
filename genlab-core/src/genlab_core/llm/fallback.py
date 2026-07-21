"""Shared Anthropic → OpenAI fallback machinery for direct-call sites.

The 2026-07-18 → 2026-07-21 outage was caused by Anthropic API credit
exhaustion. `AnthropicLLMClient` (writer path) got its own fallback in
commit `f1554193`, but 7 other Anthropic-direct call sites remained
vulnerable:

  * `scheduling/auto_approval_gate.py` — LLM judge (borderline decisions)
  * `engagement/persona_engine.py` — outbound + inbound reply generation
  * `writing/llm_hook_generator.py` — hook generation (6 sub-calls)
  * `writing/caption_segments.py` — caption writing
  * `learning/scratchpad.py` — weekly Opus reflection
  * `learning/post_rca.py` — post-mortem RCA
  * `learning/rationale_classifier.py` — rejection auto-classify

Each has slightly different shapes (different models, prompt caching,
temperatures, structured outputs). Rather than force a full refactor
to `AnthropicLLMClient`, this module provides SHARED helpers each
site can wrap around its existing Anthropic call in 3-5 lines:

    from genlab_core.llm.fallback import (
        should_fallback, call_openai_fallback, cb_record_exhaustion,
        cb_record_success, cb_is_open,
    )

    try:
        if cb_is_open():
            raise _CircuitOpen  # goes straight to fallback
        response = client.messages.create(...)
        cb_record_success()
        return response.content[0].text
    except Exception as e:
        if should_fallback(e):
            cb_record_exhaustion()
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if api_key:
                return call_openai_fallback(system, user, max_tokens, temp, api_key)
        raise

CRITICAL: circuit breaker state is MODULE-LEVEL so ALL 8 sites share
ONE breaker. Otherwise 8 sites × 3 exhaustions each = 24 total
exhaustion calls before any site stops trying. Shared state means
the first 3 exhaustions (across ANY sites) opens the breaker for
everyone.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


# ── Shared circuit breaker state (module-level, all sites benefit) ──

_ANTHROPIC_EXHAUSTION_COUNT: int = 0
_ANTHROPIC_CB_OPEN_UNTIL: float = 0.0
_CB_THRESHOLD: int = 3
_CB_COOLDOWN_S: int = 600  # 10 min


class CircuitOpen(Exception):
    """Sentinel raised internally when caller checks `cb_is_open()`
    and wants to skip Anthropic. Never raised by the helpers below;
    it's here as a documented pattern for callers who want the "if
    breaker open, immediately fallback" flow."""

    pass


# ── Exhaustion classifier ──

_ANTHROPIC_EXHAUSTION_MARKERS = (
    "credit balance is too low",
    "credit balance too low",
    "insufficient credits",
    "insufficient_quota",
)


def should_fallback(exc: Exception) -> bool:
    """True when the exception clearly indicates Anthropic exhaustion
    or rate-limit (fallback-worthy). False for auth / network / other
    errors that OpenAI can't help with.

    Recognises:
      * `credit balance is too low` — 400 BadRequestError from exhausted
        account (the 2026-07-18 exhaustion class)
      * `insufficient credits` / `insufficient_quota` — variants
      * SDK exception class names `RateLimitError` / `APIStatusError`

    Rejects (re-raise):
      * `401 unauthorized` — needs operator, OpenAI can't help
      * `ConnectionError` — network layer, OpenAI would hit same DNS
      * `invalid_request_error` shapes that aren't credit-exhaustion
    """
    exc_name = type(exc).__name__
    if exc_name in ("RateLimitError", "APIStatusError"):
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _ANTHROPIC_EXHAUSTION_MARKERS)


# ── Circuit breaker API ──


def cb_is_open() -> bool:
    """True if the shared Anthropic circuit breaker is currently open.
    Callers should skip the Anthropic attempt and go straight to
    OpenAI when this returns True."""
    return time.time() < _ANTHROPIC_CB_OPEN_UNTIL


def cb_record_exhaustion() -> None:
    """Increment the shared exhaustion counter. Open the breaker if
    we hit the threshold. Idempotent within a single process."""
    global _ANTHROPIC_EXHAUSTION_COUNT, _ANTHROPIC_CB_OPEN_UNTIL
    _ANTHROPIC_EXHAUSTION_COUNT += 1
    if _ANTHROPIC_EXHAUSTION_COUNT >= _CB_THRESHOLD:
        _ANTHROPIC_CB_OPEN_UNTIL = time.time() + _CB_COOLDOWN_S
        logger.warning(
            "[llm-fallback] Anthropic circuit breaker OPEN for %ds after "
            "%d consecutive exhaustion errors — routing ALL Anthropic "
            "sites straight to OpenAI",
            _CB_COOLDOWN_S,
            _ANTHROPIC_EXHAUSTION_COUNT,
        )


def cb_record_success() -> None:
    """Reset the counter + close the breaker on any successful
    Anthropic call from any site."""
    global _ANTHROPIC_EXHAUSTION_COUNT, _ANTHROPIC_CB_OPEN_UNTIL
    if _ANTHROPIC_EXHAUSTION_COUNT > 0 or _ANTHROPIC_CB_OPEN_UNTIL > 0:
        logger.info(
            "[llm-fallback] Anthropic recovered — resetting circuit "
            "breaker (was %d consecutive exhaustions)",
            _ANTHROPIC_EXHAUSTION_COUNT,
        )
    _ANTHROPIC_EXHAUSTION_COUNT = 0
    _ANTHROPIC_CB_OPEN_UNTIL = 0.0


def fallback_enabled() -> bool:
    """Env-flag opt-in (default ON). Operator can set
    ``GENLAB_LLM_FALLBACK_ENABLED=0`` to disable when OpenAI budget
    is also dry, so we don't cascade a second outage."""
    return os.environ.get("GENLAB_LLM_FALLBACK_ENABLED", "1").strip() != "0"


# ── OpenAI call helper ──


def call_openai_fallback(
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    api_key: str,
    *,
    model: str = "gpt-4o-mini",
    json_mode: bool = False,
) -> str:
    """Call OpenAI as writer/gate/reply fallback. Returns the response
    text as a string.

    Mirrors shape of `genlab_core.llm.router._call_openai`. Reuses the
    cost accumulator so fallback spend is visible on the same ledger
    as primary calls.

    ``json_mode=True`` requests OpenAI's structured JSON output
    (matches `router.py:_call_openai`'s json_mode param) — use for
    sites that need parseable JSON (rationale_classifier, judge).
    """
    import openai  # noqa: PLC0415 — lazy import; tests without openai still import

    client = openai.OpenAI(api_key=api_key)
    # Skip system role when empty — some sites (e.g., caption_segments)
    # only send a user prompt. OpenAI accepts an empty system message
    # but including it as an empty string is wasteful/confusing.
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    try:
        from genlab_core.intelligence.cost_accumulator import record_openai_usage

        record_openai_usage(model, response)
    except Exception:  # noqa: BLE001 — cost tracking never blocks
        pass
    return response.choices[0].message.content


# ── Convenience wrapper for the common pattern ──


def with_openai_fallback(
    anthropic_call: Any,  # callable returning str
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
    site_label: str = "unknown",
) -> str:
    """Run an Anthropic call and transparently fall back to OpenAI on
    exhaustion. Callers pass a zero-arg callable that performs the
    Anthropic call and returns a str. Handles the circuit breaker +
    logging boilerplate.

    Use this when you don't need site-specific Anthropic customisation
    (prompt caching, model routing, etc.). For sites with rich
    Anthropic-specific setup, use the primitives above directly.

    Returns str. On unrecoverable failure, re-raises the ORIGINAL
    Anthropic exception so downstream error classifiers behave as
    before.
    """
    # CB-open fast path: skip Anthropic entirely.
    if fallback_enabled() and cb_is_open():
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            logger.debug(
                "[llm-fallback][%s] CB open — routing to OpenAI without "
                "trying Anthropic",
                site_label,
            )
            return call_openai_fallback(
                system, user, max_tokens, temperature, openai_key, json_mode=json_mode
            )
        # No OpenAI key → fall through and try Anthropic anyway.

    try:
        result = anthropic_call()
    except Exception as anthropic_exc:
        if not (fallback_enabled() and should_fallback(anthropic_exc)):
            raise
        cb_record_exhaustion()
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not openai_key:
            logger.warning(
                "[llm-fallback][%s] Anthropic exhausted but OPENAI_API_KEY "
                "not set — re-raising (%s)",
                site_label,
                type(anthropic_exc).__name__,
            )
            raise
        logger.warning(
            "[llm-fallback][%s] Anthropic %s → falling back to OpenAI: %s",
            site_label,
            type(anthropic_exc).__name__,
            str(anthropic_exc)[:120],
        )
        try:
            return call_openai_fallback(
                system, user, max_tokens, temperature, openai_key, json_mode=json_mode
            )
        except Exception as openai_exc:
            logger.warning(
                "[llm-fallback][%s] OpenAI fallback ALSO failed (%s) — "
                "re-raising original Anthropic error",
                site_label,
                openai_exc,
            )
            raise anthropic_exc from openai_exc

    cb_record_success()
    return result


__all__ = [
    "should_fallback",
    "call_openai_fallback",
    "cb_is_open",
    "cb_record_exhaustion",
    "cb_record_success",
    "fallback_enabled",
    "with_openai_fallback",
    "CircuitOpen",
]
