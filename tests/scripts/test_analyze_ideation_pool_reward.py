"""Pin Phase 4.E session 3 pool-vs-trending reward analyzer:

  * _analyze_niche fail-opens on DB error
  * pool_share_pct math
  * lift_pct math (t - c) / c * 100
  * Zero-trending mean → lift=None
  * Zero-pool count → pool_share_pct 0
  * Main exits 1 without DATABASE_URL
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "analyze_ideation_pool_reward",
    _ROOT / "scripts" / "analyze_ideation_pool_reward.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["analyze_ideation_pool_reward"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestAnalyzeNiche:
    def test_db_error_returns_zeros(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        r = _MOD._analyze_niche(conn, "gaming", 30)
        assert r["n_pool"] == 0
        assert r["n_trending"] == 0
        assert r["pool_share_pct"] is None
        assert r["lift_pct"] is None

    def test_pool_share_computation(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_pool": 20, "n_trending": 80,
            "mean_pool": 0.15, "mean_trending": 0.10,
        }
        r = _MOD._analyze_niche(conn, "gaming", 30)
        # 20 / (20+80) = 20%
        assert r["pool_share_pct"] == pytest.approx(20.0)
        # (0.15 - 0.10) / 0.10 * 100 = 50% lift
        assert r["lift_pct"] == pytest.approx(50.0)

    def test_zero_trending_mean_no_lift(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_pool": 5, "n_trending": 5,
            "mean_pool": 0.1, "mean_trending": 0.0,
        }
        r = _MOD._analyze_niche(conn, "gaming", 30)
        assert r["lift_pct"] is None

    def test_zero_pool_and_trending_no_share(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_pool": 0, "n_trending": 0,
            "mean_pool": None, "mean_trending": None,
        }
        r = _MOD._analyze_niche(conn, "gaming", 30)
        assert r["pool_share_pct"] is None


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main([]) == 1


class TestActiveNiches:
    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }
