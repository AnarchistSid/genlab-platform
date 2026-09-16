"""The runway guard must page with enough lead time to act on.

OPS-02 Step 3. Six credit outages between 2026-08-03 and 2026-08-22 happened
with this check present in the codebase and scheduled every 30 minutes. It
prevented none of them, and the mechanism was none of the usual suspects — not
unscheduled, not an unreachable threshold, not fires-but-only-logs. It
early-returns when ANTHROPIC_MONTHLY_BUDGET_USD is unset, which it was. Zero
rows in pipeline_alerts across the table's entire history confirm it never
fired once.

These simulate the low-balance state rather than waiting for a seventh outage
to validate a guard against outages.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from genlab_core.monitoring.checks import llm_cost


@pytest.fixture
def budget(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "10.00")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")


def _run(mtd: float, daily: float):
    with patch.object(llm_cost, "_fetch_month_to_date_llm_spend", return_value=mtd), \
         patch.object(llm_cost, "_fetch_daily_llm_costs",
                      return_value=[(f"d{i}", daily) for i in range(14)]):
        return llm_cost.check_llm_budget_runway()


class TestUnconfiguredIsTheRealMechanism:
    def test_unset_budget_returns_nothing(self, monkeypatch):
        """The actual cause of six unprevented outages."""
        monkeypatch.delenv("ANTHROPIC_MONTHLY_BUDGET_USD", raising=False)
        assert _run(mtd=9.0, daily=0.35) == []

    def test_empty_string_also_returns_nothing(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "")
        assert _run(mtd=9.0, daily=0.35) == []


class TestSevenDayWarning:
    def test_six_days_of_runway_now_warns(self, budget):
        """$2.10 left at $0.35/day = 6 days. Silent under the old 3-day
        threshold; this is the window in which a human can still act."""
        alerts = _run(mtd=7.90, daily=0.35)
        assert alerts, "6 days of runway produced no alert"
        assert alerts[0].severity == "warning"
        assert alerts[0].check == "llm_budget_runway_low"

    def test_comfortable_runway_stays_quiet(self, budget):
        """20 days left — no alert. The guard must not become background
        noise, or it joins the alerts nobody reads."""
        assert _run(mtd=3.00, daily=0.35) == []

    def test_one_day_is_critical(self, budget):
        alerts = _run(mtd=9.75, daily=0.35)
        assert alerts and alerts[0].severity == "critical"

    def test_exceeded_budget_is_critical(self, budget):
        alerts = _run(mtd=12.00, daily=0.35)
        assert alerts and alerts[0].check == "llm_budget_exceeded"
        assert alerts[0].severity == "critical"

    @pytest.mark.parametrize("mtd,expected", [
        (3.00, None),        # ~20 days
        (7.90, "warning"),   # ~6 days
        (9.50, "warning"),   # ~1.4 days
        (9.75, "critical"),  # ~0.7 days
    ])
    def test_escalation_ladder(self, budget, mtd, expected):
        alerts = _run(mtd=mtd, daily=0.35)
        got = alerts[0].severity if alerts else None
        assert got == expected, f"mtd={mtd} expected {expected}, got {got}"

    def test_threshold_constant_is_seven(self):
        assert llm_cost._RUNWAY_WARNING_DAYS == 7.0


class TestNoBurnNoAlert:
    def test_zero_burn_does_not_divide_by_zero(self, budget):
        assert _run(mtd=5.0, daily=0.0) == []
