"""2026-08-12: pin the run_report → pipeline_alerts wire.

Motivating incident: gaming's pipeline emitted
    slo_violations: ["Zero blueprints produced from 1 stories (videos_validated=1)"]
in every daily run_report for weeks. The only sink was an opt-in
Slack webhook (`GENLAB_SLO_ALERT_WEBHOOK`) that the operator hadn't
configured. Nothing surfaced on Mission Control. Silent zero-
blueprint outage went undetected until manual audit on 2026-08-12.

Fix: `run_report._write_slo_violations_to_alerts_table` writes one
`pipeline_alerts` row per violation. Called after `fire_slo_alert`,
fail-open (never blocks the run_report write).

These pins lock the wire so a refactor can't silently break it.
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.pipeline.stages.run_report import (
    _slo_check_name,
    _write_slo_violations_to_alerts_table,
)


class TestSloCheckName:
    """Stable check_name per violation TYPE so different SLOs create
    distinct alerts but repeats dedup (write_alerts_to_db keys on
    check_name + niche_id)."""

    def test_zero_blueprints_prefix(self):
        assert (
            _slo_check_name(
                "Zero blueprints produced from 1 stories (videos_validated=1)"
            )
            == "slo:zero_blueprints"
        )

    def test_qc_pass_rate_prefix(self):
        assert (
            _slo_check_name("QC pass rate 2/5 below 90% SLO")
            == "slo:qc_pass_rate"
        )

    def test_p95_pipeline_prefix(self):
        assert (
            _slo_check_name("P95 pipeline exceeded 600s SLO (813.2s)")
            == "slo:p95_pipeline"
        )

    def test_llm_cost_prefix(self):
        assert (
            _slo_check_name("LLM cost $6.83 exceeded $5.00 budget")
            == "slo:llm_cost"
        )

    def test_unrecognised_falls_through_to_other(self):
        assert _slo_check_name("some novel violation shape") == "slo:other"


class TestWriteSloViolationsToAlertsTable:
    def _base_report(self, **overrides) -> dict:
        report = {
            "niche_id": "gaming",
            "run_id": "gaming_20260812_040001",
            "status": "failed",
            "metrics": {"stories_count": 1, "blueprints_count": 0},
            "slo_violations": [
                "Zero blueprints produced from 1 stories (videos_validated=1)"
            ],
            "stage_failures": {"video_validation": 1},
            "bottleneck_stage": None,
            "bottleneck_reason": None,
        }
        report.update(overrides)
        return report

    def test_writes_one_alert_per_violation(self):
        report = self._base_report(
            slo_violations=[
                "Zero blueprints produced from 1 stories",
                "QC pass rate 2/5 below 90% SLO",
            ]
        )
        with patch(
            "genlab_core.monitoring.health_monitor.write_alerts_to_db",
            return_value=2,
        ) as mock_write:
            n = _write_slo_violations_to_alerts_table(report)
        assert n == 2
        mock_write.assert_called_once()
        alerts = mock_write.call_args.args[0]
        assert len(alerts) == 2
        # Distinct check names so dedup handles them independently
        assert {a.check for a in alerts} == {"slo:zero_blueprints", "slo:qc_pass_rate"}

    def test_no_violations_writes_nothing(self):
        report = self._base_report(slo_violations=[])
        with patch(
            "genlab_core.monitoring.health_monitor.write_alerts_to_db"
        ) as mock_write:
            n = _write_slo_violations_to_alerts_table(report)
        assert n == 0
        mock_write.assert_not_called()

    def test_severity_critical_when_status_failed(self):
        report = self._base_report(status="failed")
        with patch(
            "genlab_core.monitoring.health_monitor.write_alerts_to_db",
            return_value=1,
        ) as mock_write:
            _write_slo_violations_to_alerts_table(report)
        alerts = mock_write.call_args.args[0]
        assert alerts[0].severity == "critical"

    def test_severity_warning_when_status_partial(self):
        report = self._base_report(status="partial")
        with patch(
            "genlab_core.monitoring.health_monitor.write_alerts_to_db",
            return_value=1,
        ) as mock_write:
            _write_slo_violations_to_alerts_table(report)
        alerts = mock_write.call_args.args[0]
        assert alerts[0].severity == "warning"

    def test_details_carries_actionable_context(self):
        """Operator triaging an alert needs run_id, blueprints_count,
        stage_failures, and bottleneck info — all in details JSONB."""
        report = self._base_report()
        with patch(
            "genlab_core.monitoring.health_monitor.write_alerts_to_db",
            return_value=1,
        ) as mock_write:
            _write_slo_violations_to_alerts_table(report)
        alerts = mock_write.call_args.args[0]
        details = alerts[0].details
        assert details["run_id"] == "gaming_20260812_040001"
        assert details["status"] == "failed"
        assert details["blueprints_count"] == 0
        assert details["stage_failures"] == {"video_validation": 1}

    def test_niche_id_propagated(self):
        """Dedup keys on (check_name, niche_id) — same violation shape
        on gaming vs sports must create separate alerts."""
        report = self._base_report(niche_id="sports")
        with patch(
            "genlab_core.monitoring.health_monitor.write_alerts_to_db",
            return_value=1,
        ) as mock_write:
            _write_slo_violations_to_alerts_table(report)
        alerts = mock_write.call_args.args[0]
        assert alerts[0].niche_id == "sports"

    def test_message_preserved_verbatim(self):
        """The violation message is the operator-facing description.
        Must reach pipeline_alerts.message unchanged so triage is
        possible from the Mission Control card."""
        long_msg = "Zero blueprints produced from 1 stories (videos_validated=1)"
        report = self._base_report(slo_violations=[long_msg])
        with patch(
            "genlab_core.monitoring.health_monitor.write_alerts_to_db",
            return_value=1,
        ) as mock_write:
            _write_slo_violations_to_alerts_table(report)
        alerts = mock_write.call_args.args[0]
        assert alerts[0].message == long_msg
