"""Tests for check_bandit_regret_signal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_artifact(dir_: Path, filename: str, per_arm: list[dict]) -> Path:
    """Write a synthetic replay-*-all.json for testing."""
    path = dir_ / filename
    payload = {
        "generated_at": "2026-07-23T00:00:00+00:00",
        "niche": "all",
        "window_days": 30,
        "n_decisions_total": sum(a.get("n_decisions", 0) for a in per_arm),
        "dr_enabled": True,
        "per_arm": per_arm,
    }
    path.write_text(json.dumps(payload))
    return path


class TestArtifactDiscovery:
    def test_no_dir_returns_no_alerts(self, tmp_path, monkeypatch):
        from genlab_core.monitoring.checks import bandit_regret

        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        # No counterfactual-replay dir exists
        assert bandit_regret.check_bandit_regret_signal() == []

    def test_single_artifact_no_alert(self, tmp_path, monkeypatch):
        """Need at least 2 artifacts to detect a chronic pattern."""
        from genlab_core.monitoring.checks import bandit_regret

        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "counterfactual-replay"
        dir_.mkdir()
        _write_artifact(
            dir_,
            "replay-20260723-all.json",
            [
                {"arm_id": "a", "n_decisions": 50, "relative_lift": -0.3},
                {"arm_id": "b", "n_decisions": 50, "relative_lift": -0.4},
                {"arm_id": "c", "n_decisions": 50, "relative_lift": -0.2},
            ],
        )
        assert bandit_regret.check_bandit_regret_signal() == []


class TestFiringThreshold:
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "counterfactual-replay"
        dir_.mkdir()
        return dir_

    def test_two_artifacts_with_3_negative_fires_warning(self, tmp_path, monkeypatch):
        from genlab_core.monitoring.checks import bandit_regret

        dir_ = self._setup(tmp_path, monkeypatch)

        # Two artifacts, both with ≥3 arms showing negative lift.
        per_arm = [
            {"arm_id": "a", "n_decisions": 50, "relative_lift": -0.3},
            {"arm_id": "b", "n_decisions": 50, "relative_lift": -0.4},
            {"arm_id": "c", "n_decisions": 50, "relative_lift": -0.2},
            {"arm_id": "d", "n_decisions": 50, "relative_lift": 0.1},  # positive
        ]
        _write_artifact(dir_, "replay-20260720-all.json", per_arm)
        # Bump mtime so this is "older" but still within the 45-day
        # window. Using 2 days ago.
        import os as _os
        import time as _time
        old_path = dir_ / "replay-20260720-all.json"
        two_days_ago = _time.time() - (2 * 86400)
        _os.utime(old_path, (two_days_ago, two_days_ago))
        _write_artifact(dir_, "replay-20260723-all.json", per_arm)

        alerts = bandit_regret.check_bandit_regret_signal()
        assert len(alerts) == 1
        assert alerts[0].check == "bandit_regret_signal"
        assert alerts[0].severity == "warning"
        assert alerts[0].details["latest_negative_count"] == 3
        assert alerts[0].details["previous_negative_count"] == 3

    def test_below_threshold_no_alert(self, tmp_path, monkeypatch):
        """Only 2 arms negative — under the 3-arm threshold. No alert."""
        from genlab_core.monitoring.checks import bandit_regret

        dir_ = self._setup(tmp_path, monkeypatch)

        per_arm = [
            {"arm_id": "a", "n_decisions": 50, "relative_lift": -0.3},
            {"arm_id": "b", "n_decisions": 50, "relative_lift": -0.4},
            {"arm_id": "c", "n_decisions": 50, "relative_lift": 0.1},
        ]
        _write_artifact(dir_, "replay-20260720-all.json", per_arm)
        _write_artifact(dir_, "replay-20260723-all.json", per_arm)

        assert bandit_regret.check_bandit_regret_signal() == []

    def test_thin_n_decisions_ignored(self, tmp_path, monkeypatch):
        """Arms with n_decisions < 20 have noisy lift — ignored."""
        from genlab_core.monitoring.checks import bandit_regret

        dir_ = self._setup(tmp_path, monkeypatch)

        per_arm = [
            # All under n=20 → all ignored
            {"arm_id": "a", "n_decisions": 5, "relative_lift": -0.3},
            {"arm_id": "b", "n_decisions": 10, "relative_lift": -0.4},
            {"arm_id": "c", "n_decisions": 15, "relative_lift": -0.2},
        ]
        _write_artifact(dir_, "replay-20260720-all.json", per_arm)
        _write_artifact(dir_, "replay-20260723-all.json", per_arm)

        assert bandit_regret.check_bandit_regret_signal() == []

    def test_null_lift_ignored(self, tmp_path, monkeypatch):
        """Arms with lift=null (insufficient data) don't contribute."""
        from genlab_core.monitoring.checks import bandit_regret

        dir_ = self._setup(tmp_path, monkeypatch)

        per_arm = [
            {"arm_id": "a", "n_decisions": 50, "relative_lift": None},
            {"arm_id": "b", "n_decisions": 50, "relative_lift": None},
            {"arm_id": "c", "n_decisions": 50, "relative_lift": None},
        ]
        _write_artifact(dir_, "replay-20260720-all.json", per_arm)
        _write_artifact(dir_, "replay-20260723-all.json", per_arm)

        assert bandit_regret.check_bandit_regret_signal() == []


class TestHealthMonitorWiring:
    def test_check_bandit_regret_signal_wired_into_health_monitor(self):
        import inspect

        from genlab_core.monitoring import health_monitor

        src = inspect.getsource(health_monitor)
        assert "check_bandit_regret_signal" in src, (
            "health_monitor must import + invoke check_bandit_regret_signal "
            "— otherwise the exploration meta-param signal never fires."
        )
