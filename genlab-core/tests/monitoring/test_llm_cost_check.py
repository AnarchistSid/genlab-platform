"""Tests for genlab_core.monitoring.checks.llm_cost.

The predictive check fires WARNING/CRITICAL alerts before the balance
hits zero. Complements the reactive anthropic_credit_monitor.
"""

from __future__ import annotations

from unittest.mock import patch


class TestRunawaySpikeDetection:
    def test_no_data_returns_no_alerts(self, monkeypatch):
        from genlab_core.monitoring.checks import llm_cost

        with patch.object(llm_cost, "_fetch_daily_llm_costs", return_value=[]):
            assert llm_cost.check_llm_cost_runaway() == []

    def test_small_baseline_returns_no_alerts(self, monkeypatch):
        """< 3 days of history — can't compute stable median. Skip."""
        from genlab_core.monitoring.checks import llm_cost

        with patch.object(
            llm_cost,
            "_fetch_daily_llm_costs",
            return_value=[("2026-07-22", 0.1), ("2026-07-21", 0.1)],
        ):
            assert llm_cost.check_llm_cost_runaway() == []

    def test_zero_baseline_returns_no_alerts(self, monkeypatch):
        """When all history days are $0, we can't compute a ratio.
        Skip rather than divide-by-zero."""
        from genlab_core.monitoring.checks import llm_cost

        from datetime import UTC, datetime

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        with patch.object(
            llm_cost,
            "_fetch_daily_llm_costs",
            return_value=[
                (today, 5.0),
                ("2026-07-22", 0.0),
                ("2026-07-21", 0.0),
                ("2026-07-20", 0.0),
                ("2026-07-19", 0.0),
            ],
        ):
            assert llm_cost.check_llm_cost_runaway() == []

    def test_normal_day_no_alert(self, monkeypatch):
        from genlab_core.monitoring.checks import llm_cost

        from datetime import UTC, datetime

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        with patch.object(
            llm_cost,
            "_fetch_daily_llm_costs",
            return_value=[
                (today, 0.15),  # 1.5x median — below 3x threshold
                ("2026-07-22", 0.1),
                ("2026-07-21", 0.1),
                ("2026-07-20", 0.1),
                ("2026-07-19", 0.1),
            ],
        ):
            assert llm_cost.check_llm_cost_runaway() == []

    def test_below_absolute_floor_no_alert(self, monkeypatch):
        """Even a 100× ratio doesn't alert if today's absolute cost
        is under $1 — avoids noise on genuinely cheap days."""
        from genlab_core.monitoring.checks import llm_cost

        from datetime import UTC, datetime

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        with patch.object(
            llm_cost,
            "_fetch_daily_llm_costs",
            return_value=[
                (today, 0.5),  # 100x median but only $0.50
                ("2026-07-22", 0.005),
                ("2026-07-21", 0.005),
                ("2026-07-20", 0.005),
                ("2026-07-19", 0.005),
            ],
        ):
            assert llm_cost.check_llm_cost_runaway() == []

    def test_5x_spike_fires_warning(self, monkeypatch):
        """The exact shape of today's incident: $10 vs $0.002 median
        would fire a WARNING with 5000× ratio."""
        from genlab_core.monitoring.checks import llm_cost

        from datetime import UTC, datetime

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        with patch.object(
            llm_cost,
            "_fetch_daily_llm_costs",
            return_value=[
                (today, 10.0),  # today's actual spike
                ("2026-07-22", 0.002),
                ("2026-07-21", 0.002),
                ("2026-07-20", 0.0),
                ("2026-07-19", 0.0),
                ("2026-07-18", 0.07),
                ("2026-07-17", 0.09),
                ("2026-07-16", 0.07),
            ],
        ):
            alerts = llm_cost.check_llm_cost_runaway()
        assert len(alerts) == 1
        assert alerts[0].check == "llm_cost_runaway"
        assert alerts[0].severity == "warning"
        # Ratio should be huge — well above the 3x threshold
        assert alerts[0].details["ratio"] > 10


class TestBudgetRunwayProjection:
    def test_no_budget_env_skips(self, monkeypatch):
        from genlab_core.monitoring.checks import llm_cost

        monkeypatch.delenv("ANTHROPIC_MONTHLY_BUDGET_USD", raising=False)
        assert llm_cost.check_llm_budget_runway() == []

    def test_invalid_budget_skips(self, monkeypatch):
        from genlab_core.monitoring.checks import llm_cost

        monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "not_a_number")
        assert llm_cost.check_llm_budget_runway() == []

    def test_zero_budget_skips(self, monkeypatch):
        from genlab_core.monitoring.checks import llm_cost

        monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "0")
        assert llm_cost.check_llm_budget_runway() == []

    def test_budget_exceeded_fires_critical(self, monkeypatch):
        from genlab_core.monitoring.checks import llm_cost

        monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "5.0")
        with (
            patch.object(llm_cost, "_fetch_month_to_date_llm_spend", return_value=8.5),
            patch.object(
                llm_cost,
                "_fetch_daily_llm_costs",
                return_value=[("2026-07-23", 8.5)],
            ),
        ):
            alerts = llm_cost.check_llm_budget_runway()
        assert len(alerts) == 1
        assert alerts[0].check == "llm_budget_exceeded"
        assert alerts[0].severity == "critical"

    def test_seven_day_runway_no_alert(self, monkeypatch):
        """Plenty of runway — informational only, no alert."""
        from genlab_core.monitoring.checks import llm_cost

        monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "10.0")
        with (
            # $2 spent, $8 left, $0.10/day burn = 80 days runway
            patch.object(llm_cost, "_fetch_month_to_date_llm_spend", return_value=2.0),
            patch.object(
                llm_cost,
                "_fetch_daily_llm_costs",
                return_value=[(f"2026-07-{d}", 0.1) for d in range(23, 15, -1)],
            ),
        ):
            assert llm_cost.check_llm_budget_runway() == []

    def test_three_day_runway_fires_warning(self, monkeypatch):
        from genlab_core.monitoring.checks import llm_cost

        monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "10.0")
        with (
            # $9.5 spent, $0.5 left, $0.20/day burn = 2.5 days runway
            patch.object(llm_cost, "_fetch_month_to_date_llm_spend", return_value=9.5),
            patch.object(
                llm_cost,
                "_fetch_daily_llm_costs",
                return_value=[(f"2026-07-{d}", 0.20) for d in range(23, 15, -1)],
            ),
        ):
            alerts = llm_cost.check_llm_budget_runway()
        assert len(alerts) == 1
        assert alerts[0].check == "llm_budget_runway_low"
        assert alerts[0].severity == "warning"

    def test_one_day_runway_fires_critical(self, monkeypatch):
        from genlab_core.monitoring.checks import llm_cost

        monkeypatch.setenv("ANTHROPIC_MONTHLY_BUDGET_USD", "10.0")
        with (
            # $9.9 spent, $0.1 left, $0.20/day burn = 0.5 days runway
            patch.object(llm_cost, "_fetch_month_to_date_llm_spend", return_value=9.9),
            patch.object(
                llm_cost,
                "_fetch_daily_llm_costs",
                return_value=[(f"2026-07-{d}", 0.20) for d in range(23, 15, -1)],
            ),
        ):
            alerts = llm_cost.check_llm_budget_runway()
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"


class TestHealthMonitorWiring:
    """The check must be invoked from health_monitor.gather_alerts()."""

    def test_check_llm_cost_in_health_monitor_imports(self):
        import inspect

        from genlab_core.monitoring import health_monitor

        src = inspect.getsource(health_monitor)
        assert "check_llm_cost" in src, (
            "health_monitor must import + invoke check_llm_cost — "
            "otherwise the predictive alerts never fire in the sweep."
        )


class TestAutoFixValuesNotWhitelisted:
    """The auto_fix values are OPERATOR SUGGESTIONS, not completed
    actions. They MUST NOT be in _AUTO_FIX_COMPLETED_VALUES —
    otherwise the auto_fix_applied resolver would auto-close them and
    hide the incident. Same class-of-bug as the disk_pressure +
    anthropic_credit_exhausted incident on 2026-07-23."""

    def test_investigate_prefix_not_in_whitelist(self):
        from genlab_core.observability.alert_auto_resolver import (
            _AUTO_FIX_COMPLETED_PREFIXES,
            _AUTO_FIX_COMPLETED_VALUES,
        )

        for prefix in _AUTO_FIX_COMPLETED_PREFIXES:
            assert not prefix.startswith("Investigate"), (
                f"Suggestion prefix {prefix!r} slipped into completed "
                "whitelist — this would auto-resolve the runaway-spike "
                "alerts before operator sees them."
            )

    def test_top_up_not_in_whitelist(self):
        from genlab_core.observability.alert_auto_resolver import (
            _AUTO_FIX_COMPLETED_VALUES,
        )

        for value in _AUTO_FIX_COMPLETED_VALUES:
            assert "Top up" not in value, (
                f"Suggestion value {value!r} slipped into completed "
                "whitelist — this would auto-resolve budget-runway "
                "alerts before operator sees them."
            )
