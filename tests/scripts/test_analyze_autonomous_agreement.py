"""Pin Phase 5.B session 2 agreement analyzer:

  * _analyze fail-opens on DB error
  * autonomous_share math
  * quality math per source
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
    "analyze_autonomous_agreement",
    _ROOT / "scripts" / "analyze_autonomous_agreement.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["analyze_autonomous_agreement"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestAnalyze:
    def test_db_error_returns_zeros(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        r = _MOD._analyze(conn, 4)
        assert r["n_total"] == 0
        assert r["autonomous_share"] is None

    def test_autonomous_share_math(self):
        conn = MagicMock()
        # First query: volume; second query: quality
        vol_result = MagicMock()
        vol_result.fetchone.return_value = {
            "n_auto": 30, "n_llm_accept": 40, "n_llm_reject": 20,
            "n_operator": 10, "n_total": 100,
        }
        qual_result = MagicMock()
        qual_result.fetchone.return_value = {
            "auto_improved": 45, "auto_regressed": 15,
            "op_improved": 6, "op_regressed": 2,
        }
        conn.execute.side_effect = [vol_result, qual_result]
        r = _MOD._analyze(conn, 4)
        # (30+40+20) / 100 = 90%
        assert r["autonomous_share"] == pytest.approx(90.0)
        # Auto quality: 45 / (45+15) = 75%
        assert r["quality_auto"] == pytest.approx(75.0)
        # Operator quality: 6 / (6+2) = 75%
        assert r["quality_operator"] == pytest.approx(75.0)

    def test_zero_operator_baseline_quality_none(self):
        conn = MagicMock()
        vol_result = MagicMock()
        vol_result.fetchone.return_value = {
            "n_auto": 10, "n_llm_accept": 0, "n_llm_reject": 0,
            "n_operator": 0, "n_total": 10,
        }
        qual_result = MagicMock()
        qual_result.fetchone.return_value = {
            "auto_improved": 5, "auto_regressed": 5,
            "op_improved": 0, "op_regressed": 0,
        }
        conn.execute.side_effect = [vol_result, qual_result]
        r = _MOD._analyze(conn, 4)
        assert r["quality_auto"] == pytest.approx(50.0)
        assert r["quality_operator"] is None


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main([]) == 1
