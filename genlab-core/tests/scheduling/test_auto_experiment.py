"""Tests for auto_experiment scaffold (#9 autonomy roadmap)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestFlagGate:
    def test_off_by_default(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment import is_enabled

        monkeypatch.delenv("GENLAB_AUTO_EXPERIMENT_ENABLED", raising=False)
        assert is_enabled() is False

    def test_strict_true_enables(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment import is_enabled

        monkeypatch.setenv("GENLAB_AUTO_EXPERIMENT_ENABLED", "true")
        assert is_enabled() is True


class TestExperimentSpecShape:
    def test_default_duration_is_7_days(self):
        from genlab_core.scheduling.auto_experiment import (
            DEFAULT_DURATION_DAYS,
            ExperimentSpec,
        )

        spec = ExperimentSpec()
        assert spec.duration_days == DEFAULT_DURATION_DAYS
        assert DEFAULT_DURATION_DAYS == 7

    def test_to_json_serializes_all_fields(self):
        from genlab_core.scheduling.auto_experiment import ExperimentSpec

        spec = ExperimentSpec(
            arms=["a", "b"],
            niche_id="gaming",
            expected_metric_shift=0.15,
            duration_days=14,
            notes="test",
        )
        import json as _json

        payload = _json.loads(spec.to_json())
        assert payload["arms"] == ["a", "b"]
        assert payload["niche_id"] == "gaming"
        assert payload["expected_metric_shift"] == 0.15
        assert payload["duration_days"] == 14
        assert payload["notes"] == "test"


class TestQueuePending:
    def test_queue_returns_row_id(self):
        from genlab_core.scheduling.auto_experiment import (
            ExperimentSpec,
            queue_pending_experiment,
        )

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("new-uuid",)

        result = queue_pending_experiment(
            mock_conn,
            source_report_id="report-1",
            hypothesis_index=2,
            niche_id="gaming",
            spec=ExperimentSpec(arms=["a", "b"], niche_id="gaming"),
        )
        assert result == "new-uuid"

    def test_conflict_returns_none(self):
        """When the ON CONFLICT (source_report_id, hypothesis_index)
        skip fires, fetchone returns None."""
        from genlab_core.scheduling.auto_experiment import (
            ExperimentSpec,
            queue_pending_experiment,
        )

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None

        result = queue_pending_experiment(
            mock_conn,
            source_report_id="report-1",
            hypothesis_index=2,
            niche_id="gaming",
            spec=ExperimentSpec(),
        )
        assert result is None

    def test_db_error_returns_none(self):
        from genlab_core.scheduling.auto_experiment import (
            ExperimentSpec,
            queue_pending_experiment,
        )

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("db down")

        result = queue_pending_experiment(
            mock_conn,
            source_report_id="report-1",
            hypothesis_index=2,
            niche_id="gaming",
            spec=ExperimentSpec(),
        )
        assert result is None


class TestCompleteExperiment:
    def test_complete_writes_result_json(self):
        from genlab_core.scheduling.auto_experiment import complete_experiment

        mock_conn = MagicMock()
        result_payload = {
            "observed_reward_arm_a": 0.15,
            "observed_reward_arm_b": 0.22,
            "met_threshold": True,
        }
        assert complete_experiment(mock_conn, "exp-1", result_payload) is True
        mock_conn.execute.assert_called_once()
        # Verify the JSON payload passed as first arg
        args = mock_conn.execute.call_args
        assert "'completed'" in args[0][0]
        assert "exp-1" in args[0][1]

    def test_fail_open_on_error(self):
        from genlab_core.scheduling.auto_experiment import complete_experiment

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("bad")
        assert complete_experiment(mock_conn, "exp-1", {"met": True}) is False


class TestStartPending:
    def test_start_pending_returns_count(self):
        from genlab_core.scheduling.auto_experiment import start_pending_experiments

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("id-1",), ("id-2",)]
        assert start_pending_experiments(mock_conn) == 2

    def test_no_pending_returns_zero(self):
        from genlab_core.scheduling.auto_experiment import start_pending_experiments

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        assert start_pending_experiments(mock_conn) == 0
