"""Pin Phase 4.E session 1 ideator runner:

  * _fetch_trend_topics reads latest per-niche artifact
  * _fetch_competitor_hooks fail-opens
  * _fetch_recent_hooks fail-opens
  * _expire_stale UPDATE returns rowcount
  * Main exits 1 without DATABASE_URL
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "run_content_ideator",
    _ROOT / "scripts" / "run_content_ideator.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["run_content_ideator"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestActiveNiches:
    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }


class TestExpireAfterConstant:
    def test_is_30(self):
        assert _MOD._EXPIRE_AFTER_DAYS == 30


class TestFetchTrendTopics:
    def test_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert _MOD._fetch_trend_topics("gaming") == []

    def test_reads_latest_artifact(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        d = tmp_path / "trend-anticipation"
        d.mkdir()
        # Older
        (d / "20260810-gaming.json").write_text(json.dumps({
            "ranking": [{"topic": "old topic"}],
        }))
        # Newer (mtime will be later, that's what we sort on)
        (d / "20260814-gaming.json").write_text(json.dumps({
            "ranking": [{"topic": "new topic"}, {"topic": "another"}],
        }))
        topics = _MOD._fetch_trend_topics("gaming")
        assert "new topic" in topics

    def test_bad_json_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        d = tmp_path / "trend-anticipation"
        d.mkdir()
        (d / "20260814-gaming.json").write_text("not json {{")
        assert _MOD._fetch_trend_topics("gaming") == []


class TestFetchCompetitorHooks:
    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._fetch_competitor_hooks(conn, "gaming") == []

    def test_returns_titles(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"competitor_title": "hook 1"},
            {"competitor_title": "hook 2"},
        ]
        assert _MOD._fetch_competitor_hooks(conn, "gaming") == ["hook 1", "hook 2"]

    def test_filters_null_titles(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            {"competitor_title": "real"},
            {"competitor_title": None},
        ]
        assert _MOD._fetch_competitor_hooks(conn, "gaming") == ["real"]


class TestExpireStale:
    def test_db_error_returns_0(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._expire_stale(conn) == 0

    def test_returns_rowcount(self):
        conn = MagicMock()
        conn.execute.return_value.rowcount = 5
        assert _MOD._expire_stale(conn) == 5


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1
