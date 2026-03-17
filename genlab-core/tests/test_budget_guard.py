"""Tests for genlab_core.intelligence.budget_guard."""
import pytest
from genlab_core.intelligence.budget_guard import BudgetGuard
from genlab_core.intelligence.cost_accumulator import (
    CostAccumulator,
    get_accumulator,
    reset_accumulator,
    set_accumulator,
)


@pytest.fixture()
def guard():
    return BudgetGuard()


@pytest.fixture()
def acc_with_budget(request):
    """Create an accumulator with given budget and spend, set in contextvar."""
    budget = getattr(request, "param", {}).get("budget", 5.0)
    spend_pct = getattr(request, "param", {}).get("spend_pct", 0)

    acc = CostAccumulator(run_id="guard-test", _budget_usd=budget)

    # Spend the desired percentage via cheap tokens
    if spend_pct > 0:
        target_spend = budget * (spend_pct / 100)
        # gpt-4o-mini input rate: 0.15 per 1M tokens
        # tokens needed = target_spend * 1_000_000 / 0.15
        tokens_needed = int(target_spend * 1_000_000 / 0.15)
        acc.record_llm("gpt-4o-mini", tokens_needed, 0)

    token = set_accumulator(acc)
    yield acc
    reset_accumulator(token)


class TestNoAccumulator:
    def test_no_accumulator_returns_original_model(self, guard):
        # Ensure no accumulator is set
        assert get_accumulator() is None
        assert guard.check_model("claude-sonnet-4-5-20250514") == "claude-sonnet-4-5-20250514"
        assert guard.check_model("gpt-4o") == "gpt-4o"


class TestUnderThreshold:
    @pytest.mark.parametrize("acc_with_budget", [{"budget": 10.0, "spend_pct": 5}], indirect=True)
    def test_under_threshold_returns_original_model(self, guard, acc_with_budget):
        # 5% spent, all thresholds are 10%+
        assert guard.check_model("claude-sonnet-4-5-20250514") == "claude-sonnet-4-5-20250514"
        assert guard.check_model("gpt-4o") == "gpt-4o"
        assert guard.check_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"


class TestSonnetDowngrade:
    @pytest.mark.parametrize("acc_with_budget", [{"budget": 10.0, "spend_pct": 12}], indirect=True)
    def test_10pct_downgrades_sonnet(self, guard, acc_with_budget):
        # 12% spent -> sonnet should downgrade to haiku
        result = guard.check_model("claude-sonnet-4-5-20250514")
        assert result == "claude-haiku-4-5-20251001"

    @pytest.mark.parametrize("acc_with_budget", [{"budget": 10.0, "spend_pct": 12}], indirect=True)
    def test_10pct_keeps_gpt4o(self, guard, acc_with_budget):
        # 12% spent, gpt-4o threshold is 25% -> no downgrade yet
        assert guard.check_model("gpt-4o") == "gpt-4o"


class TestGpt4oDowngrade:
    @pytest.mark.parametrize("acc_with_budget", [{"budget": 10.0, "spend_pct": 28}], indirect=True)
    def test_25pct_downgrades_gpt4o(self, guard, acc_with_budget):
        # 28% spent -> gpt-4o should downgrade to gpt-4o-mini
        assert guard.check_model("gpt-4o") == "gpt-4o-mini"


class TestCheapestModels:
    @pytest.mark.parametrize("acc_with_budget", [{"budget": 10.0, "spend_pct": 45}], indirect=True)
    def test_40pct_uses_cheapest(self, guard, acc_with_budget):
        # 45% spent -> haiku should downgrade to gpt-4o-mini
        assert guard.check_model("claude-haiku-4-5-20251001") == "gpt-4o-mini"

    @pytest.mark.parametrize("acc_with_budget", [{"budget": 10.0, "spend_pct": 45}], indirect=True)
    def test_40pct_also_downgrades_sonnet_and_gpt4o(self, guard, acc_with_budget):
        # At 45%, all downgrade rules fire for their respective models
        assert guard.check_model("claude-sonnet-4-5-20250514") == "claude-haiku-4-5-20251001"
        assert guard.check_model("gpt-4o") == "gpt-4o-mini"


class TestShouldSkip:
    @pytest.mark.parametrize("acc_with_budget", [{"budget": 1.0, "spend_pct": 96}], indirect=True)
    def test_should_skip_at_95pct(self, guard, acc_with_budget):
        assert guard.should_skip() is True

    @pytest.mark.parametrize("acc_with_budget", [{"budget": 10.0, "spend_pct": 80}], indirect=True)
    def test_should_not_skip_at_80pct(self, guard, acc_with_budget):
        assert guard.should_skip() is False

    def test_should_skip_no_accumulator(self, guard):
        assert guard.should_skip() is False
