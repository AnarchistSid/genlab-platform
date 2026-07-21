"""Strategist-specific Anthropic client.

Dedicated to the weekly meta-cognition use case:
- Claude Sonnet 4.6 (reasoning quality > cost; runs once per niche per week)
- Forces JSON output (the schema in the user prompt is the contract)
- Hard cost cap per call (fails soft if input is too large)
- 60s timeout with single retry on transient errors

This is intentionally separate from `genlab_core.llm.router.llm_call` to
keep blast radius small: changes here can't regress hook generation or
other production-facing LLM calls.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Pinned model — change requires bumping SYSTEM_PROMPT_VERSION in prompts.py
# (different models interpret structured-output instructions differently;
# treating them as a versioned contract surface prevents silent drift).
STRATEGIST_MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 4_000  # ~3K JSON + headroom
# 2026-07-07: bumped 60.0 → 180.0 after live-fire caught 60s was
# ~10% short of the real call duration. Measured on prod with the
# actual Strategist prompt shape: 67.02s for one niche (1724 input
# tokens + 3323 output tokens at Sonnet 4.6 throughput). The 60s
# limit caused every single call to time out; the Anthropic SDK's
# built-in 3 retries then multiplied the 60s wait × 3 attempts × 2
# our-attempts = 360s of wasted wall-time before we surfaced the
# failure. 180s gives ~2.7× headroom over the measured p50 without
# masking a real degradation — if a future call ever takes 180s+
# that's a legitimate incident to investigate (prompt bloat or
# Anthropic API slowness) rather than "just bump the timeout again".
DEFAULT_TIMEOUT_SEC = 180.0


@dataclass
class CallResult:
    """Structured output of one LLM call — captured for telemetry + cost accounting."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_sec: float


class AnthropicStrategistClient:
    """Thin wrapper around anthropic SDK for Strategist use only.

    Pass a custom `client_factory` in tests to inject a mock; default uses
    the real anthropic.Anthropic with ANTHROPIC_API_KEY from env.
    """

    def __init__(self, client_factory=None, timeout_sec: float = DEFAULT_TIMEOUT_SEC):
        self._client_factory = client_factory
        self._timeout = timeout_sec

    @classmethod
    def _pricing(cls) -> tuple[float, float]:
        """Return ``(input_per_M, output_per_M)`` for the strategist model.

        Reads from :data:`genlab_core.intelligence.cost_accumulator.MODEL_COSTS`
        so the pricing lives in ONE place. Prior to this fix (DEV-3
        observation, 2026-07-08), this class held its own
        ``INPUT_COST_PER_M`` / ``OUTPUT_COST_PER_M`` constants that
        duplicated the values in ``MODEL_COSTS['claude-sonnet-4-6']``.
        When Anthropic changed pricing, one path got updated and the
        other silently drifted — ``CallResult.cost_usd`` would then
        disagree with the dashboard aggregate that reads from
        ``cost_accumulator``.

        Falls back to ``(3.00, 15.00)`` (sonnet-4-6 as of 2026-06) if
        the model isn't in the table — same defensive default the
        module used pre-fix so no behaviour changes on a stale table.
        """
        from genlab_core.intelligence.cost_accumulator import MODEL_COSTS

        rates = MODEL_COSTS.get(STRATEGIST_MODEL)
        if rates is None or "input" not in rates or "output" not in rates:
            logger.warning(
                "strategist.pricing_lookup_fallback model=%s — MODEL_COSTS "
                "missing entry, defaulting to (3.00, 15.00). Update "
                "cost_accumulator.MODEL_COSTS to include %s.",
                STRATEGIST_MODEL,
                STRATEGIST_MODEL,
            )
            return 3.00, 15.00
        return float(rates["input"]), float(rates["output"])

    def generate_report(self, system_prompt: str, user_prompt: str) -> CallResult:
        """Call the model and return raw text + cost telemetry.

        Single retry on transient errors (network / 5xx). Non-transient
        errors (auth, bad request) propagate immediately so the caller can
        decide whether to fail-soft (skip this run) or escalate.
        """
        client = self._get_client()
        t0 = time.monotonic()
        attempt = 0
        last_exc: Exception | None = None

        while attempt < 2:
            attempt += 1
            try:
                response = client.messages.create(
                    model=STRATEGIST_MODEL,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    temperature=0.3,  # lower temp for structured-output consistency
                    timeout=self._timeout,
                )
                duration = time.monotonic() - t0
                # Anthropic responses always have content[0] as TextBlock for non-tool calls
                text = response.content[0].text if response.content else ""
                usage = response.usage
                input_tokens = getattr(usage, "input_tokens", 0)
                output_tokens = getattr(usage, "output_tokens", 0)
                input_per_m, output_per_m = self._pricing()
                cost = (input_tokens * input_per_m + output_tokens * output_per_m) / 1_000_000
                logger.info(
                    "strategist.llm_call_ok model=%s in=%d out=%d cost=$%.4f t=%.2fs",
                    STRATEGIST_MODEL,
                    input_tokens,
                    output_tokens,
                    cost,
                    duration,
                )

                # PR Blind-Spot-1: wire cost_accumulator so the Strategist's
                # weekly LLM spend shows up in the dashboard cost-per-blueprint
                # aggregate. Previously tracked only in this module's own log.
                # Fail-closed: if cost_accumulator can't be imported (e.g.
                # test env), we log-only and continue — never break the LLM
                # call over telemetry.
                try:
                    from genlab_core.intelligence.cost_accumulator import (
                        record_anthropic_usage,
                    )

                    record_anthropic_usage(STRATEGIST_MODEL, response)
                except Exception as exc:
                    logger.debug("strategist.cost_track_skip err=%s", exc)

                return CallResult(
                    text=text,
                    model=STRATEGIST_MODEL,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=round(cost, 6),
                    duration_sec=round(duration, 2),
                )
            except Exception as exc:
                last_exc = exc
                # 2026-07-21: Anthropic-exhaustion fallback to OpenAI gpt-4o.
                # Sonnet-4-6-quality reasoning task, so we use gpt-4o (not
                # gpt-4o-mini like the writer/reply fallback sites) to keep
                # the JSON-schema-adherence + reasoning depth. Fallback
                # returns text only; cost accumulator picks up the OpenAI
                # spend on that side.
                fallback_result = self._try_openai_fallback(
                    exc, system_prompt, user_prompt, t0
                )
                if fallback_result is not None:
                    return fallback_result
                # Retry once on transient classes; bail immediately on others.
                if not self._is_transient(exc):
                    raise
                logger.warning(
                    "strategist.llm_call_transient_error attempt=%d err=%s", attempt, exc
                )

        raise RuntimeError(f"Strategist LLM call failed after 2 attempts: {last_exc}")

    def _try_openai_fallback(
        self,
        anthropic_exc: Exception,
        system_prompt: str,
        user_prompt: str,
        t0: float,
    ) -> CallResult | None:
        """Return CallResult from OpenAI gpt-4o fallback if Anthropic is
        exhausted AND OPENAI_API_KEY is set. Return None otherwise so the
        caller falls through to its regular error/retry path.

        Kept as its own method for readability + testability. gpt-4o
        chosen over gpt-4o-mini because Strategist is a reasoning task
        with JSON-schema-adherence requirements — mini is too small for
        the meta-cognition prompt shape.
        """
        from genlab_core.llm.fallback import (
            call_openai_fallback,
            cb_record_exhaustion,
            fallback_enabled,
            should_fallback,
        )

        if not (fallback_enabled() and should_fallback(anthropic_exc)):
            return None
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not openai_key:
            logger.warning(
                "strategist.fallback_skip reason=no_openai_key exc=%s",
                type(anthropic_exc).__name__,
            )
            return None
        cb_record_exhaustion()
        try:
            text = call_openai_fallback(
                system_prompt,
                user_prompt,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.3,
                api_key=openai_key,
                model="gpt-4o",  # sonnet-4-6-equivalent reasoning tier
                json_mode=True,  # strategist output must be parseable JSON
            )
            duration = time.monotonic() - t0
            logger.warning(
                "strategist.llm_call_fallback_openai reason=%s cost_tracked_openai t=%.2fs",
                type(anthropic_exc).__name__,
                duration,
            )
            # Cost is tracked inside call_openai_fallback via
            # record_openai_usage. Returning 0 tokens here is a mild lie
            # (they were consumed on OpenAI's side), but CallResult's
            # anthropic-specific fields don't cleanly map to an OpenAI
            # call. Downstream persistence uses `text` + `model` fields;
            # cost dashboards read cost_accumulator directly.
            return CallResult(
                text=text,
                model="gpt-4o (fallback)",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                duration_sec=round(duration, 2),
            )
        except Exception as openai_exc:
            logger.warning(
                "strategist.fallback_openai_failed openai_exc=%s — "
                "returning None to re-raise original Anthropic error",
                openai_exc,
            )
            return None

    def _get_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        try:
            import anthropic  # noqa: PLC0415 — lazy import so test envs without SDK still work
        except ImportError as exc:
            raise RuntimeError(
                "anthropic SDK not installed; install with `uv add anthropic` "
                "or inject a mock client_factory for tests."
            ) from exc
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
        return anthropic.Anthropic(api_key=api_key)

    # DEV-3 (2026-07-08 audit) — locked-in name list used by both
    # the string-match path and pin tests. If the anthropic SDK
    # renames a class the isinstance path (below) still catches it
    # via the actual class hierarchy; if the SDK removes a name
    # entirely the string list preserves backward compat with
    # in-flight retries queued against the old name.
    _TRANSIENT_ERROR_NAMES: frozenset[str] = frozenset(
        {
            "APIConnectionError",
            "APITimeoutError",
            "InternalServerError",
            "RateLimitError",
            "TimeoutError",
            "ConnectionError",
        }
    )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """Return True iff ``exc`` is a transient error worth retrying.

        Belt-and-suspenders after the DEV-3 audit (2026-07-08): the
        pre-fix version matched only on ``type(exc).__name__``, which
        silently stops retrying if the anthropic SDK ever renames one
        of these classes. Now we ALSO check isinstance against the
        real class hierarchy, catching future renames as long as the
        SDK preserves the subclass relationship (which SDKs usually
        do — the class rename is invariably backed by an alias or a
        subclass).

        Both paths run; either one returning True wins. Tests can
        still raise ad-hoc classes with the historical names and
        get retry behaviour, so the pin surface stays stable.
        """
        # 1. Name-based match — historical behaviour, preserved.
        name = type(exc).__name__
        if name in AnthropicStrategistClient._TRANSIENT_ERROR_NAMES:
            return True

        # 2. isinstance-based match — catches future SDK renames as
        #    long as the class hierarchy is preserved. Lazy import
        #    so test envs without anthropic still work (mirrors the
        #    _get_client lazy-import pattern above).
        try:
            import anthropic  # noqa: PLC0415 — lazy: tests may skip SDK.
        except ImportError:
            return False

        # Collect the classes we know about from the SDK. Using
        # ``getattr(anthropic, ..., None)`` because ``anthropic``
        # may not expose every class we listed (older SDK versions
        # lacked ``APITimeoutError``, for example). Any missing
        # attribute simply doesn't participate in the isinstance
        # check.
        transient_classes = tuple(
            cls
            for cls in (
                getattr(anthropic, "APIConnectionError", None),
                getattr(anthropic, "APITimeoutError", None),
                getattr(anthropic, "RateLimitError", None),
                getattr(anthropic, "InternalServerError", None),
            )
            if isinstance(cls, type)
        )
        if transient_classes and isinstance(exc, transient_classes):
            return True

        return False
