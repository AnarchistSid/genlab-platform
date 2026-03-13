"""Budget-aware model fallback cascade."""
from __future__ import annotations

import logging
from typing import Optional

from .cost_accumulator import get_accumulator

logger = logging.getLogger(__name__)

# Thresholds as % of budget consumed -> action
FALLBACK_CASCADE = [
    (40, "claude-haiku-4-5-20251001", "gpt-4o-mini"),  # 40%+ -> cheapest models only
    (25, "gpt-4o", "gpt-4o-mini"),  # 25%+ -> downgrade gpt-4o
    (10, "claude-sonnet-4-5-20250514", "claude-haiku-4-5-20251001"),  # 10%+ -> downgrade sonnet
]


class BudgetGuard:
    """Check budget before LLM calls, suggest cheaper models if over threshold."""

    def check_model(self, requested_model: str) -> str:
        acc = get_accumulator()
        if acc is None:
            return requested_model

        spent_pct = 100 - acc.budget_remaining_pct

        for threshold_pct, from_model, to_model in FALLBACK_CASCADE:
            if spent_pct >= threshold_pct and requested_model == from_model:
                logger.warning(
                    "[BudgetGuard] %.1f%% budget used — downgrading %s -> %s",
                    spent_pct,
                    from_model,
                    to_model,
                )
                return to_model

        return requested_model

    def should_skip(self) -> bool:
        """Return True if budget is completely exhausted (>95%)."""
        acc = get_accumulator()
        if acc is None:
            return False
        return acc.budget_remaining_pct < 5.0


# Module-level singleton
budget_guard = BudgetGuard()
