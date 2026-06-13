"""Pins for genlab_core.scheduling.calibration_logger.

AUTO #1b (2026-06-13). The logger captures (gate verdict, operator
action) pairs for the confusion matrix that unblocks AUTO #2.

Key invariants:
- log() NEVER raises, even on DB error / missing DATABASE_URL
- Invalid operator_action is rejected (skipped, not silently logged)
- gate decision=None is preserved (NULL gate_approved in DB)
- stats() returns zeros on cold start, doesn't crash
- ready_for_enforcement requires both ≥30 samples AND ≥90% agreement
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.scheduling.auto_approval_gate import AutoApprovalDecision
from genlab_core.scheduling.calibration_logger import (
    VALID_OPERATOR_ACTIONS,
    CalibrationStats,
    log,
    stats,
)


@pytest.fixture
def clean_env(monkeypatch):
    """Ensure tests don't accidentally hit a real DB."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield


def _decision(approved=True, confidence=0.78):
    return AutoApprovalDecision(
        approved=approved,
        confidence=confidence,
        passed_checks=["has_video", "has_hook"],
        failed_checks=[] if approved else ["composite_score"],
        reasons=["Video present", "Hook present"],
    )


class TestLogNoDb:
    """When DATABASE_URL isn't set, log() is a no-op returning False."""

    def test_returns_false_silently(self, clean_env):
        assert (
            log(
                blueprint_id="bp1",
                niche_id="gaming",
                decision=_decision(),
                operator_action="approved",
            )
            is False
        )


class TestLogValidation:
    """Bad input rejected before any DB attempt."""

    def test_empty_blueprint_id(self, clean_env):
        assert (
            log(
                blueprint_id="",
                niche_id="gaming",
                decision=_decision(),
                operator_action="approved",
            )
            is False
        )

    def test_empty_niche_id(self, clean_env):
        assert (
            log(
                blueprint_id="bp1",
                niche_id="",
                decision=_decision(),
                operator_action="approved",
            )
            is False
        )

    def test_invalid_operator_action(self, clean_env, monkeypatch):
        # Even with DB set, invalid action is rejected.
        monkeypatch.setenv("DATABASE_URL", "postgresql://x")
        assert (
            log(
                blueprint_id="bp1",
                niche_id="gaming",
                decision=_decision(),
                operator_action="published",  # not in VALID_OPERATOR_ACTIONS
            )
            is False
        )

    def test_valid_actions_are_canonical_past_tense(self):
        # Pin the contract — these must match review_server's
        # _execute_review_action accepted values.
        assert VALID_OPERATOR_ACTIONS == {"approved", "rejected", "revised", "skipped"}


class TestLogDbErrorFailsOpen:
    """A DB failure during write must NOT raise to the caller —
    operator review must never be blocked by calibration."""

    def test_psycopg_connect_failure_returns_false(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://invalid-host:5432/nope")

        # Mock psycopg.connect to raise
        mock_psycopg = MagicMock()
        mock_psycopg.connect.side_effect = Exception("connection refused")
        with patch.dict("sys.modules", {"psycopg": mock_psycopg}):
            # Must return False, NOT raise
            result = log(
                blueprint_id="bp1",
                niche_id="gaming",
                decision=_decision(),
                operator_action="approved",
            )
            assert result is False

    def test_successful_write_returns_true(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        mock_psycopg = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg.connect.return_value.__enter__.return_value = mock_conn
        with patch.dict("sys.modules", {"psycopg": mock_psycopg}):
            result = log(
                blueprint_id="bp1",
                niche_id="gaming",
                decision=_decision(),
                operator_action="approved",
            )
            assert result is True
            # Verify the INSERT was executed with the right values
            mock_cur.execute.assert_called_once()
            args = mock_cur.execute.call_args[0]
            assert "INSERT INTO auto_approval_calibration" in args[0]
            # parameters tuple: (bp, niche, gate_approved, conf, passed, failed, action)
            assert args[1][0] == "bp1"
            assert args[1][1] == "gaming"
            assert args[1][2] is True  # gate_approved
            assert args[1][6] == "approved"


class TestLogNoneDecision:
    """When gate evaluation errored, decision=None — still log the
    operator's action, just with NULL gate fields (for debugging)."""

    def test_none_decision_writes_nulls(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        mock_psycopg = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg.connect.return_value.__enter__.return_value = mock_conn
        with patch.dict("sys.modules", {"psycopg": mock_psycopg}):
            result = log(
                blueprint_id="bp1",
                niche_id="gaming",
                decision=None,
                operator_action="rejected",
            )
            assert result is True
            args = mock_cur.execute.call_args[0]
            # gate_approved and gate_confidence should be None
            assert args[1][2] is None
            assert args[1][3] is None


class TestStats:
    def test_empty_niche_returns_zeros(self, clean_env):
        s = stats(niche_id="")
        assert s.sample_count == 0
        assert s.agreement_count == 0
        assert s.ready_for_enforcement is False

    def test_no_db_returns_zeros(self, clean_env):
        s = stats(niche_id="gaming")
        assert s.sample_count == 0
        assert s.ready_for_enforcement is False

    def test_query_success_populates_confusion_matrix(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        # 35 total, 32 in agreement = ~91% — past the gate
        mock_psycopg = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (35, 25, 7, 2, 1)  # sample, tp, tn, fp, fn
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_psycopg.connect.return_value.__enter__.return_value = mock_conn
        with patch.dict("sys.modules", {"psycopg": mock_psycopg}):
            s = stats(niche_id="gaming", window_days=7)
            assert s.sample_count == 35
            assert s.true_positives == 25
            assert s.true_negatives == 7
            assert s.false_positives == 2
            assert s.false_negatives == 1
            assert s.agreement_count == 32  # tp + tn
            assert abs(s.agreement_rate - 0.914) < 0.01
            assert s.ready_for_enforcement is True  # ≥30 samples + ≥90%


class TestReadyForEnforcement:
    """Pin the heuristic — both gates must clear."""

    def _make(self, samples, agreement):
        return CalibrationStats(
            niche_id="gaming",
            sample_count=samples,
            agreement_count=agreement,
            true_positives=agreement,
            true_negatives=0,
            false_positives=samples - agreement,
            false_negatives=0,
        )

    def test_high_agreement_low_samples_not_ready(self):
        # 100% agreement but only 10 samples
        assert self._make(10, 10).ready_for_enforcement is False

    def test_many_samples_low_agreement_not_ready(self):
        # 80 samples but only 70% agreement
        assert self._make(80, 56).ready_for_enforcement is False

    def test_at_thresholds_ready(self):
        # Exactly 30 samples + 90% — the boundary
        assert self._make(30, 27).ready_for_enforcement is True

    def test_zero_samples_zero_rate(self):
        s = self._make(0, 0)
        assert s.agreement_rate == 0.0
        assert s.ready_for_enforcement is False
