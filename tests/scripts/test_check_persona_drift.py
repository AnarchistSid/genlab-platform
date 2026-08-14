"""Pin Phase 4.D persona drift runner:

  * 1-of-N sampling (sample_rate=20 default)
  * DB error → empty candidate list
  * Below-threshold triggers alert emit
  * Above-threshold no alert
  * Main exits 1 without DATABASE_URL
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "check_persona_drift",
    _ROOT / "scripts" / "check_persona_drift.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["check_persona_drift"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestConstants:
    def test_sample_rate_default_20(self):
        assert _MOD.DEFAULT_SAMPLE_RATE == 20

    def test_alert_threshold_matches_module(self):
        from genlab_core.quality.persona_drift import ALERT_THRESHOLD
        assert _MOD.ALERT_THRESHOLD == ALERT_THRESHOLD

    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }


class TestFindUnscored:
    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._find_unscored_blueprints(conn, "gaming", 7) == []

    def test_normalizes_rows(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"bp_id": "bp-1", "hook_text": "hook 1"},
            {"bp_id": "bp-2", "hook_text": "hook 2"},
        ]
        rows = _MOD._find_unscored_blueprints(conn, "gaming", 7)
        assert len(rows) == 2
        assert rows[0]["blueprint_id"] == "bp-1"


class TestRunNiche:
    def _make_conn(self, candidates):
        conn = MagicMock()

        def _execute(sql, *args):
            result = MagicMock()
            if "blueprints" in sql and "persona_drift_scores pds" in sql:
                result.fetchall.return_value = candidates
            elif "COUNT" in sql:
                result.fetchone.return_value = {"n": 0}
            else:
                result.fetchone.return_value = None
            return result

        conn.execute.side_effect = _execute
        return conn

    def test_sample_rate_20_from_100_yields_5(self):
        """1-of-20 sampling from 100 candidates → 5 sampled."""
        candidates = [
            {"bp_id": f"bp-{i}", "hook_text": f"hook {i}"}
            for i in range(100)
        ]
        conn = self._make_conn(candidates)
        counts = _MOD._run_niche(conn, "gaming", 20, 7, dry_run=True)
        assert counts["candidates"] == 100
        assert counts["sampled"] == 5

    def test_sample_rate_1_samples_all(self):
        candidates = [
            {"bp_id": f"bp-{i}", "hook_text": f"hook {i}"} for i in range(3)
        ]
        conn = self._make_conn(candidates)
        counts = _MOD._run_niche(conn, "gaming", 1, 7, dry_run=True)
        assert counts["sampled"] == 3

    @patch("genlab_core.quality.persona_drift.compute_drift")
    def test_below_threshold_triggers_alert(self, mock_compute):
        """Score below 0.6 fires _emit_alert."""
        from genlab_core.quality.persona_drift import DriftResult
        mock_compute.return_value = DriftResult(
            ok=True, drift_score=0.4,
            reasons=["too casual"], persona_hash="abc",
            llm_cost_usd=0.001,
        )
        candidates = [{"bp_id": "bp-1", "hook_text": "yo dude"}]

        conn = self._make_conn(candidates)
        # Track INSERTs to pipeline_alerts + persona_drift_scores
        counts = _MOD._run_niche(conn, "gaming", 1, 7, dry_run=False)
        assert counts["scored"] == 1
        assert counts["alerts"] == 1

    @patch("genlab_core.quality.persona_drift.compute_drift")
    def test_above_threshold_no_alert(self, mock_compute):
        from genlab_core.quality.persona_drift import DriftResult
        mock_compute.return_value = DriftResult(
            ok=True, drift_score=0.9,
            reasons=["perfect"], persona_hash="abc",
            llm_cost_usd=0.001,
        )
        candidates = [{"bp_id": "bp-1", "hook_text": "polished hook"}]
        conn = self._make_conn(candidates)
        counts = _MOD._run_niche(conn, "gaming", 1, 7, dry_run=False)
        assert counts["scored"] == 1
        assert counts["alerts"] == 0

    @patch("genlab_core.quality.persona_drift.compute_drift")
    def test_compute_failed_counts_skipped(self, mock_compute):
        from genlab_core.quality.persona_drift import DriftResult
        mock_compute.return_value = DriftResult(
            ok=False, reason_code="no_persona",
        )
        candidates = [{"bp_id": "bp-1", "hook_text": "hook"}]
        conn = self._make_conn(candidates)
        counts = _MOD._run_niche(conn, "gaming", 1, 7, dry_run=False)
        assert counts["skipped"] == 1
        assert counts["scored"] == 0


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1
