"""Pin Phase 3.D session 2 experiment analyzer runner.

The decision matrix is the load-bearing logic — tests pin every
cell of it:

  * B_WINS + early_stop=true + duration NOT exceeded → FINALIZE completed
  * B_WINS + early_stop=false + duration NOT exceeded → HOLD (wait)
  * A_WINS + duration exceeded → FINALIZE completed
  * NO_SIGNAL + duration NOT exceeded → HOLD
  * NO_SIGNAL + duration exceeded → FINALIZE completed (null result)
  * INSUFFICIENT_SAMPLES + duration NOT exceeded → HOLD
  * INSUFFICIENT_SAMPLES + duration exceeded → FINALIZE discarded

The "futility-stop → discarded" split is critical per the roadmap
success criterion ("Zero ran-for-2-weeks-no-signal wasted
experiments"). Discarded ≠ Completed at the DB layer — strategist
needs to distinguish "we tested and it didn't work" from "we
tested and didn't get enough traffic to know".
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "run_experiment_analyzer",
    _ROOT / "scripts" / "run_experiment_analyzer.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["run_experiment_analyzer"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestEarlyStopFlag:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("GENLAB_EXPERIMENT_EARLY_STOP", raising=False)
        assert _MOD._early_stop_enabled(None) is True

    def test_off_via_env(self, monkeypatch):
        monkeypatch.setenv("GENLAB_EXPERIMENT_EARLY_STOP", "0")
        assert _MOD._early_stop_enabled(None) is False

    def test_cli_override_beats_env(self, monkeypatch):
        monkeypatch.setenv("GENLAB_EXPERIMENT_EARLY_STOP", "0")
        assert _MOD._early_stop_enabled(True) is True
        assert _MOD._early_stop_enabled(False) is False


class TestAnalyzeDecisionMatrix:
    """Feed compute_verdict a mock that returns each of the 4 enum
    values; verify _analyze_one takes the correct action per
    (verdict, duration_exceeded, early_stop) combination.

    Uses fake connection returning empty sample lists (fetch_arm_samples
    doesn't matter — we replace compute_verdict itself)."""

    def _make_conn(self):
        conn = MagicMock()
        # _fetch_arm_samples calls conn.execute().fetchall()
        conn.execute.return_value.fetchall.return_value = []
        return conn

    def _make_experiment(self, age_days: float, duration_days: int = 7):
        return {
            "id": "exp-abc-uuid",
            "niche_id": "gaming",
            "spec": {
                "arms": ["arm_A", "arm_B"],
                "duration_days": duration_days,
                "baseline_reward": 0.5,
            },
            "started_at": datetime.now(UTC) - timedelta(days=age_days),
            "age_seconds": int(age_days * 86400),
        }

    def _patch_verdict(self, verdict_enum_value):
        """Return a verdict object with the requested enum."""
        from genlab_core.scheduling.experiment_analysis import (
            ExperimentVerdict, VerdictResult,
        )
        verdict_map = {
            "B_WINS": ExperimentVerdict.B_WINS,
            "A_WINS": ExperimentVerdict.A_WINS,
            "NO_SIGNAL": ExperimentVerdict.NO_SIGNAL,
            "INSUFFICIENT_SAMPLES": ExperimentVerdict.INSUFFICIENT_SAMPLES,
        }
        return VerdictResult(
            verdict=verdict_map[verdict_enum_value],
            prob_b_beats_a=0.5,
            n_a=20, n_b=20,
            posterior_a_mean=0.5, posterior_b_mean=0.5,
            reason=f"test:{verdict_enum_value}",
        )

    # ── Winning cases ────────────────────────────────────────────

    @patch("genlab_core.scheduling.experiment_analysis.compute_verdict")
    def test_b_wins_early_stop_on_finalizes(self, mock_verdict):
        mock_verdict.return_value = self._patch_verdict("B_WINS")
        conn = self._make_conn()
        # age 3 days < duration 7 days = NOT exceeded
        action = _MOD._analyze_one(
            conn, self._make_experiment(age_days=3),
            early_stop=True, dry_run=True,
        )
        assert action.startswith("DRY:completed:B_WINS")

    @patch("genlab_core.scheduling.experiment_analysis.compute_verdict")
    def test_b_wins_early_stop_off_holds_until_duration(self, mock_verdict):
        mock_verdict.return_value = self._patch_verdict("B_WINS")
        conn = self._make_conn()
        action = _MOD._analyze_one(
            conn, self._make_experiment(age_days=3),
            early_stop=False, dry_run=True,
        )
        assert action == "HOLD"

    @patch("genlab_core.scheduling.experiment_analysis.compute_verdict")
    def test_a_wins_duration_exceeded_finalizes(self, mock_verdict):
        mock_verdict.return_value = self._patch_verdict("A_WINS")
        conn = self._make_conn()
        # age 10 > duration 7 = exceeded
        action = _MOD._analyze_one(
            conn, self._make_experiment(age_days=10),
            early_stop=False, dry_run=True,
        )
        assert action.startswith("DRY:completed:A_WINS")

    # ── NO_SIGNAL cases ──────────────────────────────────────────

    @patch("genlab_core.scheduling.experiment_analysis.compute_verdict")
    def test_no_signal_not_exceeded_holds(self, mock_verdict):
        mock_verdict.return_value = self._patch_verdict("NO_SIGNAL")
        conn = self._make_conn()
        action = _MOD._analyze_one(
            conn, self._make_experiment(age_days=3),
            early_stop=True, dry_run=True,
        )
        assert action == "HOLD"

    @patch("genlab_core.scheduling.experiment_analysis.compute_verdict")
    def test_no_signal_exceeded_finalizes_completed(self, mock_verdict):
        mock_verdict.return_value = self._patch_verdict("NO_SIGNAL")
        conn = self._make_conn()
        action = _MOD._analyze_one(
            conn, self._make_experiment(age_days=8),
            early_stop=True, dry_run=True,
        )
        # No signal + exceeded → completed (not discarded)
        assert action.startswith("DRY:completed:NO_SIGNAL")

    # ── INSUFFICIENT_SAMPLES → discarded discipline ──────────────

    @patch("genlab_core.scheduling.experiment_analysis.compute_verdict")
    def test_insufficient_not_exceeded_holds(self, mock_verdict):
        mock_verdict.return_value = self._patch_verdict("INSUFFICIENT_SAMPLES")
        conn = self._make_conn()
        action = _MOD._analyze_one(
            conn, self._make_experiment(age_days=2),
            early_stop=True, dry_run=True,
        )
        assert action == "HOLD"

    @patch("genlab_core.scheduling.experiment_analysis.compute_verdict")
    def test_insufficient_exceeded_marks_discarded(self, mock_verdict):
        """This is the roadmap-critical case — an experiment that
        never got enough traffic must mark as DISCARDED so the
        strategist can distinguish it from a completed null result."""
        mock_verdict.return_value = self._patch_verdict("INSUFFICIENT_SAMPLES")
        conn = self._make_conn()
        action = _MOD._analyze_one(
            conn, self._make_experiment(age_days=10),
            early_stop=True, dry_run=True,
        )
        assert action.startswith("DRY:discarded:INSUFFICIENT_SAMPLES")

    # ── Bad spec handling ────────────────────────────────────────

    def test_wrong_arm_count_skips(self):
        conn = self._make_conn()
        exp = self._make_experiment(age_days=5)
        exp["spec"]["arms"] = ["only_one_arm"]
        action = _MOD._analyze_one(conn, exp, early_stop=True, dry_run=True)
        assert action == "SKIP:bad_spec"

    def test_zero_arms_skips(self):
        conn = self._make_conn()
        exp = self._make_experiment(age_days=5)
        exp["spec"]["arms"] = []
        action = _MOD._analyze_one(conn, exp, early_stop=True, dry_run=True)
        assert action == "SKIP:bad_spec"


class TestMainExitCodes:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1
