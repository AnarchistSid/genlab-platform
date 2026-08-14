"""Pin cleanup_invalid_arm_experiments logic:

  * `_extract_arms` handles str + dict spec
  * `_extract_arms` returns [] on malformed input
  * Main exits 1 without DATABASE_URL
  * --apply + --dry-run rejected as mutually exclusive
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "cleanup_invalid_arm_experiments",
    _ROOT / "scripts" / "cleanup_invalid_arm_experiments.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["cleanup_invalid_arm_experiments"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestExtractArms:
    def test_dict_spec(self):
        assert _MOD._extract_arms({"arms": ["a", "b"]}) == ["a", "b"]

    def test_string_spec_parsed(self):
        assert _MOD._extract_arms('{"arms": ["x", "y"]}') == ["x", "y"]

    def test_none_returns_empty(self):
        assert _MOD._extract_arms(None) == []

    def test_missing_arms_key(self):
        assert _MOD._extract_arms({"other": "field"}) == []

    def test_arms_not_a_list(self):
        assert _MOD._extract_arms({"arms": "just_a_string"}) == []

    def test_malformed_json(self):
        assert _MOD._extract_arms("not json {{") == []

    def test_strips_whitespace(self):
        assert _MOD._extract_arms({"arms": ["  a  ", "b\n"]}) == ["a", "b"]

    def test_drops_empty_strings(self):
        assert _MOD._extract_arms({"arms": ["a", "", "b"]}) == ["a", "b"]


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1

    def test_apply_plus_dry_run_rejected(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        assert _MOD.main(["--apply", "--dry-run"]) == 1
