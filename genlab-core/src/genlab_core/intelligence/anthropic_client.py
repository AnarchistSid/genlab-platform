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

    # Sonnet 4.6 pricing (as of 2026-06; recheck per pricing page when updating model)
    INPUT_COST_PER_M = 3.00
    OUTPUT_COST_PER_M = 15.00

    def __init__(self, client_factory=None, timeout_sec: float = DEFAULT_TIMEOUT_SEC):
        self._client_factory = client_factory
        self._timeout = timeout_sec

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
                cost = (
                    input_tokens * self.INPUT_COST_PER_M + output_tokens * self.OUTPUT_COST_PER_M
                ) / 1_000_000
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
                # Retry once on transient classes; bail immediately on others.
                if not self._is_transient(exc):
                    raise
                logger.warning(
                    "strategist.llm_call_transient_error attempt=%d err=%s", attempt, exc
                )

        raise RuntimeError(f"Strategist LLM call failed after 2 attempts: {last_exc}")

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

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """Heuristic — anthropic SDK transient errors typically have these names."""
        name = type(exc).__name__
        return name in {
            "APIConnectionError",
            "APITimeoutError",
            "InternalServerError",
            "RateLimitError",
            "TimeoutError",
            "ConnectionError",
        }
