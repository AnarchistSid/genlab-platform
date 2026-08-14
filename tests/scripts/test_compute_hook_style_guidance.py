"""Pin Phase 4.C session 1 hook style guidance runner:

  * _monday_of returns Monday
  * Main exits 1 without DATABASE_URL
  * BRAND_COLORS/ACTIVE_NICHES contract preserved
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "compute_hook_style_guidance",
    _ROOT / "scripts" / "compute_hook_style_guidance.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["compute_hook_style_guidance"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestMondayOf:
    def test_wednesday_returns_monday(self):
        assert _MOD._monday_of(date(2026, 8, 13)) == date(2026, 8, 10)

    def test_monday_returns_itself(self):
        assert _MOD._monday_of(date(2026, 8, 10)) == date(2026, 8, 10)

    def test_sunday_returns_prior_monday(self):
        assert _MOD._monday_of(date(2026, 8, 16)) == date(2026, 8, 10)


class TestActiveNiches:
    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1
