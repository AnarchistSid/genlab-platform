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


class TestMeasureExperimentResult:
    """Pin the shape of measure_experiment_result. It's the load-bearing
    metric fn — if the shape drifts, complete_experiment persists
    something the dashboard can't render."""

    def _fake_row(self, avg_r, n):
        # Simulate psycopg dict_row shape (has .get).
        return {"avg_r": avg_r, "n": n}

    def _make_conn(self, per_arm_rows):
        """Return a conn whose execute().fetchone() cycles through
        per-arm rows in the order arms are iterated."""
        mock_conn = MagicMock()
        it = iter(per_arm_rows)

        def _execute(*_a, **_kw):
            r = next(it)
            m = MagicMock()
            m.fetchone.return_value = r
            return m

        mock_conn.execute.side_effect = _execute
        return mock_conn

    def test_met_threshold_true_when_lift_exceeds_shift_with_samples(self):
        from genlab_core.scheduling.auto_experiment import measure_experiment_result

        conn = self._make_conn(
            [
                self._fake_row(0.10, 8),  # control
                self._fake_row(0.30, 8),  # treatment
            ]
        )
        exp = {
            "id": "exp-1",
            "niche_id": "gaming",
            "started_at": "2026-07-16T00:00:00+00:00",
            "spec": {
                "arms": ["hook_style_a", "hook_style_b"],
                "expected_metric_shift": 0.15,
                "niche_id": "gaming",
            },
        }
        result = measure_experiment_result(conn, exp)
        assert result["met_threshold"] is True
        assert result["sufficient_samples"] is True
        assert result["observed_lift"] == pytest.approx(0.20, abs=1e-6)
        assert result["arm_rewards"]["hook_style_a"]["n_samples"] == 8
        assert result["arm_rewards"]["hook_style_b"]["n_samples"] == 8

    def test_met_threshold_false_when_samples_insufficient(self):
        from genlab_core.scheduling.auto_experiment import (
            MIN_SAMPLES_PER_ARM,
            measure_experiment_result,
        )

        conn = self._make_conn(
            [
                self._fake_row(0.10, 3),  # below MIN
                self._fake_row(0.90, 3),  # huge lift but sample-starved
            ]
        )
        exp = {
            "id": "exp-2",
            "niche_id": "sports",
            "started_at": "2026-07-16T00:00:00+00:00",
            "spec": {
                "arms": ["ctrl", "trt"],
                "expected_metric_shift": 0.05,
            },
        }
        result = measure_experiment_result(conn, exp)
        assert result["sufficient_samples"] is False
        assert result["met_threshold"] is False
        assert result["min_samples_required"] == MIN_SAMPLES_PER_ARM

    def test_met_threshold_false_when_lift_under_expected(self):
        from genlab_core.scheduling.auto_experiment import measure_experiment_result

        conn = self._make_conn(
            [
                self._fake_row(0.20, 10),
                self._fake_row(0.22, 10),  # only 0.02 lift vs 0.10 expected
            ]
        )
        exp = {
            "id": "exp-3",
            "niche_id": "movies",
            "started_at": "2026-07-16T00:00:00+00:00",
            "spec": {
                "arms": ["a", "b"],
                "expected_metric_shift": 0.10,
            },
        }
        result = measure_experiment_result(conn, exp)
        assert result["sufficient_samples"] is True
        assert result["met_threshold"] is False
        assert result["observed_lift"] == pytest.approx(0.02, abs=1e-6)

    def test_fail_open_on_db_error(self):
        from genlab_core.scheduling.auto_experiment import measure_experiment_result

        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("db down")
        exp = {
            "id": "exp-4",
            "niche_id": "anime",
            "started_at": "2026-07-16T00:00:00+00:00",
            "spec": {"arms": ["x", "y"], "expected_metric_shift": 0.1},
        }
        result = measure_experiment_result(conn, exp)
        # Fail-open: still returns a shape, met_threshold False.
        assert result["met_threshold"] is False
        assert result["arm_rewards"]["x"]["observed_reward"] is None
        assert result["arm_rewards"]["y"]["observed_reward"] is None

    def test_iso_window_shape(self):
        from genlab_core.scheduling.auto_experiment import measure_experiment_result

        conn = self._make_conn(
            [self._fake_row(0.1, 5), self._fake_row(0.2, 5)]
        )
        exp = {
            "id": "exp-5",
            "niche_id": "ai_creators",
            "started_at": "2026-07-16T00:00:00+00:00",
            "spec": {"arms": ["a", "b"], "expected_metric_shift": 0.05},
        }
        result = measure_experiment_result(conn, exp)
        assert result["window_start"] == "2026-07-16T00:00:00+00:00"
        # window_end is NOW() ISO — must be a non-empty ISO string.
        assert "T" in result["window_end"]
        assert len(result["window_end"]) >= 19

    def test_min_samples_constant_is_five(self):
        """Regression pin: bumping this without operator review would
        make the ratchet stall silently. Any change requires updating
        the docstring rationale AND this test."""
        from genlab_core.scheduling.auto_experiment import MIN_SAMPLES_PER_ARM

        assert MIN_SAMPLES_PER_ARM == 5


