"""Tests for genlab_core.pipeline.stages.video_gate.VideoGate."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.pipeline.stages.video_gate import VideoGate


def _make_context(stories, clip_index=None):
    """Build a minimal pipeline context dict."""
    ctx = {"stories": stories}
    if clip_index is not None:
        ctx["clip_index"] = clip_index
    return ctx


def _make_clip_index(clips_map):
    """Build a clip_index dict from a mapping of story_id -> clip_entry."""
    return {
        "run_id": "test-run-001",
        "videos_total": len(clips_map),
        "videos_downloaded": sum(1 for c in clips_map.values() if c.get("success")),
        "videos_failed": sum(1 for c in clips_map.values() if not c.get("success")),
        "clips": clips_map,
    }


class TestVideoGate:
    """Tests for the VideoGate pipeline stage."""

    def test_video_gate_skips_story_without_clip(self):
        """Stories not present in clip_index should be marked _skip_llm=True."""
        stories = [
            {"story_id": "story-001", "title": "No clip available"},
            {"story_id": "story-002", "title": "Also missing"},
        ]
        clip_index = _make_clip_index({})

        gate = VideoGate()
        result = gate.execute(_make_context(stories, clip_index))

        assert result["stories"][0]["_skip_llm"] is True
        assert result["stories"][1]["_skip_llm"] is True
        assert result["run_stats"]["video_gate"]["skipped"] == 2
        assert result["run_stats"]["video_gate"]["passed"] == 0

    def test_video_gate_passes_story_with_successful_clip(self):
        """Stories with a successful clip entry should NOT be marked _skip_llm."""
        stories = [
            {"story_id": "story-001", "title": "Has a clip"},
        ]
        clip_index = _make_clip_index({
            "story-001": {
                "story_id": "story-001",
                "success": True,
                "clip_path": "/tmp/clips/story-001.mp4",
                "source_url": "https://youtube.com/watch?v=abc",
                "backend": "yt-dlp",
                "duration_seconds": 30.0,
                "error": "",
            },
        })

        # Mock Path so the file "exists" and is large enough
        mock_stat = MagicMock()
        mock_stat.st_size = 500 * 1024  # 500 KB

        with patch("genlab_core.pipeline.stages.video_gate.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.stat.return_value = mock_stat
            MockPath.return_value = mock_path_instance

            gate = VideoGate()
            result = gate.execute(_make_context(stories, clip_index))

        assert "_skip_llm" not in result["stories"][0]
        assert result["run_stats"]["video_gate"]["passed"] == 1
        assert result["run_stats"]["video_gate"]["skipped"] == 0

    def test_video_gate_rejects_tiny_clip(self):
        """Clips smaller than _MIN_CLIP_SIZE_BYTES should be rejected."""
        stories = [
            {"story_id": "story-001", "title": "Tiny clip"},
        ]
        clip_index = _make_clip_index({
            "story-001": {
                "story_id": "story-001",
                "success": True,
                "clip_path": "/tmp/clips/story-001.mp4",
                "source_url": "https://youtube.com/watch?v=abc",
                "backend": "yt-dlp",
                "duration_seconds": 2.0,
                "error": "",
            },
        })

        # Mock Path so the file exists but is too small
        mock_stat = MagicMock()
        mock_stat.st_size = 50 * 1024  # 50 KB — below 100 KB minimum

        with patch("genlab_core.pipeline.stages.video_gate.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.stat.return_value = mock_stat
            MockPath.return_value = mock_path_instance

            gate = VideoGate()
            result = gate.execute(_make_context(stories, clip_index))

        assert result["stories"][0]["_skip_llm"] is True
        assert result["run_stats"]["video_gate"]["skipped"] == 1
        assert result["run_stats"]["video_gate"]["passed"] == 0

    def test_video_gate_stats_in_context(self):
        """run_stats['video_gate'] should contain passed and skipped counts."""
        stories = [
            {"story_id": "story-ok", "title": "Good"},
            {"story_id": "story-bad", "title": "Bad"},
            {"story_id": "story-also-bad", "title": "Also Bad"},
        ]
        clip_index = _make_clip_index({
            "story-ok": {
                "story_id": "story-ok",
                "success": True,
                "clip_path": "/tmp/clips/ok.mp4",
                "source_url": "https://youtube.com/watch?v=ok",
                "backend": "yt-dlp",
                "duration_seconds": 25.0,
                "error": "",
            },
            "story-bad": {
                "story_id": "story-bad",
                "success": False,
                "clip_path": "",
                "source_url": "https://youtube.com/watch?v=bad",
                "backend": "yt-dlp",
                "duration_seconds": 0,
                "error": "download failed",
            },
            # story-also-bad not in clips at all
        })

        with patch("genlab_core.pipeline.stages.video_gate.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.stat.return_value = MagicMock(st_size=200 * 1024)
            MockPath.return_value = mock_path_instance

            gate = VideoGate()
            result = gate.execute(_make_context(stories, clip_index))

        stats = result["run_stats"]["video_gate"]
        assert stats["passed"] == 1
        assert stats["skipped"] == 2

    def test_video_gate_handles_empty_stories(self):
        """Empty stories list should produce zero counts and not raise."""
        clip_index = _make_clip_index({})

        gate = VideoGate()
        result = gate.execute(_make_context([], clip_index))

        assert result["run_stats"]["video_gate"]["passed"] == 0
        assert result["run_stats"]["video_gate"]["skipped"] == 0

    def test_video_gate_handles_missing_clip_index(self):
        """If clip_index is missing from context, all stories should be skipped."""
        stories = [
            {"story_id": "story-001", "title": "Orphaned story"},
        ]

        gate = VideoGate()
        # No clip_index in context at all
        result = gate.execute(_make_context(stories))

        assert result["stories"][0]["_skip_llm"] is True
        assert result["run_stats"]["video_gate"]["skipped"] == 1
        assert result["run_stats"]["video_gate"]["passed"] == 0

    def test_video_gate_clip_path_not_on_disk(self):
        """If clip_path is set but file doesn't exist on disk, story still passes.

        The file-exists check only rejects clips that exist but are too small.
        A missing file might be on a different machine or will be mounted later.
        """
        stories = [
            {"story_id": "story-001", "title": "Remote clip"},
        ]
        clip_index = _make_clip_index({
            "story-001": {
                "story_id": "story-001",
                "success": True,
                "clip_path": "/remote/clips/story-001.mp4",
                "source_url": "https://youtube.com/watch?v=abc",
                "backend": "yt-dlp",
                "duration_seconds": 20.0,
                "error": "",
            },
        })

        with patch("genlab_core.pipeline.stages.video_gate.Path") as MockPath:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False  # File not on disk
            MockPath.return_value = mock_path_instance

            gate = VideoGate()
            result = gate.execute(_make_context(stories, clip_index))

        # success=True + clip_path set + file not on disk => still passes
        assert "_skip_llm" not in result["stories"][0]
        assert result["run_stats"]["video_gate"]["passed"] == 1

    def test_video_gate_preserves_existing_run_stats(self):
        """VideoGate should not overwrite pre-existing run_stats keys."""
        context = _make_context([], _make_clip_index({}))
        context["run_stats"] = {"express_lane": {"total": 5}}

        gate = VideoGate()
        result = gate.execute(context)

        assert result["run_stats"]["express_lane"] == {"total": 5}
        assert "video_gate" in result["run_stats"]
