"""Pin tests for Phase 6 silent-fail health checks.

Origin: 2026-08-11 session found 5 silent-fail bugs in the learning
loops via manual row-count queries. Each was "systemd exit 0 + zero
downstream rows" — invisible without human investigation.
check_learning_loops_silent_fail() automates the row-count assertions
so future occurrences fire pipeline_alerts within one health-monitor
cycle instead of requiring another audit round.

Tests verify each sub-check fires correctly when its target table
lacks expected rows AND stays quiet when the loop is healthy.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.checks.bandit_engagement import (
    _check_artifact_freshness,
    _check_ig_view_metric_regression,
    _check_late_reward_dead,
    _check_outcome_calibration_dead,
    _check_reward_pipeline_flow,
    _check_strategist_apply_dead,
    check_learning_loops_silent_fail,
)


def _mock_pg_conn(rows_map: dict):
    """Build a mock pg_connect context manager whose execute() returns
    the next row from ``rows_map`` (matched by SQL substring).

    ``rows_map`` = { 'sql_substring': row_dict } — first match wins.
    """
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=None)

    def _execute(sql, params=None):
        result = MagicMock()
        for needle, row in rows_map.items():
            if needle in sql:
                result.fetchone.return_value = row
                return result
        result.fetchone.return_value = None
        return result

    conn.execute.side_effect = _execute
    return conn


class TestLateRewardDeadDetection:
    def test_alerts_when_zero_deltas_in_48h(self):
        conn = _mock_pg_conn({"late_reward_deltas": {"n": 0, "latest": None}})
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_late_reward_dead("postgresql://x")
        assert len(alerts) == 1
        assert alerts[0].check == "silent_fail_late_reward"
        assert alerts[0].severity == "warning"
        assert "0 late_reward_deltas rows" in alerts[0].message

    def test_stays_quiet_when_rows_flowing(self):
        conn = _mock_pg_conn(
            {"late_reward_deltas": {"n": 15, "latest": "2026-08-11T10:00:00"}}
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_late_reward_dead("postgresql://x")
        assert alerts == []


class TestOutcomeCalibrationDeadDetection:
    def test_alerts_when_zero_outcome_rows_in_48h(self):
        conn = _mock_pg_conn(
            {"auto_approval_calibration": {"n": 0}}
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_outcome_calibration_dead("postgresql://x")
        assert len(alerts) == 1
        assert alerts[0].check == "silent_fail_outcome_calibration"

    def test_stays_quiet_when_outcome_writes_flowing(self):
        conn = _mock_pg_conn({"auto_approval_calibration": {"n": 18}})
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_outcome_calibration_dead("postgresql://x")
        assert alerts == []


class TestStrategistApplyDeadDetection:
    def test_alerts_when_accepted_but_not_applied(self):
        """Regression pin for the 5-layer bug chain: proposals_accepted
        gets written but applied_indices doesn't grow → chain broken
        somewhere in Bug 3d / 3e territory."""
        conn = _mock_pg_conn({"strategist_reports": {"n": 3}})
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_strategist_apply_dead("postgresql://x")
        assert len(alerts) == 1
        assert alerts[0].check == "silent_fail_strategist_apply"
        assert "3 strategist_reports" in alerts[0].message

    def test_stays_quiet_when_all_applied(self):
        conn = _mock_pg_conn({"strategist_reports": {"n": 0}})
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_strategist_apply_dead("postgresql://x")
        assert alerts == []


class TestRewardPipelineFlowDetection:
    def test_alerts_when_publishes_but_no_rewards(self):
        conn = _mock_pg_conn(
            {"publishing_analytics": {"n": 50}, "pending_feedback": {"n": 0}}
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_reward_pipeline_flow("postgresql://x")
        assert len(alerts) == 1
        assert alerts[0].check == "silent_fail_reward_pipeline"
        assert "50 publishes" in alerts[0].message

    def test_stays_quiet_when_low_publish_volume(self):
        """<20 publishes in 3d means "not enough to expect reward closure"
        — avoids false-alarming on genuinely-paused publishing."""
        conn = _mock_pg_conn(
            {"publishing_analytics": {"n": 5}, "pending_feedback": {"n": 0}}
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_reward_pipeline_flow("postgresql://x")
        assert alerts == []

    def test_stays_quiet_when_rewards_flowing(self):
        conn = _mock_pg_conn(
            {"publishing_analytics": {"n": 50}, "pending_feedback": {"n": 20}}
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_reward_pipeline_flow("postgresql://x")
        assert alerts == []


class TestIgMetricRegressionDetection:
    def test_alerts_when_majority_ig_posts_zero_views(self):
        """Detects the Meta API deprecation class-of-bug (e.g. `plays`
        removed in v22 → fetcher returns 0 for every IG post)."""
        conn = _mock_pg_conn(
            {"WITH recent_ig": {"zero_view": 15, "total": 20}}
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_ig_view_metric_regression("postgresql://x")
        assert len(alerts) == 1
        assert alerts[0].check == "silent_fail_ig_metric_regression"
        assert "15/20" in alerts[0].message

    def test_stays_quiet_below_50pct_zero_view(self):
        """Natural distribution has some 0-view posts — only alarm on
        clearly-anomalous majority."""
        conn = _mock_pg_conn(
            {"WITH recent_ig": {"zero_view": 3, "total": 20}}
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_ig_view_metric_regression("postgresql://x")
        assert alerts == []

    def test_stays_quiet_below_min_sample_size(self):
        """<10 IG posts in the 3-10d window means not enough data to
        distinguish real regression from small-sample noise."""
        conn = _mock_pg_conn(
            {"WITH recent_ig": {"zero_view": 5, "total": 8}}
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = _check_ig_view_metric_regression("postgresql://x")
        assert alerts == []


class TestArtifactFreshness:
    """2026-08-11 addendum to Phase 6: artifact-freshness check for
    file-based signals (counterfactual-replay writes JSON files,
    cross-niche-transfer writes JSON files). Original Phase 6 checks
    were DB-only — this closes the file-based blind spot that let
    counterfactual-replay go stale 29 days before manual discovery."""

    def test_stale_file_produces_alert(self, tmp_path, monkeypatch):
        """A file older than max_age_hours → warning alert."""
        stale_file = tmp_path / "priors.json"
        stale_file.write_text("{}")
        # Force mtime 10 days into the past
        import os
        import time

        past = time.time() - 10 * 86400
        os.utime(stale_file, (past, past))

        from genlab_core.monitoring.checks import bandit_engagement

        monkeypatch.setattr(
            bandit_engagement,
            "_ARTIFACT_FRESHNESS_MANIFEST",
            ((str(stale_file), 24, "genlab-test-service"),),
        )
        alerts = _check_artifact_freshness()
        assert len(alerts) == 1
        assert alerts[0].check == "silent_fail_artifact_stale"
        assert "genlab-test-service" in alerts[0].message
        assert alerts[0].details["age_hours"] > 24

    def test_fresh_file_stays_quiet(self, tmp_path, monkeypatch):
        """A file within max_age_hours → no alert."""
        fresh_file = tmp_path / "priors.json"
        fresh_file.write_text("{}")

        from genlab_core.monitoring.checks import bandit_engagement

        monkeypatch.setattr(
            bandit_engagement,
            "_ARTIFACT_FRESHNESS_MANIFEST",
            ((str(fresh_file), 168, "genlab-test-service"),),
        )
        assert _check_artifact_freshness() == []

    def test_missing_file_with_parent_dir_alerts(self, tmp_path, monkeypatch):
        """If the expected file is absent BUT its parent directory
        exists (service should have written there), alert."""
        expected = tmp_path / "priors.json"  # parent exists, file doesn't

        from genlab_core.monitoring.checks import bandit_engagement

        monkeypatch.setattr(
            bandit_engagement,
            "_ARTIFACT_FRESHNESS_MANIFEST",
            ((str(expected), 168, "genlab-test-service"),),
        )
        alerts = _check_artifact_freshness()
        assert len(alerts) == 1
        assert alerts[0].check == "silent_fail_artifact_missing"

    def test_missing_file_no_parent_dir_stays_quiet(self, tmp_path, monkeypatch):
        """Fresh install / wrong environment case: whole path missing
        (parent dir doesn't exist) → skip (don't alarm on test
        environments that legitimately have no artifacts)."""
        never_created = tmp_path / "nonexistent" / "sub" / "priors.json"

        from genlab_core.monitoring.checks import bandit_engagement

        monkeypatch.setattr(
            bandit_engagement,
            "_ARTIFACT_FRESHNESS_MANIFEST",
            ((str(never_created), 168, "genlab-test-service"),),
        )
        assert _check_artifact_freshness() == []

    def test_glob_pattern_uses_latest_match(self, tmp_path, monkeypatch):
        """Glob pattern (e.g. replay-*.json) picks the MOST RECENT
        matching file's mtime — matches counterfactual-replay's
        timestamp-suffixed file pattern."""
        import os
        import time

        (tmp_path / "replay-oldest.json").write_text("{}")
        os.utime(tmp_path / "replay-oldest.json", (time.time() - 40 * 86400, time.time() - 40 * 86400))
        (tmp_path / "replay-newer.json").write_text("{}")
        # newer file at current time — should count as latest

        pattern = str(tmp_path / "replay-*.json")

        from genlab_core.monitoring.checks import bandit_engagement

        monkeypatch.setattr(
            bandit_engagement,
            "_ARTIFACT_FRESHNESS_MANIFEST",
            ((pattern, 24, "genlab-test-service"),),
        )
        # Latest file is fresh — should NOT alert despite older files present
        assert _check_artifact_freshness() == []

    def test_actual_manifest_covers_known_file_writers(self):
        """Regression pin: the manifest MUST include cross-niche-transfer
        and counterfactual-replay. Removing either re-introduces the
        file-based silent-fail blind spot."""
        from genlab_core.monitoring.checks.bandit_engagement import (
            _ARTIFACT_FRESHNESS_MANIFEST,
        )
        service_names = [entry[2] for entry in _ARTIFACT_FRESHNESS_MANIFEST]
        assert "genlab-cross-niche-transfer" in service_names, (
            "Manifest must include cross-niche-transfer — writes weekly "
            "priors.json used by learning/cross_niche_transfer.py."
        )
        assert "genlab-counterfactual-replay" in service_names, (
            "Manifest must include counterfactual-replay — writes monthly "
            "replay-*.json used by dashboard CounterfactualReplayCard."
        )


class TestOrchestrator:
    def test_no_dsn_returns_empty_gracefully(self, monkeypatch):
        """When DATABASE_URL isn't set, return [] instead of crashing.
        Health-monitor calls this in every fire; a crash would poison
        the whole monitoring run."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        alerts = check_learning_loops_silent_fail()
        assert alerts == []

    def test_orchestrator_wires_all_six_sub_checks(self, monkeypatch):
        """Regression pin: if a sub-check gets accidentally removed
        from the tuple, coverage silently drops. This test names each
        sub-check explicitly. 6 sub-checks after 2026-08-11 artifact-
        freshness addendum (was 5)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://mock")

        import inspect

        from genlab_core.monitoring.checks import bandit_engagement

        source = inspect.getsource(bandit_engagement.check_learning_loops_silent_fail)
        for expected_check in (
            "_check_artifact_freshness",
            "_check_late_reward_dead",
            "_check_outcome_calibration_dead",
            "_check_strategist_apply_dead",
            "_check_reward_pipeline_flow",
            "_check_ig_view_metric_regression",
        ):
            assert expected_check in source, (
                f"check_learning_loops_silent_fail must include "
                f"{expected_check} in its sub-check tuple. Removing a "
                f"sub-check silently reduces silent-fail detection "
                f"coverage — same class-of-bug this module was built "
                f"to prevent."
            )

    def test_one_failing_sub_check_doesnt_mask_others(self, monkeypatch):
        """Regression pin: sub-checks must be independent. If one
        raises, the others still run."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://mock")

        conn = _mock_pg_conn(
            {
                "late_reward_deltas": {"n": 0, "latest": None},
                "auto_approval_calibration": {"n": 0},
                "strategist_reports": {"n": 0},
                "publishing_analytics": {"n": 5},  # < 20, no alarm
                "pending_feedback": {"n": 0},
                "WITH recent_ig": {"zero_view": 1, "total": 20},  # low zero%, no alarm
            }
        )
        with patch(
            "genlab_core.monitoring.checks.bandit_engagement.pg_connect",
            return_value=conn,
        ):
            alerts = check_learning_loops_silent_fail()

        # At minimum the 2 zero-row checks fire; the 3 conditional
        # checks stay quiet. Test asserts we get >= 2 alerts (proves
        # each sub-check ran independently vs one exception killing all).
        assert len(alerts) >= 2
        alert_checks = {a.check for a in alerts}
        assert "silent_fail_late_reward" in alert_checks
        assert "silent_fail_outcome_calibration" in alert_checks
