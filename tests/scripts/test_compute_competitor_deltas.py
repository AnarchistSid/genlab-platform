"""Pin Phase 3.A competitor-deltas runner shape:

  * `_artifact_dir` honours GENLAB_TMP
  * `_load_latest_artifact` skips stale files older than lookback window
  * `_load_latest_artifact` picks the most recent file
  * `_load_latest_artifact` returns None on malformed JSON (fail-open)
  * `_fetch_video_stats` returns {} on missing api key input (via empty ids)
  * Runner exits 1 when DATABASE_URL is unset
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "compute_competitor_deltas",
    _ROOT / "scripts" / "compute_competitor_deltas.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["compute_competitor_deltas"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestArtifactDir:
    def test_honours_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert _MOD._artifact_dir() == tmp_path / "top-creator-uploads"

    def test_falls_back_to_cwd(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GENLAB_TMP", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _MOD._artifact_dir() == tmp_path / ".tmp" / "top-creator-uploads"


class TestLoadLatestArtifact:
    def _write(self, dir_, stamp, niche, payload):
        dir_.mkdir(parents=True, exist_ok=True)
        (dir_ / f"{stamp}-{niche}.json").write_text(json.dumps(payload))

    def test_cold_start_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert _MOD._load_latest_artifact("gaming", 7) is None

    def test_picks_most_recent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "top-creator-uploads"
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        today = date.today().strftime("%Y%m%d")
        self._write(dir_, yesterday, "gaming", {"tag": "old"})
        self._write(dir_, today, "gaming", {"tag": "new"})
        result = _MOD._load_latest_artifact("gaming", 7)
        assert result == {"tag": "new"}

    def test_skips_stale_beyond_lookback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "top-creator-uploads"
        stale = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
        self._write(dir_, stale, "gaming", {"tag": "very_old"})
        # lookback of 7 days excludes the 30-day-old file
        assert _MOD._load_latest_artifact("gaming", 7) is None

    def test_malformed_json_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "top-creator-uploads"
        dir_.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        (dir_ / f"{today}-gaming.json").write_text("not json {{")
        assert _MOD._load_latest_artifact("gaming", 7) is None

    def test_filters_by_niche_suffix(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        dir_ = tmp_path / "top-creator-uploads"
        today = date.today().strftime("%Y%m%d")
        self._write(dir_, today, "gaming", {"tag": "gaming"})
        self._write(dir_, today, "anime", {"tag": "anime"})
        # Should not pick up gaming.json when asking for anime
        assert _MOD._load_latest_artifact("anime", 7) == {"tag": "anime"}
        assert _MOD._load_latest_artifact("gaming", 7) == {"tag": "gaming"}


class TestFetchVideoStats:
    def test_empty_ids_returns_empty(self):
        assert _MOD._fetch_video_stats([], "fake_key") == {}


class TestMainExitCodes:
    def test_missing_dsn_exits_1(self, monkeypatch, capsys):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        exit_code = _MOD.main(["--dry-run"])
        assert exit_code == 1

    def test_missing_api_key_exits_1(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        exit_code = _MOD.main(["--dry-run"])
        assert exit_code == 1


class TestActiveNiches:
    def test_five_niches_configured(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports"
        }


class TestPathContractWithWatcher:
    """Pin the fetcher-schema-drift bug caught 2026-08-14 on first
    prod dry-run: this runner MUST read the same directory the
    top-creator watcher writes. If either side changes their
    _artifact_dir path, this test catches it at CI time — the class-
    of-bug this catches is `shared-contract, N-implementers, silent
    divergence` per operator memory index."""

    def test_deltas_runner_reads_watcher_output_path(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        # Import the watcher's _artifact_dir directly so any future
        # rename on either side breaks the assertion
        watcher_spec = importlib.util.spec_from_file_location(
            "watch_top_creator_uploads_for_pin",
            _ROOT / "scripts" / "watch_top_creator_uploads.py",
        )
        watcher_mod = importlib.util.module_from_spec(watcher_spec)
        sys.modules["watch_top_creator_uploads_for_pin"] = watcher_mod
        watcher_spec.loader.exec_module(watcher_mod)
        assert watcher_mod._artifact_dir() == _MOD._artifact_dir(), (
            "watcher writes to one path but deltas runner reads another — "
            "any fix must update BOTH scripts (fetcher-schema-drift bug)"
        )
