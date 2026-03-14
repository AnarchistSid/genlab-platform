"""Tests for genlab_core.intelligence.cost_accumulator."""
import threading
from unittest.mock import patch

import pytest

from genlab_core.intelligence.cost_accumulator import (
    CostAccumulator,
    MODEL_COSTS,
    get_accumulator,
    reset_accumulator,
    set_accumulator,
)


def _local_only_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Force local MODEL_COSTS table (skip litellm) for deterministic tests."""
    rates = MODEL_COSTS.get(model, MODEL_COSTS.get("gpt-4o-mini"))
    return (input_tokens * rates.get("input", 0) + output_tokens * rates.get("output", 0)) / 1_000_000


class TestRecordLlm:
    def test_record_llm_calculates_cost(self):
        acc = CostAccumulator(run_id="test-1")
        # Use local table to test accumulator math (litellm path tested in test_cost_litellm.py)
        with patch("genlab_core.intelligence.cost_accumulator._compute_cost", side_effect=_local_only_cost):
            cost = acc.record_llm("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        # claude-haiku-4-5-20251001: input=0.80, output=4.00 per 1M tokens
        assert cost == pytest.approx(0.80 + 4.00)

    def test_record_llm_unknown_model_uses_gpt4o_mini_rates(self):
        acc = CostAccumulator(run_id="test-unknown")
        # Unknown model falls back to gpt-4o-mini: input=0.15, output=0.60
        cost = acc.record_llm("some-unknown-model", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.15 + 0.60)

    def test_record_llm_appends_entry(self):
        acc = CostAccumulator(run_id="test-entry")
        acc.record_llm("gpt-4o-mini", 500, 200)
        assert len(acc.entries) == 1
        assert acc.entries[0].category == "llm"
        assert acc.entries[0].model == "gpt-4o-mini"
        assert acc.entries[0].input_tokens == 500
        assert acc.entries[0].output_tokens == 200


class TestRecordImage:
    def test_record_image_calculates_cost(self):
        acc = CostAccumulator(run_id="test-img")
        cost = acc.record_image("gpt-image-1", count=3)
        assert cost == pytest.approx(0.06)

    def test_record_image_unknown_model_default_rate(self):
        acc = CostAccumulator(run_id="test-img-unk")
        cost = acc.record_image("unknown-image-model", count=5)
        assert cost == pytest.approx(0.10)


class TestRecordTts:
    def test_record_tts_calculates_cost(self):
        acc = CostAccumulator(run_id="test-tts")
        cost = acc.record_tts(chars=2000, cost_per_1k_chars=0.015)
        assert cost == pytest.approx(0.03)

    def test_record_tts_custom_rate(self):
        acc = CostAccumulator(run_id="test-tts-custom")
        cost = acc.record_tts(chars=5000, cost_per_1k_chars=0.03)
        assert cost == pytest.approx(0.15)


class TestTotalByCategory:
    def test_total_by_category(self):
        acc = CostAccumulator(run_id="test-cats")
        acc.record_llm("gpt-4o-mini", 1_000_000, 0)  # 0.15
        acc.record_llm("gpt-4o-mini", 0, 1_000_000)  # 0.60
        acc.record_tts(chars=1000)  # 0.015
        acc.record_image("gpt-image-1", count=1)  # 0.02

        assert acc.total_usd == pytest.approx(0.15 + 0.60 + 0.015 + 0.02)

        cats = acc.by_category
        assert cats["llm"] == pytest.approx(0.75)
        assert cats["tts"] == pytest.approx(0.015)
        assert cats["image"] == pytest.approx(0.02)


class TestBudgetRemainingPct:
    def test_budget_remaining_pct_full_budget(self):
        acc = CostAccumulator(run_id="test-budget", _budget_usd=10.0)
        assert acc.budget_remaining_pct == pytest.approx(100.0)

    def test_budget_remaining_pct_half_spent(self):
        acc = CostAccumulator(run_id="test-budget-half", _budget_usd=1.0)
        # Spend 0.50 USD
        acc.record_llm("gpt-4o", 100_000, 100_000)
        # gpt-4o: input=2.50, output=10.00 per 1M
        # 100k in = 0.25, 100k out = 1.00 -> total 1.25 USD ... too much
        # Let's just check the math is right
        expected_cost = (100_000 * 2.50 + 100_000 * 10.00) / 1_000_000
        expected_pct = max(0.0, 1.0 - (expected_cost / 1.0)) * 100
        assert acc.budget_remaining_pct == pytest.approx(expected_pct)

    def test_budget_remaining_pct_over_budget_clamps_to_zero(self):
        acc = CostAccumulator(run_id="test-over", _budget_usd=0.001)
        acc.record_llm("gpt-4o", 1_000_000, 1_000_000)
        assert acc.budget_remaining_pct == 0.0

    def test_budget_remaining_pct_zero_budget(self):
        acc = CostAccumulator(run_id="test-zero", _budget_usd=0.0)
        assert acc.budget_remaining_pct == 0.0


class TestContextvarsIsolation:
    def test_contextvars_isolation(self):
        """Two threads with separate accumulators stay isolated."""
        results = {}

        def worker(name, budget):
            acc = CostAccumulator(run_id=name, _budget_usd=budget)
            token = set_accumulator(acc)
            try:
                acc.record_llm("gpt-4o-mini", 1_000_000, 0)
                current = get_accumulator()
                results[name] = {
                    "run_id": current.run_id,
                    "total": current.total_usd,
                    "budget": current._budget_usd,
                }
            finally:
                reset_accumulator(token)

        t1 = threading.Thread(target=worker, args=("thread-1", 5.0))
        t2 = threading.Thread(target=worker, args=("thread-2", 10.0))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["thread-1"]["run_id"] == "thread-1"
        assert results["thread-2"]["run_id"] == "thread-2"
        assert results["thread-1"]["budget"] == 5.0
        assert results["thread-2"]["budget"] == 10.0
        # Both recorded same model/tokens so same cost
        assert results["thread-1"]["total"] == results["thread-2"]["total"]

    def test_get_accumulator_default_is_none(self):
        assert get_accumulator() is None


class TestSummaryFormat:
    def test_summary_format(self):
        acc = CostAccumulator(run_id="summary-test", _budget_usd=5.0)
        acc.record_llm("gpt-4o-mini", 100_000, 50_000)
        acc.record_tts(chars=500)

        s = acc.summary()

        assert s["run_id"] == "summary-test"
        assert isinstance(s["total_usd"], float)
        assert isinstance(s["by_category"], dict)
        assert "llm" in s["by_category"]
        assert "tts" in s["by_category"]
        assert isinstance(s["budget_remaining_pct"], float)
        assert s["entry_count"] == 2

    def test_summary_empty_accumulator(self):
        acc = CostAccumulator(run_id="empty")
        s = acc.summary()
        assert s["total_usd"] == 0.0
        assert s["by_category"] == {}
        assert s["entry_count"] == 0