class TestPromoteVerdictToProposal:
    """Closes the loop verdict -> proposal auto-accept. When lifecycle
    completes an experiment with met_threshold=True + sufficient_samples
    =True, the matching strategist proposal is auto-accepted so the
    winning arm gets activated in bandit_arms on the next
    apply_strategist_actions fire."""

    def _make_conn(self, report_row=None, arm_rows=None, raise_on=None):
        """Build a conn that returns the given report_row for
        strategist_reports lookup and arm_rows for bandit_arms lookup."""
        mock_conn = MagicMock()
        arm_rows = arm_rows or []
        calls = []

        def _execute(sql, *_a, **_kw):
            calls.append(sql)
            m = MagicMock()
            if raise_on and raise_on in sql:
                raise RuntimeError("db down")
            if "FROM strategist_reports" in sql:
                m.fetchone.return_value = report_row
            elif "FROM bandit_arms" in sql:
                m.fetchall.return_value = arm_rows
            elif "UPDATE strategist_reports" in sql:
                m.rowcount = 1
            return m

        mock_conn.execute.side_effect = _execute
        mock_conn._calls = calls
        return mock_conn

    def test_skips_when_met_threshold_false(self):
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        conn = self._make_conn()
        arm_id, reason = promote_verdict_to_proposal(
            conn,
            {
                "id": "e1",
                "spec": {"arms": ["a", "b"]},
                "result": {"met_threshold": False, "sufficient_samples": True},
                "source_report_id": "r1",
                "niche_id": "gaming",
            },
        )
        assert arm_id is None
        assert reason == "skip:verdict_not_met_or_low_n"

    def test_skips_when_low_samples(self):
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        conn = self._make_conn()
        arm_id, _ = promote_verdict_to_proposal(
            conn,
            {
                "id": "e1",
                "spec": {"arms": ["a", "b"]},
                "result": {"met_threshold": True, "sufficient_samples": False},
                "source_report_id": "r1",
                "niche_id": "gaming",
            },
        )
        assert arm_id is None

    def test_skips_when_no_source_report(self):
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        conn = self._make_conn()
        arm_id, reason = promote_verdict_to_proposal(
            conn,
            {
                "id": "e1",
                "spec": {"arms": ["a", "b"]},
                "result": {"met_threshold": True, "sufficient_samples": True},
                "source_report_id": None,
                "niche_id": "gaming",
            },
        )
        assert arm_id is None
        assert reason == "skip:no_source_report"

    def test_skips_when_no_matching_proposal(self):
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        # Report has proposals but none match the winning arm.
        report_row = {
            "proposals": [
                {"type": "arm_add", "proposed": {"arm_id": "style:gaming:other"}},
            ],
            "accepted": [],
        }
        conn = self._make_conn(
            report_row=report_row,
            arm_rows=[{"arm_id": "style:gaming:existing"}],
        )
        arm_id, reason = promote_verdict_to_proposal(
            conn,
            {
                "id": "e1",
                "spec": {"arms": ["ctrl", "style:gaming:winner"]},
                "result": {"met_threshold": True, "sufficient_samples": True},
                "source_report_id": "r1",
                "niche_id": "gaming",
            },
        )
        assert arm_id is None
        assert "no_matching_proposal" in reason

    def test_skips_when_already_accepted(self):
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        report_row = {
            "proposals": [
                {"type": "arm_add", "proposed": {"arm_id": "style:gaming:winner"}},
            ],
            "accepted": [0],  # already at index 0
        }
        conn = self._make_conn(
            report_row=report_row,
            arm_rows=[{"arm_id": "style:gaming:existing"}],
        )
        arm_id, reason = promote_verdict_to_proposal(
            conn,
            {
                "id": "e1",
                "spec": {"arms": ["ctrl", "style:gaming:winner"]},
                "result": {"met_threshold": True, "sufficient_samples": True},
                "source_report_id": "r1",
                "niche_id": "gaming",
            },
        )
        assert arm_id is None
        assert reason == "skip:already_accepted"

    def test_promotes_style_variant_when_existing_style_present(self):
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        report_row = {
            "proposals": [
                {"type": "arm_add", "proposed": {"arm_id": "style:gaming:winner"}},
            ],
            "accepted": [],
        }
        conn = self._make_conn(
            report_row=report_row,
            arm_rows=[{"arm_id": "style:gaming:existing"}],
        )
        arm_id, reason = promote_verdict_to_proposal(
            conn,
            {
                "id": "e1",
                "spec": {"arms": ["ctrl", "style:gaming:winner"]},
                "result": {"met_threshold": True, "sufficient_samples": True},
                "source_report_id": "r1",
                "niche_id": "gaming",
            },
        )
        assert arm_id == "style:gaming:winner"
        assert reason.startswith("auto_accept:style_variant")

    def test_declines_new_dimension_even_when_verdict_met(self):
        """Verdict-confirmed does NOT bypass shape guards. A
        first-of-dimension arm still needs operator review."""
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        report_row = {
            "proposals": [
                {"type": "arm_add", "proposed": {"arm_id": "source:gaming:new_feed"}},
            ],
            "accepted": [],
        }
        conn = self._make_conn(
            report_row=report_row,
            arm_rows=[{"arm_id": "style:gaming:existing"}],
        )
        arm_id, reason = promote_verdict_to_proposal(
            conn,
            {
                "id": "e1",
                "spec": {"arms": ["ctrl", "source:gaming:new_feed"]},
                "result": {"met_threshold": True, "sufficient_samples": True},
                "source_report_id": "r1",
                "niche_id": "gaming",
            },
        )
        # new_source shape always operator-gates.
        assert arm_id is None
        assert "classifier_declined" in reason

    def test_fail_open_on_report_load_error(self):
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        conn = self._make_conn(raise_on="FROM strategist_reports")
        arm_id, reason = promote_verdict_to_proposal(
            conn,
            {
                "id": "e1",
                "spec": {"arms": ["a", "b"]},
                "result": {"met_threshold": True, "sufficient_samples": True},
                "source_report_id": "r1",
                "niche_id": "gaming",
            },
        )
        assert arm_id is None
        assert reason == "skip:report_load_error"

    def test_writes_verdict_promoted_experiment_ids_marker(self):
        """The UPDATE must record the experiment_id in
        extra.verdict_promoted_experiment_ids so operator can
        distinguish auto-accepted-by-strategist-classifier from
        auto-accepted-by-experiment-verdict later."""
        from genlab_core.scheduling.auto_experiment import promote_verdict_to_proposal

        report_row = {
            "proposals": [
                {"type": "arm_add", "proposed": {"arm_id": "style:gaming:winner"}},
            ],
            "accepted": [],
        }
        conn = self._make_conn(
            report_row=report_row,
            arm_rows=[{"arm_id": "style:gaming:existing"}],
        )
        arm_id, _ = promote_verdict_to_proposal(
            conn,
            {
                "id": "exp-abc123",
                "spec": {"arms": ["ctrl", "style:gaming:winner"]},
                "result": {"met_threshold": True, "sufficient_samples": True},
                "source_report_id": "r1",
                "niche_id": "gaming",
            },
        )
        assert arm_id == "style:gaming:winner"
        # Verify the UPDATE SQL carries the experiment_ids marker key.
        update_calls = [c for c in conn._calls if "UPDATE strategist_reports" in c]
        assert len(update_calls) == 1
        assert "verdict_promoted_experiment_ids" in update_calls[0]


class TestLifecycleCLIStructure:
    """Structural pins for scripts/run_experiment_lifecycle.py — catches
    rename drift on the imports the timer relies on."""

    def test_cli_imports_the_right_symbols(self):
        import pathlib

        p = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "run_experiment_lifecycle.py"
        assert p.exists(), f"CLI runner missing at {p}"
        text = p.read_text()
        # These symbols MUST be imported — the timer depends on them.
        for sym in (
            "start_pending_experiments",
            "check_running_experiments",
            "measure_experiment_result",
            "complete_experiment",
            "is_enabled",
        ):
            assert sym in text, f"CLI must import {sym}"

    def test_cli_gates_on_is_enabled(self):
        """Strict-true flag gate — if the flag isn't set, the CLI must
        exit cleanly. Otherwise the systemd timer will silently mutate
        DB before the operator flips the flag."""
        import pathlib

        p = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "run_experiment_lifecycle.py"
        text = p.read_text()
        assert "if not is_enabled():" in text

    def test_cli_dry_run_default(self):
        """--apply must be opt-in. Reverses of this pattern (--dry-run
        opt-in) are how test invocations accidentally mutate prod."""
        import pathlib

        p = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "run_experiment_lifecycle.py"
        text = p.read_text()
        assert '"--apply"' in text
        assert 'action="store_true"' in text


class TestLifecycleSystemdUnit:
    """Structural pins for the systemd unit — rule #26 (exit 0 unless
    genuine incident) requires the script to guarantee 0 on nothing-
    to-do. The service body must not accidentally set ExecStop or
    Restart= that would create false-alarm on empty runs."""

    def _read(self, filename):
        import pathlib

        p = (
            pathlib.Path(__file__).resolve().parents[3]
            / "deploy"
            / "systemd-phase2"
            / filename
        )
        assert p.exists(), f"unit missing at {p}"
        return p.read_text()

    def test_service_is_oneshot(self):
        assert "Type=oneshot" in self._read(
            "genlab-experiment-lifecycle.service"
        )

    def test_service_has_onfailure_alert(self):
        assert "OnFailure=genlab-service-failure-alert@%n.service" in self._read(
            "genlab-experiment-lifecycle.service"
        )

    def test_timer_is_persistent(self):
        # Rule #21 — never leave a rare-fire timer at Persistent=false.
        # Every-6h is often enough that Persistent=true is right too.
        assert "Persistent=true" in self._read(
            "genlab-experiment-lifecycle.timer"
        )

    def test_timer_calendar_shape(self):
        # 4x/day at :20 past the hour, UTC — pin exact cadence.
        assert "*-*-* 00,06,12,18:20:00 UTC" in self._read(
            "genlab-experiment-lifecycle.timer"
        )
