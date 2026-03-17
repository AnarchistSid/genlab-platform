"""Tests for genlab_core.media.download_top_videos."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.download_top_videos import (
    DownloadTopVideos,
    build_clip_index,
    download_videos_for_stories,
)


# ---------------------------------------------------------------------------
# build_clip_index
# ---------------------------------------------------------------------------


class TestBuildClipIndex:
    def test_build_clip_index_format(self):
        """Verify output structure has run_id, clips, videos_total, videos_downloaded."""
        entries = {
            "story_abc": {
                "story_id": "story_abc",
                "success": True,
                "clip_path": "/tmp/clip.mp4",
                "source_url": "https://youtube.com/watch?v=x",
                "backend": "youtube",
                "duration_seconds": 45.0,
                "error": "",
            },
            "story_def": {
                "story_id": "story_def",
                "success": False,
                "clip_path": "",
                "source_url": "",
                "backend": "",
                "duration_seconds": 0.0,
                "error": "no video found",
            },
        }
        result = build_clip_index("run_20260314_120000", entries)

        assert result["run_id"] == "run_20260314_120000"
        assert result["videos_total"] == 2
        assert result["videos_downloaded"] == 1
        assert result["videos_failed"] == 1
        assert "clips" in result
        assert isinstance(result["clips"], dict)
        assert "story_abc" in result["clips"]
        assert "story_def" in result["clips"]
        assert result["clips"]["story_abc"]["success"] is True
        assert result["clips"]["story_def"]["success"] is False

    def test_build_clip_index_empty(self):
        """Empty entries produce valid structure with zero counts."""
        result = build_clip_index("run_empty", {})
        assert result["run_id"] == "run_empty"
        assert result["videos_total"] == 0
        assert result["videos_downloaded"] == 0
        assert result["videos_failed"] == 0
        assert result["clips"] == {}

    def test_build_clip_index_all_success(self):
        entries = {
            f"s{i}": {"story_id": f"s{i}", "success": True}
            for i in range(3)
        }
        result = build_clip_index("run_ok", entries)
        assert result["videos_downloaded"] == 3
        assert result["videos_failed"] == 0


# ---------------------------------------------------------------------------
# download_videos_for_stories
# ---------------------------------------------------------------------------


class TestDownloadVideosForStories:
    def _make_stories(self, n: int) -> list:
        return [
            {"story_id": f"sid_{i}", "title": f"Story {i} about AI"}
            for i in range(n)
        ]

    @patch("genlab_core.media.download_top_videos._download_video")
    @patch("genlab_core.media.download_top_videos._validate_download")
    @patch("genlab_core.media.download_top_videos._find_downloaded_file")
    def test_respects_max_stories(
        self, mock_find, mock_validate, mock_dl, tmp_path,
    ):
        """Pass 5 stories with max_stories=2, verify only 2 processed."""
        mock_video_result = MagicMock()
        mock_video_result.url = "https://youtube.com/watch?v=test"
        mock_video_result.backend = "youtube"

        with patch(
            "genlab_core.media.video_sourcer.VideoSourcer"
        ) as MockSourcer:
            instance = MockSourcer.return_value
            instance.find_video_for_story.return_value = mock_video_result
            instance.get_stats.return_value = {
                "direct_url": 0, "youtube": 2, "reddit": 0, "tmdb": 0, "none": 0,
            }

            mock_dl.return_value = {"success": True, "duration": 1.0, "error": ""}
            mock_find.return_value = str(tmp_path / "clip.mp4")
            mock_validate.return_value = {
                "valid": True, "reason": "", "duration_seconds": 30.0,
            }

            stories = self._make_stories(5)
            entries = download_videos_for_stories(
                stories=stories,
                run_dir=tmp_path,
                niche_id="ai_creators",
                max_stories=2,
            )

        assert len(entries) == 2
        assert instance.find_video_for_story.call_count == 2

    def test_handles_no_video_found(self, tmp_path):
        """VideoSourcer returning None produces success=False entry."""
        with patch(
            "genlab_core.media.video_sourcer.VideoSourcer"
        ) as MockSourcer:
            instance = MockSourcer.return_value
            instance.find_video_for_story.return_value = None
            instance.get_stats.return_value = {
                "direct_url": 0, "youtube": 0, "reddit": 0, "tmdb": 0, "none": 1,
            }

            stories = [{"story_id": "no_vid", "title": "No video here"}]
            entries = download_videos_for_stories(
                stories=stories,
                run_dir=tmp_path,
                niche_id="ai_creators",
                max_stories=10,
            )

        assert len(entries) == 1
        assert entries["no_vid"]["success"] is False
        assert entries["no_vid"]["error"] == "no video found"
        assert entries["no_vid"]["clip_path"] == ""

    @patch("genlab_core.media.download_top_videos._download_video")
    def test_handles_download_failure(self, mock_dl, tmp_path):
        """yt-dlp failure produces success=False with error message."""
        mock_video_result = MagicMock()
        mock_video_result.url = "https://youtube.com/watch?v=fail"
        mock_video_result.backend = "youtube"

        with patch(
            "genlab_core.media.video_sourcer.VideoSourcer"
        ) as MockSourcer:
            instance = MockSourcer.return_value
            instance.find_video_for_story.return_value = mock_video_result
            instance.get_stats.return_value = {
                "direct_url": 0, "youtube": 1, "reddit": 0, "tmdb": 0, "none": 0,
            }

            mock_dl.return_value = {
                "success": False,
                "duration": 0.5,
                "error": "HTTP 403",
            }

            stories = [{"story_id": "fail_dl", "title": "Download fails"}]
            entries = download_videos_for_stories(
                stories=stories,
                run_dir=tmp_path,
                niche_id="ai_creators",
                max_stories=10,
            )

        assert entries["fail_dl"]["success"] is False
        assert "HTTP 403" in entries["fail_dl"]["error"]
        assert entries["fail_dl"]["source_url"] == "https://youtube.com/watch?v=fail"

    def test_generates_story_id_when_missing(self, tmp_path):
        """Stories missing story_id get an auto-generated hash ID."""
        with patch(
            "genlab_core.media.video_sourcer.VideoSourcer"
        ) as MockSourcer:
            instance = MockSourcer.return_value
            instance.find_video_for_story.return_value = None
            instance.get_stats.return_value = {
                "direct_url": 0, "youtube": 0, "reddit": 0, "tmdb": 0, "none": 0,
            }

            stories = [
                {"title": "No ID story"},
                {"story_id": "", "title": "Empty ID"},
            ]
            entries = download_videos_for_stories(
                stories=stories,
                run_dir=tmp_path,
                niche_id="ai_creators",
                max_stories=10,
            )

        # Stories now get processed with auto-generated IDs
        assert len(entries) == 2
        # Verify story_id was backfilled on the original dicts
        assert stories[0]["story_id"] != ""
        assert stories[1]["story_id"] != ""

    @patch("genlab_core.media.download_top_videos._download_video")
    @patch("genlab_core.media.download_top_videos._validate_download")
    @patch("genlab_core.media.download_top_videos._find_downloaded_file")
    def test_validation_failure(
        self, mock_find, mock_validate, mock_dl, tmp_path,
    ):
        """Downloaded file that fails validation produces success=False."""
        mock_video_result = MagicMock()
        mock_video_result.url = "https://youtube.com/watch?v=bad"
        mock_video_result.backend = "youtube"

        with patch(
            "genlab_core.media.video_sourcer.VideoSourcer"
        ) as MockSourcer:
            instance = MockSourcer.return_value
            instance.find_video_for_story.return_value = mock_video_result
            instance.get_stats.return_value = {
                "direct_url": 0, "youtube": 1, "reddit": 0, "tmdb": 0, "none": 0,
            }

            mock_dl.return_value = {"success": True, "duration": 1.0, "error": ""}
            mock_find.return_value = str(tmp_path / "bad.mp4")
            mock_validate.return_value = {
                "valid": False,
                "reason": "file too small (50 bytes)",
                "duration_seconds": 0.0,
            }

            stories = [{"story_id": "bad_file", "title": "Bad download"}]
            entries = download_videos_for_stories(
                stories=stories,
                run_dir=tmp_path,
                niche_id="ai_creators",
                max_stories=10,
            )

        assert entries["bad_file"]["success"] is False
        assert "validation" in entries["bad_file"]["error"]


# ---------------------------------------------------------------------------
# DownloadTopVideos stage class
# ---------------------------------------------------------------------------


class TestDownloadTopVideosStage:
    def test_has_run_and_execute_methods(self):
        """Stage class has both run() and execute() methods."""
        stage = DownloadTopVideos()
        assert hasattr(stage, "run")
        assert hasattr(stage, "execute")
        assert callable(stage.run)
        assert callable(stage.execute)

    @patch("genlab_core.media.download_top_videos.download_videos_for_stories")
    def test_execute_writes_clip_index(self, mock_download, tmp_path):
        """Execute writes clip_index.json and sets context keys."""
        mock_download.return_value = {
            "sid_0": {
                "story_id": "sid_0",
                "success": True,
                "clip_path": str(tmp_path / "clips" / "sid_0.mp4"),
                "source_url": "https://youtube.com/watch?v=x",
                "backend": "youtube",
                "duration_seconds": 30.0,
                "error": "",
            },
        }

        context = {
            "stories": [{"story_id": "sid_0", "title": "Test"}],
            "run_dir": str(tmp_path),
            "run_id": "test_run",
            "niche_id": "ai_creators",
            "niche_config": {"pipeline": {"max_items_per_run": 5}},
        }

        stage = DownloadTopVideos()
        result = stage.execute(context)

        assert "clip_index" in result
        assert result["clip_index"]["run_id"] == "test_run"
        assert result["clip_index"]["videos_downloaded"] == 1

        clip_index_path = Path(result["clip_index_path"])
        assert clip_index_path.exists()

        with open(clip_index_path) as f:
            written = json.load(f)
        assert written["clips"]["sid_0"]["success"] is True

    @patch("genlab_core.media.download_top_videos.download_videos_for_stories")
    def test_execute_empty_stories(self, mock_download, tmp_path):
        """Execute with empty stories list skips download."""
        context = {
            "stories": [],
            "run_dir": str(tmp_path),
            "run_id": "empty_run",
            "niche_id": "ai_creators",
            "niche_config": {"pipeline": {"max_items_per_run": 10}},
        }

        stage = DownloadTopVideos()
        result = stage.execute(context)

        mock_download.assert_not_called()
        assert result["clip_index"]["videos_total"] == 0

    def test_execute_no_run_dir(self):
        """Execute with missing run_dir returns empty clip_index."""
        context = {
            "stories": [{"story_id": "s1", "title": "Test"}],
            "run_dir": "",
            "run_id": "no_dir",
            "niche_id": "ai_creators",
        }
        stage = DownloadTopVideos()
        result = stage.execute(context)
        assert result["clip_index"]["videos_total"] == 0

    @patch("genlab_core.media.download_top_videos.download_videos_for_stories")
    def test_run_delegates_to_execute(self, mock_download, tmp_path):
        """run() delegates to execute()."""
        mock_download.return_value = {}
        context = {
            "stories": [{"story_id": "s1", "title": "Test"}],
            "run_dir": str(tmp_path),
            "run_id": "run_delegate",
            "niche_id": "ai_creators",
            "niche_config": {"pipeline": {"max_items_per_run": 5}},
        }

        stage = DownloadTopVideos()
        result = stage.run(context)

        assert "clip_index" in result
        mock_download.assert_called_once()

    @patch("genlab_core.media.download_top_videos.download_videos_for_stories")
    def test_reads_max_items_from_config(self, mock_download, tmp_path):
        """Stage reads max_items_per_run from niche_config.pipeline."""
        mock_download.return_value = {}
        context = {
            "stories": [{"story_id": f"s{i}", "title": f"T{i}"} for i in range(20)],
            "run_dir": str(tmp_path),
            "run_id": "cfg_test",
            "niche_id": "gaming",
            "niche_config": {"pipeline": {"max_items_per_run": 3}},
        }

        stage = DownloadTopVideos()
        stage.execute(context)

        # Verify max_stories=3 was passed
        call_kwargs = mock_download.call_args
        assert call_kwargs[1]["max_stories"] == 3 or call_kwargs.kwargs.get("max_stories") == 3


# ---------------------------------------------------------------------------
# Clip index JSON compatibility with compose_blueprints
# ---------------------------------------------------------------------------


class TestClipIndexComposeCompat:
    """Verify clip_index.json format is compatible with compose_blueprints.py."""

    def test_successful_entry_has_required_fields(self):
        """Each clip entry must have story_id, success, clip_path, source_url."""
        entries = {
            "sid_1": {
                "story_id": "sid_1",
                "success": True,
                "clip_path": "/path/to/clip.mp4",
                "source_url": "https://youtube.com/watch?v=x",
                "backend": "youtube",
                "duration_seconds": 60.0,
                "error": "",
            },
        }
        index = build_clip_index("run_compat", entries)
        clip = index["clips"]["sid_1"]

        # These are the fields compose_blueprints.load_verified_video_story_ids checks
        assert "story_id" in clip
        assert "success" in clip
        assert "clip_path" in clip
        assert "source_url" in clip
        assert clip["success"] is True
        assert clip["story_id"] == "sid_1"

    def test_index_has_clips_dict(self):
        """Top-level must have 'clips' as a dict (not list)."""
        index = build_clip_index("r1", {"a": {"story_id": "a", "success": True}})
        assert isinstance(index["clips"], dict)
