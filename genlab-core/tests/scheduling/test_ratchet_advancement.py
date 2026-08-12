"""Pin the ratchet advancement signal + auto_approver wire.

Contract:

  * `check_ratchet_advancement_signal(niche_id)` combines
    calibration_logger.stats (operator-agreement path) +
    outcome_readiness.check_outcome_readiness (reward-outcome path).
  * combined_ready = calibration_ready OR (outcome_ready AND flag on)
  * Flag off (default): combined_ready = calibration_ready
    (backward compat with pre-fix behavior).
  * Any DB / query error returns zeroed signal + combined=False.
    Never raises.

  * `log_ratchet_signal(niche_id)` emits INFO log with structured
    per-niche state.

Structural pin:

  * auto_approver.run_pass calls log_ratchet_signal at the top,
    BEFORE guards, so operator sees the signal even when the pass
    is disabled by kill-switch or per-niche policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from genlab_core.scheduling.ratchet_advancement import (
    RatchetAdvancementSignal,
    check_ratchet_advancement_signal,
    log_ratchet_signal,
)


@dataclass
class _FakeCalStats:
    """Minimal stub matching CalibrationStats fields we read."""

    sample_count: int
    agreement_count: int
    ready_for_enforcement: bool

    @property
    def agreement_rate(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return self.agreement_count / self.sample_count


@dataclass
class _FakeOutcome:
    """Minimal stub matching OutcomeReadiness fields we read."""

    sample_count: int
    outcome_good_count: int
    outcome_good_rate: float
    ready: bool


class TestCalibrationOnlyPath:
    """When outcome flag is off, only calibration_ready matters
    (pre-fix behavior preserved)."""

    def test_calibration_ready_flag_off_combined_true(self, monkeypatch):
        monkeypatch.delenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", raising=False)
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=_FakeCalStats(
                sample_count=50, agreement_count=48, ready_for_enforcement=True,
            ),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            return_value=_FakeOutcome(
                sample_count=100, outcome_good_count=80, outcome_good_rate=0.8, ready=True,
            ),
        ):
            signal = check_ratchet_advancement_signal("sports")
        assert signal.combined_ready is True
        assert signal.calibration_ready is True

    def test_calibration_not_ready_flag_off_combined_false(self, monkeypatch):
        """Outcome ready alone is not enough when flag is off."""
        monkeypatch.delenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", raising=False)
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=_FakeCalStats(
                sample_count=0, agreement_count=0, ready_for_enforcement=False,
            ),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            return_value=_FakeOutcome(
                sample_count=100, outcome_good_count=80, outcome_good_rate=0.8, ready=True,
            ),
        ):
            signal = check_ratchet_advancement_signal("sports")
        assert signal.combined_ready is False


class TestCombinedPathWithFlag:
    """When outcome flag is on, either signal satisfies (OR logic).
    Closes the "operator hasn't clicked in 24 days" stuck-loop."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", "1")

    def test_outcome_ready_alone_advances(self):
        """The core stuck-loop unlock: operator has 0 clicks but
        reward signal is strong -> combined_ready=True."""
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=_FakeCalStats(
                sample_count=0, agreement_count=0, ready_for_enforcement=False,
            ),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            return_value=_FakeOutcome(
                sample_count=50, outcome_good_count=42, outcome_good_rate=0.84, ready=True,
            ),
        ):
            signal = check_ratchet_advancement_signal("gaming")
        assert signal.combined_ready is True
        assert signal.outcome_ready is True
        assert signal.calibration_ready is False

    def test_calibration_ready_alone_advances(self):
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=_FakeCalStats(
                sample_count=50, agreement_count=48, ready_for_enforcement=True,
            ),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            return_value=_FakeOutcome(
                sample_count=5, outcome_good_count=1, outcome_good_rate=0.2, ready=False,
            ),
        ):
            signal = check_ratchet_advancement_signal("sports")
        assert signal.combined_ready is True

    def test_neither_ready_combined_false(self):
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=_FakeCalStats(
                sample_count=5, agreement_count=3, ready_for_enforcement=False,
            ),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            return_value=_FakeOutcome(
                sample_count=5, outcome_good_count=1, outcome_good_rate=0.2, ready=False,
            ),
        ):
            signal = check_ratchet_advancement_signal("anime")
        assert signal.combined_ready is False

    def test_outcome_query_none_still_uses_calibration(self):
        """When outcome query returns None (DB missing / import error),
        combined_ready falls back to calibration_ready alone."""
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=_FakeCalStats(
                sample_count=50, agreement_count=48, ready_for_enforcement=True,
            ),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            return_value=None,
        ):
            signal = check_ratchet_advancement_signal("sports")
        assert signal.combined_ready is True
        assert signal.calibration_ready is True
        assert signal.outcome_samples == 0


