"""Thread-safe per-run cost accumulation via contextvars."""
from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Cost rates per 1M tokens (as of 2026-03)
MODEL_COSTS: Dict[str, Dict[str, float]] = {
    # Anthropic
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-5-20250514": {"input": 3.00, "output": 15.00},
    # OpenAI
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-image-1": {"per_image": 0.02},
}

CATEGORIES = ("llm", "tts", "image", "compute", "media", "bandwidth")


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute USD cost. Primary: litellm. Fallback: local MODEL_COSTS table."""
    try:
        from litellm import completion_cost, ModelResponse, Choices, Message, Usage
        resp = ModelResponse(
            id="cost-calc",
            choices=[Choices(finish_reason="stop", index=0,
                           message=Message(content="", role="assistant"))],
            model=model,
            usage=Usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )
        cost = completion_cost(completion_response=resp, model=model)
        if cost and cost > 0:
            return cost
        logger.debug("[cost] litellm returned 0 for '%s' — using local table", model)
    except Exception as e:
        logger.debug("[cost] litellm failed for '%s': %s — using local table", model, e)

    # Fallback: local pricing table
    rates = MODEL_COSTS.get(model)
    if not rates:
        # Try prefix matching
        for key, r in MODEL_COSTS.items():
            if model.startswith(key) or key in model:
                rates = r
                break
    if not rates:
        logger.warning("[cost] unknown model '%s' — using gpt-4o-mini rates", model)
        rates = MODEL_COSTS.get("gpt-4o-mini", {"input": 0.15, "output": 0.60})

    return (input_tokens * rates.get("input", 0) + output_tokens * rates.get("output", 0)) / 1_000_000


@dataclass
class CostEntry:
    category: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    cost_usd: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class CostAccumulator:
    """Accumulates costs for a single pipeline run."""

    run_id: str = ""
    entries: list = field(default_factory=list)
    _budget_usd: float = 5.0

    def record_llm(self, model: str, input_tokens: int, output_tokens: int) -> float:
        cost = _compute_cost(model, input_tokens, output_tokens)
        entry = CostEntry(
            category="llm",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        self.entries.append(entry)
        return cost

    def record_image(self, model: str, count: int = 1) -> float:
        rates = MODEL_COSTS.get(model, {"per_image": 0.02})
        cost = count * rates.get("per_image", 0.02)
        entry = CostEntry(category="image", model=model, images=count, cost_usd=cost)
        self.entries.append(entry)
        return cost

    def record_tts(self, chars: int, cost_per_1k_chars: float = 0.015) -> float:
        cost = (chars / 1000) * cost_per_1k_chars
        entry = CostEntry(category="tts", model="tts", cost_usd=cost)
        self.entries.append(entry)
        return cost

    @property
    def total_usd(self) -> float:
        return sum(e.cost_usd for e in self.entries)

    @property
    def by_category(self) -> Dict[str, float]:
        cats: Dict[str, float] = {}
        for e in self.entries:
            cats[e.category] = cats.get(e.category, 0) + e.cost_usd
        return cats

    @property
    def budget_remaining_pct(self) -> float:
        if self._budget_usd <= 0:
            return 0.0
        return max(0.0, 1.0 - (self.total_usd / self._budget_usd)) * 100

    def summary(self) -> Dict:
        return {
            "run_id": self.run_id,
            "total_usd": round(self.total_usd, 4),
            "by_category": {k: round(v, 4) for k, v in self.by_category.items()},
            "budget_remaining_pct": round(self.budget_remaining_pct, 1),
            "entry_count": len(self.entries),
        }


# contextvars-based current accumulator
_current_accumulator: contextvars.ContextVar[Optional[CostAccumulator]] = (
    contextvars.ContextVar("cost_accumulator", default=None)
)


def get_accumulator() -> Optional[CostAccumulator]:
    return _current_accumulator.get()


def set_accumulator(acc: CostAccumulator) -> contextvars.Token:
    return _current_accumulator.set(acc)


def reset_accumulator(token: contextvars.Token) -> None:
    _current_accumulator.reset(token)
