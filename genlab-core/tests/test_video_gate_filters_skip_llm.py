"""Regression pin: VideoGate drops clipless stories from active pipeline.

RENDER #6 fix (2026-06-13). Pre-fix, VideoGate set `_skip_llm=True` on
stories without a downloaded clip but left them in `context["stories"]`.
Every downstream stage then iterated over them:

  - WritingStrategy correctly skipped (top-of-loop guard)
  - HookStrategy generated hooks anyway (no guard) → hook field populated
  - QCGates ran on (hook=present, caption=empty) → "Missing required
    field: caption/body" → fail counted toward QC pass rate
  - ViralityScoring, VisualRender, etc. all wasted cycles on dead candidates

For sports today: 5 stories fetched, 2 failed VideoGate, those 2 surfaced as
QC failures → 60% QC pass rate. The 60% was structural noise, not quality
signal. After this fix, VideoGate filters skipped stories out of
context["stories"] so downstream stages only see real candidates.

This test pins:
  1. Clipless stories are removed from context["stories"]
  2. Stories with valid clips survive
  3. The skipped count + run_stats are still populated (telemetry preserved)
  4. No-skip case is a byte-stable no-op (no list rebuild churn)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.pipeline.stages.video_gate import VideoGate


def _story(story_id: str, title: str = "X") -> dict:
    return {"story_id": story_id, "title": title}


def _ctx(stories: list, clips: dict | None = None) -> dict:
    return {
        "stories": stories,
        "clip_index": {"clips": clips or {}},
    }


@pytest.fixture
def real_clip(tmp_path: Path) -> Path:
    """Create a real ~5MB tmp file so VideoGate's Path.exists() + stat().st_size
    checks pass. Avoids the brittle Path.exists mocking we hit on first attempt."""
    clip = tmp_path / "fake_video.mp4"
    clip.write_bytes(b"\x00" * (5 * 1024 * 1024))  # 5 MB
    return clip


class TestVideoGateFiltersSkipLLM:
    def test_clipless_stories_removed_from_active_pipeline(self, real_clip):
        stage = VideoGate()
        # Story A has a valid clip (real file); B does not.
        clips = {
            "story_a": {"success": True, "clip_path": str(real_clip)},
        }
        ctx = _ctx(
            stories=[
                _story("story_a", "Has clip"),
                _story("story_b", "No clip"),
            ],
            clips=clips,
        )
        # Mock the bitrate probe so the clip passes quality gates
        with patch(
            "genlab_core.pipeline.stages.video_gate._probe_video_quality",
            return_value={
                "bitrate_bps": 5_000_000,
                "duration_sec": 30,
                "bytes_per_sec": 175_000,
            },
        ):
            result = stage.execute(ctx)

        remaining_ids = [s["story_id"] for s in result["stories"]]
        assert "story_a" in remaining_ids, "Valid-clip story must survive"
        assert "story_b" not in remaining_ids, "Clipless story must be filtered out"

    def test_telemetry_preserved_after_filter(self):
        stage = VideoGate()
        ctx = _ctx(stories=[_story("x", "No clip")], clips={})
        result = stage.execute(ctx)
        # Pipeline now empty (the only story was clipless)
        assert result["stories"] == []
        # But the run_stats reflect what happened pre-filter
        stats = result["run_stats"]["video_gate"]
        assert stats["passed"] == 0
        assert stats["skipped"] == 1

    def test_all_stories_skipped_results_in_empty_list(self):
        stage = VideoGate()
        ctx = _ctx(
            stories=[_story("a"), _story("b"), _story("c")],
            clips={},  # nothing has a clip
        )
        result = stage.execute(ctx)
        assert result["stories"] == []
        assert result["run_stats"]["video_gate"]["skipped"] == 3

    def test_no_skips_means_no_list_rebuild(self, real_clip, tmp_path):
        """When every story has a valid clip, the list reference shouldn't
        be rebuilt (cheap no-op + preserves identity for callers that rely
        on it for object-identity assertions)."""
        clip_b = tmp_path / "clip_b.mp4"
        clip_b.write_bytes(b"\x00" * (5 * 1024 * 1024))
        stage = VideoGate()
        clips = {
            "a": {"success": True, "clip_path": str(real_clip)},
            "b": {"success": True, "clip_path": str(clip_b)},
        }
        original_stories = [_story("a"), _story("b")]
        ctx = _ctx(stories=original_stories, clips=clips)

        with patch(
            "genlab_core.pipeline.stages.video_gate._probe_video_quality",
            return_value={
                "bitrate_bps": 5_000_000,
                "duration_sec": 30,
                "bytes_per_sec": 175_000,
            },
        ):
            result = stage.execute(ctx)

        # Same list object — no rebuild churn
        assert result["stories"] is original_stories

    def test_empty_input_returns_empty_output(self):
        stage = VideoGate()
        ctx = _ctx(stories=[], clips={})
        result = stage.execute(ctx)
        assert result["stories"] == []
        assert result["run_stats"]["video_gate"] == {"passed": 0, "skipped": 0}