class TestFailOpen:
    def test_calibration_raises_signal_still_returned(self, monkeypatch):
        monkeypatch.setenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", "1")
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            side_effect=RuntimeError("db down"),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            return_value=_FakeOutcome(
                sample_count=50, outcome_good_count=42, outcome_good_rate=0.84, ready=True,
            ),
        ):
            signal = check_ratchet_advancement_signal("sports")
        # Signal returned despite calibration raising
        assert signal.calibration_samples == 0
        assert signal.outcome_ready is True
        assert signal.combined_ready is True  # outcome path saves it

    def test_outcome_raises_signal_still_returned(self, monkeypatch):
        monkeypatch.setenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", "1")
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=_FakeCalStats(
                sample_count=50, agreement_count=48, ready_for_enforcement=True,
            ),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            side_effect=RuntimeError("psycopg import failed"),
        ):
            signal = check_ratchet_advancement_signal("sports")
        assert signal.calibration_ready is True
        assert signal.outcome_samples == 0

    def test_both_raise_zero_signal(self, monkeypatch):
        monkeypatch.setenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", "1")
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            side_effect=RuntimeError("db down"),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            side_effect=RuntimeError("psycopg import failed"),
        ):
            signal = check_ratchet_advancement_signal("sports")
        assert signal.combined_ready is False
        assert signal.calibration_samples == 0
        assert signal.outcome_samples == 0


