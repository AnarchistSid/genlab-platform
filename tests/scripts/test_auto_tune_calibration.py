"""Pin Phase 5.A tuner runner:

  * _monday_of anchors on Monday
  * _current_min_confidence returns 0.85 default when file missing
  * _load_recent_calibration fail-opens
  * Main exits 1 without DATABASE_URL
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "auto_tune_calibration",
    _ROOT / "scripts" / "auto_tune_calibration.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["auto_tune_calibration"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestMondayOf:
    def test_wednesday_returns_monday(self):
        assert _MOD._monday_of(date(2026, 8, 13)) == date(2026, 8, 10)

    def test_sunday_returns_prior_monday(self):
        assert _MOD._monday_of(date(2026, 8, 16)) == date(2026, 8, 10)


class TestLoadRecentCalibration:
    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._load_recent_calibration(conn, "gaming", 4) == []

    def test_normalizes_rows(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"gate_approved": True, "operator_action": "approved"},
            {"gate_approved": False, "operator_action": "rejected"},
        ]
        rows = _MOD._load_recent_calibration(conn, "gaming", 4)
        assert len(rows) == 2
        assert rows[0]["operator_action"] == "approved"


class TestConstants:
    def test_lookback_weeks_is_4(self):
        assert _MOD._LOOKBACK_WEEKS == 4

    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }


class TestApplyYamlRewrite:
    def test_missing_yaml_returns_false(self, monkeypatch):
        """Non-existent niche → no yaml → fail cleanly."""
        # Force path lookup to return None
        monkeypatch.setattr(_MOD, "_publishing_yaml_path", lambda n: None)
        ok, msg = _MOD._apply_yaml_rewrite("nonexistent", 0.90)
        assert ok is False

    def test_rewrites_min_confidence(self, tmp_path, monkeypatch):
        yaml_path = tmp_path / "publishing.yaml"
        yaml_path.write_text(
            "auto_publish:\n  enabled: true\n  min_confidence: 0.80\n"
            "  rollout_pct: 1.0\n"
        )
        monkeypatch.setattr(
            _MOD, "_publishing_yaml_path", lambda n: yaml_path,
        )
        ok, msg = _MOD._apply_yaml_rewrite("gaming", 0.85)
        assert ok is True
        # Verify file was updated
        import yaml
        updated = yaml.safe_load(yaml_path.read_text())
        assert updated["auto_publish"]["min_confidence"] == 0.85
        # Verify backup created
        backups = list(tmp_path.glob("publishing.yaml.bak.*"))
        assert len(backups) == 1

    def test_preserves_other_keys(self, tmp_path, monkeypatch):
        """Only min_confidence should change — other keys stay."""
        yaml_path = tmp_path / "publishing.yaml"
        yaml_path.write_text(
            "auto_publish:\n"
            "  enabled: true\n"
            "  min_confidence: 0.70\n"
            "  rollout_pct: 0.5\n"
            "some_other_key: value\n"
        )
        monkeypatch.setattr(
            _MOD, "_publishing_yaml_path", lambda n: yaml_path,
        )
        _MOD._apply_yaml_rewrite("gaming", 0.75)
        import yaml
        updated = yaml.safe_load(yaml_path.read_text())
        assert updated["auto_publish"]["enabled"] is True
        assert updated["auto_publish"]["rollout_pct"] == 0.5
        assert updated["some_other_key"] == "value"
        assert updated["auto_publish"]["min_confidence"] == 0.75


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1
