"""Pin Phase 2.D cost budget gate."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from genlab_core.cost.budget_gate import (
    BudgetStatus,
    ThrottleLevel,
    get_status,
    get_throttle_level,
    is_call_allowed,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache_and_bypass(monkeypatch):
    monkeypatch.delenv("GENLAB_COST_BUDGET_DISABLED", raising=False)
    reset_cache()
    yield
    reset_cache()


def _mock_spend(usd: float):
    """Stub _daily_llm_spend_usd via module-level patch."""
    return patch(
        "genlab_core.cost.budget_gate._daily_llm_spend_usd",
        return_value=usd,
    )


class TestThresholdLadder:
    def test_none_below_5(self):
        with _mock_spend(4.99):
            assert get_throttle_level() == ThrottleLevel.NONE

    def test_reduce_50_at_5(self):
        with _mock_spend(5.0):
            assert get_throttle_level() == ThrottleLevel.REDUCE_50PCT

    def test_reduce_50_between_5_and_10(self):
        with _mock_spend(7.5):
            assert get_throttle_level() == ThrottleLevel.REDUCE_50PCT

    def test_pause_optional_at_10(self):
        with _mock_spend(10.0):
            assert get_throttle_level() == ThrottleLevel.PAUSE_OPTIONAL

    def test_pause_between_10_and_20(self):
        with _mock_spend(15.0):
            assert get_throttle_level() == ThrottleLevel.PAUSE_OPTIONAL

    def test_emergency_at_20(self):
        with _mock_spend(20.0):
            assert get_throttle_level() == ThrottleLevel.EMERGENCY_SHUTOFF

    def test_emergency_above_20(self):
        with _mock_spend(99.0):
            assert get_throttle_level() == ThrottleLevel.EMERGENCY_SHUTOFF


class TestCallerRouting:
    def test_essential_allowed_at_none(self):
        with _mock_spend(0.0):
            assert is_call_allowed("essential")

    def test_essential_allowed_at_reduce(self):
        with _mock_spend(7.0):
            assert is_call_allowed("essential")

    def test_essential_allowed_at_pause(self):
        with _mock_spend(12.0):
            assert is_call_allowed("essential")

    def test_essential_blocked_at_emergency(self):
        with _mock_spend(25.0):
            assert not is_call_allowed("essential")

    def test_optional_allowed_at_none(self):
        with _mock_spend(0.0):
            assert is_call_allowed("optional")

    def test_optional_blocked_at_pause(self):
        with _mock_spend(12.0):
            assert not is_call_allowed("optional")

    def test_optional_blocked_at_emergency(self):
        with _mock_spend(25.0):
            assert not is_call_allowed("optional")

    def test_unknown_caller_type_defaults_to_optional(self):
        with _mock_spend(12.0):
            # unknown → treated as optional → blocked at pause
            assert not is_call_allowed("weird_new_caller")

    def test_default_caller_type_is_optional(self):
        with _mock_spend(12.0):
            assert not is_call_allowed()


class TestBypass:
    def test_bypass_returns_none_regardless_of_spend(self, monkeypatch):
        monkeypatch.setenv("GENLAB_COST_BUDGET_DISABLED", "1")
        with _mock_spend(100.0):
            assert get_throttle_level() == ThrottleLevel.NONE

    def test_bypass_allows_all_callers(self, monkeypatch):
        monkeypatch.setenv("GENLAB_COST_BUDGET_DISABLED", "true")
        with _mock_spend(100.0):
            assert is_call_allowed("essential")
            assert is_call_allowed("optional")


class TestBudgetStatus:
    def test_status_shape(self):
        with _mock_spend(3.5):
            s = get_status()
        assert isinstance(s, BudgetStatus)
        assert s.spend_today_usd == 3.5
        assert s.throttle_level == ThrottleLevel.NONE
        assert s.reduce_50_threshold == 5.0
        assert s.pause_threshold == 10.0
        assert s.emergency_threshold == 20.0


class TestThresholdMonotonicity:
    """Regression pin: thresholds must stay ordered."""

    def test_thresholds_are_ordered(self):
        from genlab_core.cost.budget_gate import (
            _REDUCE_50_THRESHOLD_USD,
            _PAUSE_OPTIONAL_THRESHOLD_USD,
            _EMERGENCY_THRESHOLD_USD,
        )
        assert 0 < _REDUCE_50_THRESHOLD_USD < _PAUSE_OPTIONAL_THRESHOLD_USD < _EMERGENCY_THRESHOLD_USD