class TestLogDedup:
    """Log-level dedup: INFO only on material state change; DEBUG on
    identical repeat emissions. Turns 240 near-identical lines/day
    into ~2-5 informative INFO lines/day per niche."""

    @pytest.fixture(autouse=True)
    def _state_in_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_RATCHET_LOG_STATE_PATH",
            str(tmp_path / "ratchet_log_state.json"),
        )

    def _patched(self, monkeypatch, cal_stats, outcome, flag_on=False):
        if flag_on:
            monkeypatch.setenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", "1")
        else:
            monkeypatch.delenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", raising=False)
        return (
            patch(
                "genlab_core.scheduling.calibration_logger.stats",
                return_value=cal_stats,
            ),
            patch(
                "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
                return_value=outcome,
            ),
        )

    def test_first_call_emits_info(self, monkeypatch, caplog):
        cal, out = self._patched(
            monkeypatch,
            _FakeCalStats(sample_count=10, agreement_count=8, ready_for_enforcement=False),
            _FakeOutcome(sample_count=13, outcome_good_count=11, outcome_good_rate=0.85, ready=False),
        )
        with cal, out, caplog.at_level(logging.INFO):
            log_ratchet_signal("ai_creators")
        info_records = [r for r in caplog.records if r.levelname == "INFO" and "[ratchet]" in r.message]
        assert len(info_records) == 1

    def test_identical_second_call_is_debug(self, monkeypatch, caplog):
        cal_stats = _FakeCalStats(sample_count=10, agreement_count=8, ready_for_enforcement=False)
        outcome = _FakeOutcome(sample_count=13, outcome_good_count=11, outcome_good_rate=0.85, ready=False)
        # First call establishes baseline
        cal, out = self._patched(monkeypatch, cal_stats, outcome)
        with cal, out:
            log_ratchet_signal("ai_creators")
        caplog.clear()
        # Second identical call must NOT emit INFO
        cal, out = self._patched(monkeypatch, cal_stats, outcome)
        with cal, out, caplog.at_level(logging.DEBUG):
            log_ratchet_signal("ai_creators")
        info_ratchet = [
            r for r in caplog.records if r.levelname == "INFO" and "[ratchet]" in r.message
        ]
        debug_ratchet = [
            r for r in caplog.records if r.levelname == "DEBUG" and "[ratchet]" in r.message
        ]
        assert len(info_ratchet) == 0
        assert len(debug_ratchet) == 1

    def test_combined_ready_flip_emits_info(self, monkeypatch, caplog):
        cal_stats_a = _FakeCalStats(sample_count=10, agreement_count=8, ready_for_enforcement=False)
        outcome_a = _FakeOutcome(sample_count=13, outcome_good_count=11, outcome_good_rate=0.85, ready=False)
        # First call — establish baseline (combined=False since flag off)
        cal, out = self._patched(monkeypatch, cal_stats_a, outcome_a)
        with cal, out:
            log_ratchet_signal("ai_creators")
        caplog.clear()
        # Turn flag on — outcome_ready=True now flips combined_ready to True
        outcome_b = _FakeOutcome(sample_count=13, outcome_good_count=11, outcome_good_rate=0.85, ready=True)
        cal, out = self._patched(monkeypatch, cal_stats_a, outcome_b, flag_on=True)
        with cal, out, caplog.at_level(logging.INFO):
            log_ratchet_signal("ai_creators")
        info_ratchet = [
            r for r in caplog.records if r.levelname == "INFO" and "[ratchet]" in r.message
        ]
        assert len(info_ratchet) == 1
        assert "combined=True" in info_ratchet[0].message

    def test_sample_count_jump_emits_info(self, monkeypatch, caplog):
        cal, out = self._patched(
            monkeypatch,
            _FakeCalStats(sample_count=10, agreement_count=8, ready_for_enforcement=False),
            _FakeOutcome(sample_count=13, outcome_good_count=11, outcome_good_rate=0.85, ready=False),
        )
        with cal, out:
            log_ratchet_signal("gaming")
        caplog.clear()
        # outcome_samples jumped by 7 -> should re-emit INFO
        cal, out = self._patched(
            monkeypatch,
            _FakeCalStats(sample_count=10, agreement_count=8, ready_for_enforcement=False),
            _FakeOutcome(sample_count=20, outcome_good_count=17, outcome_good_rate=0.85, ready=False),
        )
        with cal, out, caplog.at_level(logging.INFO):
            log_ratchet_signal("gaming")
        info_ratchet = [
            r for r in caplog.records if r.levelname == "INFO" and "[ratchet]" in r.message
        ]
        assert len(info_ratchet) == 1

    def test_sub_threshold_sample_change_is_debug(self, monkeypatch, caplog):
        """Sample count change < 5 — not material, DEBUG only."""
        cal, out = self._patched(
            monkeypatch,
            _FakeCalStats(sample_count=10, agreement_count=8, ready_for_enforcement=False),
            _FakeOutcome(sample_count=13, outcome_good_count=11, outcome_good_rate=0.85, ready=False),
        )
        with cal, out:
            log_ratchet_signal("sports")
        caplog.clear()
        # outcome_samples changed by only 3 — below the >= 5 threshold
        cal, out = self._patched(
            monkeypatch,
            _FakeCalStats(sample_count=10, agreement_count=8, ready_for_enforcement=False),
            _FakeOutcome(sample_count=16, outcome_good_count=14, outcome_good_rate=0.875, ready=False),
        )
        with cal, out, caplog.at_level(logging.DEBUG):
            log_ratchet_signal("sports")
        info_ratchet = [
            r for r in caplog.records if r.levelname == "INFO" and "[ratchet]" in r.message
        ]
        assert len(info_ratchet) == 0

    def test_per_niche_dedup_independent(self, monkeypatch, caplog):
        """Each niche has its own dedup state — gaming's log doesn't
        affect sports' emission decision."""
        cal_stats = _FakeCalStats(sample_count=10, agreement_count=8, ready_for_enforcement=False)
        outcome = _FakeOutcome(sample_count=13, outcome_good_count=11, outcome_good_rate=0.85, ready=False)
        cal, out = self._patched(monkeypatch, cal_stats, outcome)
        with cal, out, caplog.at_level(logging.INFO):
            log_ratchet_signal("gaming")
            log_ratchet_signal("sports")
        info_ratchet = [
            r for r in caplog.records if r.levelname == "INFO" and "[ratchet]" in r.message
        ]
        # Both niches emit their own INFO (both are "first call after startup")
        assert len(info_ratchet) == 2


class TestLogEmission:
    def test_log_line_shape(self, monkeypatch, caplog):
        monkeypatch.delenv("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED", raising=False)
        with patch(
            "genlab_core.scheduling.calibration_logger.stats",
            return_value=_FakeCalStats(
                sample_count=10, agreement_count=8, ready_for_enforcement=False,
            ),
        ), patch(
            "genlab_core.scheduling.ratchet_advancement._query_outcome_readiness",
            return_value=_FakeOutcome(
                sample_count=28, outcome_good_count=12, outcome_good_rate=0.43, ready=False,
            ),
        ), caplog.at_level(logging.INFO):
            log_ratchet_signal("sports")
        msg = next(r.message for r in caplog.records if "[ratchet]" in r.message)
        assert "niche=sports" in msg
        assert "combined=False" in msg
        assert "calibration=8/10" in msg
        assert "outcome=12/28" in msg


class TestAutoApproverWire:
    def test_run_pass_source_contains_wire(self):
        """Structural pin: auto_approver.run_pass calls log_ratchet_signal
        at the top of the pass. Guards against the wire being deleted."""
        import pathlib

        path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "scheduling"
            / "auto_approver.py"
        )
        src = path.read_text()
        assert "from genlab_core.scheduling.ratchet_advancement import log_ratchet_signal" in src
        assert "log_ratchet_signal(niche_id)" in src
