"""Pin Phase 4.A session 3 quality scorer runner:

  * _resolve_video_path checks all 4 conventions + returns None
    when file doesn't exist
  * _resolve_video_path handles dict AND JSON-string extra
  * _run_niche fail-open when candidate query errors
  * Runner exits 1 without DATABASE_URL
  * BRAND_COLORS matches the 5-niche registry
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "run_quality_scorer",
    _ROOT / "scripts" / "run_quality_scorer.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["run_quality_scorer"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestBrandColors:
    def test_five_niches_present(self):
        assert set(_MOD._BRAND_COLORS.keys()) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }

    def test_all_hex_format(self):
        for niche, color in _MOD._BRAND_COLORS.items():
            assert color.startswith("#"), f"{niche} missing #"
            assert len(color) == 7, f"{niche} not 6 hex chars"


class TestResolveVideoPath:
    def test_none_extra_returns_none(self):
        assert _MOD._resolve_video_path(None) is None

    def test_string_extra_parses_json(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        extra = json.dumps({"media": {"render_path": str(video)}})
        assert _MOD._resolve_video_path(extra) == video

    def test_dict_extra_direct(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        extra = {"media": {"render_path": str(video)}}
        assert _MOD._resolve_video_path(extra) == video

    def test_final_render_path_key(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        extra = {"media": {"final_render_path": str(video)}}
        assert _MOD._resolve_video_path(extra) == video

    def test_missing_file_returns_none(self, tmp_path):
        extra = {"media": {"render_path": str(tmp_path / "nope.mp4")}}
        assert _MOD._resolve_video_path(extra) is None

    def test_no_media_key(self):
        assert _MOD._resolve_video_path({"other": "field"}) is None

    def test_malformed_json_string(self):
        assert _MOD._resolve_video_path("not json {{") is None


class TestResolveVideoPathStrategy2:
    """Strategy 2: disk-glob fallback via story_id convention.

    Discovered 2026-08-14 that no blueprint stores render_path in
    extra today, so this fallback is load-bearing. Pin the convention
    from base_visual_render.py:198 so if it ever changes, this test
    breaks and forces the runner to update in tandem."""

    def _setup_convention_layout(
        self, tmp_path, story_id: str, niche: str = "gaming",
    ):
        run_dir = tmp_path / "runs" / f"{niche}_20260814_120000"
        vis_dir = run_dir / "visuals" / story_id
        vis_dir.mkdir(parents=True)
        video = vis_dir / f"{story_id[:16]}_reel.mp4"
        video.write_bytes(b"fake mp4")
        return video

    def test_glob_finds_video_by_story_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        story_id = "5bc794863a300f4472c5b6301e3e8b9589c5084aeedc1b8bf9b309a4e2245ca9"
        expected = self._setup_convention_layout(tmp_path, story_id)
        result = _MOD._resolve_video_path(None, story_id=story_id)
        assert result == expected

    def test_no_story_id_no_glob(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert _MOD._resolve_video_path(None, story_id=None) is None
        assert _MOD._resolve_video_path(None, story_id="") is None

    def test_missing_runs_dir_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))  # no runs/ subdir
        assert _MOD._resolve_video_path(None, story_id="a" * 64) is None

    def test_extra_takes_precedence_over_glob(self, tmp_path, monkeypatch):
        """When both strategies could return a file, extra wins so
        future renderers that DO populate extra can override the
        convention."""
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        story_id = "a" * 64
        self._setup_convention_layout(tmp_path, story_id)
        override = tmp_path / "override.mp4"
        override.write_bytes(b"fake")
        extra = {"media": {"render_path": str(override)}}
        result = _MOD._resolve_video_path(extra, story_id=story_id)
        assert result == override

    def test_glob_picks_most_recent_when_multiple(self, tmp_path, monkeypatch):
        """Re-renders produce duplicate copies under different run
        directories. mtime DESC sort ensures we pick the fresh one."""
        import time
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        story_id = "b" * 64
        old = self._setup_convention_layout(tmp_path, story_id, niche="a")
        time.sleep(0.05)
        new_dir = (
            tmp_path / "runs" / "b_20260814_130000" / "visuals" / story_id
        )
        new_dir.mkdir(parents=True)
        new = new_dir / f"{story_id[:16]}_reel.mp4"
        new.write_bytes(b"newer bytes")
        result = _MOD._resolve_video_path(None, story_id=story_id)
        assert result == new


class TestFindUnscored:
    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        result = _MOD._find_unscored_blueprints(conn, "gaming", 48, None)
        assert result == []


class TestAlreadyScored:
    def test_returns_true_when_row_exists(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"1": 1}
        assert _MOD._already_scored(conn, "bp-1", "hash-1") is True

    def test_returns_false_when_row_missing(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        assert _MOD._already_scored(conn, "bp-1", "hash-1") is False

    def test_db_error_returns_false(self):
        """Fail-open: false means we'd attempt to re-score, but the
        UNIQUE constraint + ON CONFLICT DO NOTHING catches the dup."""
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert _MOD._already_scored(conn, "bp-1", "hash-1") is False


class TestMainExit:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1


class TestActiveNiches:
    def test_five_niches(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }
