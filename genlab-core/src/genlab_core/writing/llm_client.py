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

# ── LLM-PRIMARY-01 (2026-09-14): inference.sh is the PRIMARY LLM provider ──
#
# On 2026-09-14 both direct providers were empty at once — Anthropic returned
# 400 "credit balance is too low", OpenAI 429 "no credits remaining" — and the
# failover chain worked perfectly with nowhere to fail over to. Content
# generation stopped on every niche. Belt held $102 of credit the writer could
# not reach, because `call_fallback_chain` was never wired into this client.
#
# So the order inverts: belt first, direct providers as optional fallbacks that
# only matter when belt is down AND they happen to be funded. Belt carries
# claude-sonnet-4-6 at list price ($3/M in, $15/M out — 0% markup), so the
# writer keeps the same model it has always used.
#
# Measured before shipping (2026-09-14, task 5qa90xj3…): Sonnet via the `run`
# function returned 330 chars of valid JSON in 7.6s for $0.0014. The `openai`
# function returned empty on every shape tried, which is why `run` is used here.
# At ~40 writer calls per niche-fire that is ~$0.06/niche, ~$0.28/day for five.
_BELT_SONNET = ("anthropic/claude-sonnet-4-6", "belt:claude-sonnet-4-6")
_BELT_HAIKU = ("anthropic/claude-haiku-4-5", "belt:claude-haiku-4-5")


def _belt_tiers_for(model: str) -> tuple[tuple[str, str], ...]:
    """Belt tiers ordered to HONOUR the model the caller asked for.

    2026-09-14, found by gate 2: a fixed Sonnet-first list silently upgraded
    every caller to Sonnet, including the three that explicitly construct
    `AnthropicLLMClient(model=claude-haiku-4-5-…)` — `music_mood_llm_fit`,
    `dynamic_matcher`, and `chart_data_extract` (via the default). That is ~20×
    the input cost on calls whose own docstrings budget "~150 input + ~15 output
    tokens ≈ $0.00004", and it changes behaviour: Sonnet answered "none" for a
    subject-extraction Haiku was tuned for.

    The requested model leads; the other stays as the second tier so a single
    model being unavailable still degrades rather than stops.
    """
    if "haiku" in (model or "").lower():
        return (_BELT_HAIKU, _BELT_SONNET)
    return (_BELT_SONNET, _BELT_HAIKU)

# Kill switch. Set to "0" to restore the pre-2026-09-14 order (Anthropic direct
# first) without a deploy — the direct path below is unchanged and still works
# whenever those accounts are funded.
_BELT_PRIMARY_ENV = "GENLAB_LLM_BELT_PRIMARY"


def _belt_primary_enabled() -> bool:
    # OFF under pytest unless explicitly opted in. Without this the belt tier
    # runs first in every test that exercises `complete()`, which (a) makes
    # REAL billed subprocess calls from the suite, and (b) breaks ~20 existing
    # tests that mock the Anthropic path and assert on its call count. Same
    # guard shape as the T-61 cost-write block in `intelligence/cost_persist`.
    # Measured: the suite went 2.3s -> 114s before this guard was added.
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get(
        "GENLAB_ALLOW_TEST_BELT_CALLS", ""
    ).strip() != "1":
        return False
    return os.environ.get(_BELT_PRIMARY_ENV, "1").strip() not in ("0", "false", "False")


def _call_belt_tier(
    app: str,
    label: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> str | None:
    """One belt tier. Returns assistant text, or None so the caller tries the next.

    Asserts BOTH that the task completed AND that the payload carries content.
    `run_app` already rejects a non-completed status, but a completed task with
    an empty `response` is the specific trap this codebase has hit four times:
    `status_text == "completed"` alone has never been sufficient evidence that
    work happened.
    """
    from genlab_core.integrations.belt_client import run_app

    result = run_app(
        app,
        {
            "system_prompt": system,
            "text": user,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout_seconds=180,
    )
    if not result.ok:
        logger.warning("[llm-belt] %s failed: %s", label, (result.error or "")[:200])
        return None

    text = ((result.output or {}).get("response") or "") if result.output else ""
    if not str(text).strip():
        # Completed-but-empty. Loud, because this is the shape that reads as
        # success everywhere else.
        logger.warning(
            "[llm-belt] %s returned status=completed with an EMPTY response "
            "(task=%s) — treating as failure and trying the next tier",
            label,
            result.task_id,
        )
        return None

    try:
        from genlab_core.intelligence.cost_accumulator import record_provider_usage

        record_provider_usage(provider="belt", model=label, payload=result.output)
    except Exception as exc:  # noqa: BLE001 — attribution never breaks a call
        logger.warning("[llm-belt] cost attribution failed for %s: %s", label, exc)

    logger.info(
        "[llm-belt] served by %s (task=%s, %d chars)", label, result.task_id, len(text)
    )
    return str(text)


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
        # LLM-PRIMARY-01: belt tiers FIRST. Each returns None on any failure —
        # including completed-but-empty — so the loop falls through to the next
        # tier and finally to the direct providers below, whose logic is
        # unchanged. If every belt tier fails and both direct accounts are
        # empty, the original Anthropic error still surfaces exactly as before.
        if _belt_primary_enabled():
            _tiers = _belt_tiers_for(self._model)
            for _app, _label in _tiers:
                _text = _call_belt_tier(
                    _app, _label, system, user, max_tokens, temperature
                )
                if _text is not None:
                    return _text
            logger.warning(
                "[llm-belt] all %d belt tier(s) failed — falling through to "
                "the direct providers (which may themselves be unfunded)",
                len(_tiers),
            )

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
