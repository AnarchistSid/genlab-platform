"""Pin Phase 4.C session 2 A/B analyzer:

  * _analyze_niche fail-opens on DB error
  * Lift computation: (t - c) / c * 100
  * All-zero-control → lift=None (no divide-by-zero)
  * Missing means → lift=None
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "analyze_style_guidance_ab",
    _ROOT / "scripts" / "analyze_style_guidance_ab.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["analyze_style_guidance_ab"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestAnalyzeNiche:
    def test_db_error_returns_zeros(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        r = _MOD._analyze_niche(conn, "gaming", 28)
        assert r["n_control"] == 0
        assert r["n_treatment"] == 0
        assert r["mean_control"] is None
        assert r["lift_pct"] is None

    def test_valid_lift_computation(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_c": 50, "n_t": 30,
            "mean_c": 0.10, "mean_t": 0.115,
        }
        r = _MOD._analyze_niche(conn, "gaming", 28)
        assert r["n_control"] == 50
        assert r["n_treatment"] == 30
        assert r["mean_control"] == 0.10
        assert r["mean_treatment"] == 0.115
        # (0.115 - 0.10) / 0.10 * 100 = 15.0
        assert r["lift_pct"] == pytest.approx(15.0)

    def test_zero_control_mean_no_lift(self):
        """Divide-by-zero guard."""
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_c": 5, "n_t": 5,
            "mean_c": 0.0, "mean_t": 0.1,
        }
        r = _MOD._analyze_niche(conn, "gaming", 28)
        assert r["lift_pct"] is None

    def test_missing_treatment_no_lift(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n_c": 5, "n_t": 0,
            "mean_c": 0.10, "mean_t": None,
        }
        r = _MOD._analyze_niche(conn, "gaming", 28)
        assert r["lift_pct"] is None


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main([]) == 1
