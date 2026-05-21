"""Tests for the consecutive-only check_qc_collapse logic.

Previously the check summed 0% runs from a 5-run sliding window, which
made it sticky — historical 0% runs kept it firing for days after
recovery (2026-05-17 sports incident: latest run 25% pass, but two
pre-WARP 0% runs in the window kept qc_collapse alerting until manually
cleared).

After the fix: a single non-zero QC run clears the alert immediately.
"""

from __future__ import annotations

from genlab_core.monitoring.health_monitor import check_qc_collapse


def _report(pass_rate: str) -> dict:
    return {"metrics": {"qc": {"pass_rate": pass_rate}}}


class TestQcCollapseConsecutiveOnly:
    def test_two_consecutive_zeros_fires_critical(self):
        reports = [_report("0.0%"), _report("0.0%"), _report("50.0%")]
        alerts = check_qc_collapse(reports, "gaming")
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert "2 consecutive" in alerts[0].message

    def test_single_zero_no_alert(self):
        """One 0% run could be transient — needs 2 consecutive to fire."""
        reports = [_report("0.0%"), _report("50.0%")]
        alerts = check_qc_collapse(reports, "gaming")
        assert alerts == []

    def test_recovery_clears_alert(self):
        """The bug we just fixed — non-zero latest run clears even when
        history has many zeros.
        """
        reports = [
            _report("25.0%"),  # latest (recovery)
            _report("0.0%"),  # previous
            _report("0.0%"),  # before that
            _report("0.0%"),  # and before
        ]
        alerts = check_qc_collapse(reports, "sports")
        assert alerts == []

    def test_three_consecutive_zeros_fires(self):
        reports = [_report("0.0%")] * 3 + [_report("50.0%")]
        alerts = check_qc_collapse(reports, "movies")
        assert len(alerts) == 1
        assert "3 consecutive" in alerts[0].message

    def test_no_reports_no_alert(self):
        alerts = check_qc_collapse([], "anime")
        assert alerts == []

    def test_all_healthy_no_alert(self):
        reports = [_report("75.0%"), _report("80.0%"), _report("60.0%")]
        alerts = check_qc_collapse(reports, "ai_creators")
        assert alerts == []

    def test_alert_details_carry_count(self):
        reports = [_report("0.0%")] * 4
        alerts = check_qc_collapse(reports, "gaming")
        assert alerts[0].details["consecutive_zero_qc"] == 4
