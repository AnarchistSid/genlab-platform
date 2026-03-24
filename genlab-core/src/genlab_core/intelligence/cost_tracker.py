"""LLM API cost tracker — estimates costs per pipeline run.

Tracks token usage from Anthropic Claude Haiku calls and estimates
costs. Writes per-run cost summaries to the run report.

Pricing (Claude Haiku 4.5, as of March 2026):
  - Input: $0.80 / 1M tokens
  - Output: $4.00 / 1M tokens
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Claude Haiku 4.5 pricing (USD per 1M tokens)
_INPUT_PRICE_PER_M = 0.80
_OUTPUT_PRICE_PER_M = 4.00


@dataclass
class RunCosts:
    """Accumulated costs for a single pipeline run."""

    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0
    stages: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, stage: str, input_tokens: int, output_tokens: int) -> None:
        """Record a single API call's token usage."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.api_calls += 1

        if stage not in self.stages:
            self.stages[stage] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        self.stages[stage]["input_tokens"] += input_tokens
        self.stages[stage]["output_tokens"] += output_tokens
        self.stages[stage]["calls"] += 1

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate total cost in USD."""
        input_cost = (self.input_tokens / 1_000_000) * _INPUT_PRICE_PER_M
        output_cost = (self.output_tokens / 1_000_000) * _OUTPUT_PRICE_PER_M
        return round(input_cost + output_cost, 4)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for run report."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "api_calls": self.api_calls,
            "estimated_cost_usd": self.estimated_cost_usd,
            "by_stage": self.stages,
        }


# Module-level singleton for the current run
_current_run: RunCosts | None = None


def start_run() -> RunCosts:
    """Start tracking costs for a new pipeline run."""
    global _current_run
    _current_run = RunCosts()
    return _current_run


def record_call(stage: str, input_tokens: int, output_tokens: int) -> None:
    """Record an API call's cost. No-op if no run is active."""
    if _current_run is not None:
        _current_run.record(stage, input_tokens, output_tokens)


def get_run_costs() -> dict[str, Any] | None:
    """Get current run's cost summary."""
    if _current_run is None:
        return None
    return _current_run.to_dict()


def end_run() -> dict[str, Any] | None:
    """End cost tracking and return summary."""
    global _current_run
    result = get_run_costs()
    _current_run = None
    return result
