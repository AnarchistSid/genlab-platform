"""Pin: ``get_model_with_budget`` cascades to cheaper models as the
CostAccumulator burns through the per-run budget (Lever H).

Pre-2026-06-21 every caller used ``get_model()`` with the default
``budget_ratio=0.0``, so the existing cascade logic in
``cost/model_router.py:48-58`` was dead code — the system always
returned the task's configured (most-expensive) model regardless of
how much budget the current run had already consumed.

Lever H adds ``get_model_with_budget()`` that reads the
``CostAccumulator`` automatically and computes
``budget_ratio = total_usd / _budget_usd``. The cascade is:

  - < 10% budget consumed: use the task's configured model
  - 10-25% consumed: cascade to ``expensive_to_mid`` fallback
  - >= 25% consumed: cascade to ``mid_to_cheapest`` fallback

These pins lock in:

  1. No accumulator → falls through to ``get_model(task, 0.0)``
     (preserves pre-Lever-H behavior for tests / ad-hoc scripts).
  2. Accumulator with $0 spent → ``ratio=0.0`` → expensive model.
  3. Accumulator at 15% budget → ``ratio=0.15`` → mid fallback model.
  4. Accumulator at 30% budget → ``ratio=0.30`` → cheapest fallback.
  5. Zero-budget accumulator (misconfigured) → falls through cleanly.
  6. Exception in accumulator path → falls through to no-cascade
     (cost-tracking failure MUST NOT block model selection).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def _writable_config(tmp_path, monkeypatch):
    """Each test gets a known model_routing.yaml so the cascade tests
    can assert on deterministic model strings."""
    cfg_path = tmp_path / "model_routing.yaml"
    cfg_path.write_text(
        "default_model: claude-haiku-4-5-20251001\n"
        "task_routing:\n"
        "  test_task: claude-sonnet-4-6\n"
        "budget_fallbacks:\n"
        "  expensive_to_mid: claude-haiku-4-5-20251001\n"
        "  mid_to_cheapest: gpt-4o-mini\n"
    )
    monkeypatch.setenv("MODEL_ROUTING_CONFIG", str(cfg_path))
    # Clear the lru_cache so the new file is picked up.
    from genlab_core.cost.model_router import _load_routing_config

    _load_routing_config.cache_clear()
    yield cfg_path
    _load_routing_config.cache_clear()


def _make_accumulator(total_usd: float, budget_usd: float = 5.0):
    """Build a real CostAccumulator with a known spend total."""
    from genlab_core.intelligence.cost_accumulator import CostAccumulator, CostEntry

    acc = CostAccumulator()
    acc._budget_usd = budget_usd
    if total_usd > 0:
        # Append a single entry with the desired cost so total_usd matches.
        acc.entries.append(
            CostEntry(
                category="llm",
                model="claude-sonnet-4-6",
                input_tokens=0,
                output_tokens=0,
                cost_usd=total_usd,
            )
        )
    return acc


class TestBudgetAwareCascade:
    def test_no_accumulator_returns_task_model(self, _writable_config):
        """When no CostAccumulator is active (test/script context), the
        wrapper falls through to ``get_model(task, 0.0)`` — same model
        as a pre-Lever-H caller would have got."""
        from genlab_core.cost.model_router import get_model_with_budget

        # No accumulator set in this context → get_accumulator() returns None
        with patch(
            "genlab_core.intelligence.cost_accumulator.get_accumulator",
            return_value=None,
        ):
            result = get_model_with_budget("test_task")
        assert result == "claude-sonnet-4-6", (
            f"Expected task's configured model when no accumulator; got {result}"
        )

    def test_below_10pct_budget_returns_task_model(self, _writable_config):
        """Run has consumed <10% of its budget → no cascade, expensive model."""
        from genlab_core.cost.model_router import get_model_with_budget

        acc = _make_accumulator(total_usd=0.40, budget_usd=5.0)  # 8% spent
        with patch(
            "genlab_core.intelligence.cost_accumulator.get_accumulator",
            return_value=acc,
        ):
            result = get_model_with_budget("test_task")
        assert result == "claude-sonnet-4-6"

    def test_15pct_budget_cascades_to_mid_model(self, _writable_config):
        """Run has consumed 15% → cascade to ``expensive_to_mid``."""
        from genlab_core.cost.model_router import get_model_with_budget

        acc = _make_accumulator(total_usd=0.75, budget_usd=5.0)  # 15% spent
        with patch(
            "genlab_core.intelligence.cost_accumulator.get_accumulator",
            return_value=acc,
        ):
            result = get_model_with_budget("test_task")
        assert result == "claude-haiku-4-5-20251001", (
            f"Expected mid-tier fallback at 15% budget; got {result}"
        )

    def test_30pct_budget_cascades_to_cheapest_model(self, _writable_config):
        """Run has consumed 30% → cascade to ``mid_to_cheapest``."""
        from genlab_core.cost.model_router import get_model_with_budget

        acc = _make_accumulator(total_usd=1.50, budget_usd=5.0)  # 30% spent
        with patch(
            "genlab_core.intelligence.cost_accumulator.get_accumulator",
            return_value=acc,
        ):
            result = get_model_with_budget("test_task")
        assert result == "gpt-4o-mini", f"Expected cheapest fallback at 30% budget; got {result}"


class TestRobustness:
    def test_zero_budget_falls_through_to_no_cascade(self, _writable_config):
        """Misconfigured accumulator with ``_budget_usd=0`` shouldn't
        crash or trigger a divide-by-zero — just return the task model."""
        from genlab_core.cost.model_router import get_model_with_budget

        acc = _make_accumulator(total_usd=0.5, budget_usd=0.0)
        with patch(
            "genlab_core.intelligence.cost_accumulator.get_accumulator",
            return_value=acc,
        ):
            result = get_model_with_budget("test_task")
        assert result == "claude-sonnet-4-6"

    def test_accumulator_exception_falls_through(self, _writable_config):
        """If get_accumulator() itself raises (import failure, contextvar
        bug, etc.), the wrapper MUST NOT propagate — cost tracking is
        non-essential plumbing and must never block model selection."""
        from genlab_core.cost.model_router import get_model_with_budget

        with patch(
            "genlab_core.intelligence.cost_accumulator.get_accumulator",
            side_effect=RuntimeError("simulated cost-tracking failure"),
        ):
            result = get_model_with_budget("test_task")
        assert result == "claude-sonnet-4-6", (
            f"Expected fallback to no-cascade on accumulator exception; got {result}"
        )

    def test_overspend_clamps_at_100pct(self, _writable_config):
        """If a run somehow goes >100% of budget, the ratio clamps at
        1.0 — doesn't try to look up nonsense thresholds."""
        from genlab_core.cost.model_router import get_model_with_budget

        acc = _make_accumulator(total_usd=10.0, budget_usd=5.0)  # 200% spent
        with patch(
            "genlab_core.intelligence.cost_accumulator.get_accumulator",
            return_value=acc,
        ):
            result = get_model_with_budget("test_task")
        # 200% → clamped to 100% → still >= 0.25 → cheapest cascade
        assert result == "gpt-4o-mini"
