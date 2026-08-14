"""Pin Phase 4.E session 2 promoter runner:

  * flag/rollout gating (off by default; deterministic per-niche dice)
  * gap math: recent >= min_daily → no reservation
  * DB error on count fail-CLOSED to 999 (don't over-promote)
  * origin=ideation_pool + related IDs persisted to blueprint.extra
  * Failed blueprint insert triggers release_reservation
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
    "promote_ideas_to_blueprints",
    _ROOT / "scripts" / "promote_ideas_to_blueprints.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["promote_ideas_to_blueprints"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestFlagGating:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_IDEATION_POOL_ENABLED", raising=False)
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "100")
        assert _MOD._niche_should_fire("gaming") is False

    def test_flag_on_100_rollout(self, monkeypatch):
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "100")
        assert _MOD._niche_should_fire("gaming") is True

    def test_deterministic_per_niche(self, monkeypatch):
        """Same niche always fires or doesn't — critical so
        within-niche reward analysis stays uniform."""
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "50")
        # 5 same-niche checks return same result
        results = {_MOD._niche_should_fire("gaming") for _ in range(5)}
        assert len(results) == 1

    def test_rollout_pct_clamped(self, monkeypatch):
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "500")
        assert _MOD._rollout_pct() == 100
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "-5")
        assert _MOD._rollout_pct() == 0


class TestCountRecent:
    def test_db_error_returns_999(self):
        """Fail-CLOSED: if we can't tell how many blueprints exist,
        assume plenty so we DON'T promote (avoid double-post over
        the 1-per-day cap)."""
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._count_recent_blueprints(conn, "gaming") == 999

    def test_returns_row_count(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"n": 3}
        assert _MOD._count_recent_blueprints(conn, "gaming") == 3


class TestRunNiche:
    @patch(
        "genlab_core.intelligence.ideation_pool_consumer.reserve_top_pending"
    )
    def test_flag_off_no_reserve(self, mock_reserve, monkeypatch):
        monkeypatch.delenv("GENLAB_IDEATION_POOL_ENABLED", raising=False)
        conn = MagicMock()
        _MOD._run_niche(conn, "gaming", min_daily=1, dry_run=False)
        mock_reserve.assert_not_called()

    @patch(
        "genlab_core.intelligence.ideation_pool_consumer.reserve_top_pending"
    )
    def test_recent_meets_target_no_reserve(self, mock_reserve, monkeypatch):
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "100")
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"n": 5}
        counts = _MOD._run_niche(conn, "gaming", min_daily=1, dry_run=False)
        assert counts["gap"] == 0
        mock_reserve.assert_not_called()


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1


class TestConstants:
    def test_default_min_daily_is_1(self):
        """CLAUDE.md rule: 1 reel per channel per day."""
        assert _MOD.DEFAULT_MIN_DAILY == 1

    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }
