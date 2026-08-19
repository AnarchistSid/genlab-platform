"""NARR-08 (2026-08-19) — writer-side render-duration resolution.

Retires the 30s baseline from `e1f508e9` as load-bearing.

Why the obvious fix would have been wrong: ffprobing the downloaded clip is
the natural fallback, and on the real 2026-08-19 story_0 it yields **356.6s**
— the full source video — because the renderer trims to a highlight window
before anything is published. A 356s budget is far worse than the 30s default
it replaces. The resolution therefore has to model the RENDERER, not the file
on disk.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from genlab_core.media.ffmpeg import get_ffmpeg_binary
from genlab_core.strategies.base_writing import BaseWritingStrategy


class _Strategy(BaseWritingStrategy):
    def _model_route_key(self) -> str:  # pragma: no cover - not exercised
        return "test"


_REPO = Path(__file__).resolve().parents[3]


def _clip(path: Path, seconds: float) -> Path:
    binary = get_ffmpeg_binary()
    if not binary:
        pytest.skip("ffmpeg not available")
    subprocess.run(
        [
            binary, "-y",
            "-f", "lavfi", "-i", f"testsrc=size=64x64:rate=10:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        capture_output=True, check=True, timeout=90,
    )
    return path


class TestRendererTrimWindowWins:
    def test_story_0_shape_resolves_to_the_render_window_not_the_clip(self):
        """The exact 2026-08-19 story_0 inputs.

        356.6s source clip, no duration metadata on the story, BB's
        ``highlight_moment.window_seconds: 16``. Must resolve 16 — not 30
        (the old default) and not 356.6 (the file on disk).
        """
        s = _Strategy("ai_creators", _REPO / "BlackboxBrief")
        story = {"story_id": "03348d8f9e0e30d0"}
        clip_index = {
            "clips": {
                "03348d8f9e0e30d0": {
                    "clip_path": "/nonexistent.mp4",
                    "duration_seconds": 356.588844,
                }
            }
        }

        resolved = s._resolve_render_duration_seconds(story, clip_index)
        assert resolved == 16.0
        assert resolved != 30.0, "the e1f508e9 baseline must no longer be reached"
        assert resolved < 356.0, "must not size to the untrimmed source clip"

    def test_clip_shorter_than_window_clamps_to_the_clip(self):
        """You cannot trim a 10s clip to a 16s window."""
        s = _Strategy("ai_creators", _REPO / "BlackboxBrief")
        resolved = s._resolve_render_duration_seconds(
            {"story_id": "x"},
            {"clips": {"x": {"duration_seconds": 10.0}}},
        )
        assert resolved == 10.0


class TestFfprobeFallbackWhenNoTrimConfigured:
    def test_real_clip_file_is_probed_when_no_window_config(
        self, tmp_path: Path
    ):
        """Directed case: story with no duration metadata + a real clip file.

        With no ``highlight_moment`` window to model, the renderer emits the
        clip as-is, so the budget derives from ffprobe of the actual file —
        not the 30s baseline.
        """
        niche_root = tmp_path / "niche"
        (niche_root / "config").mkdir(parents=True)
        (niche_root / "config" / "visuals.yaml").write_text(
            yaml.safe_dump({"intelligent_transform": {"enabled": False}})
        )

        clip = _clip(tmp_path / "clip.mp4", 7.0)
        s = _Strategy("testniche", niche_root)

        resolved = s._resolve_render_duration_seconds(
            {"story_id": "s1", "local_path": str(clip)}, None
        )
        assert resolved is not None
        assert 6.5 < resolved < 7.5, f"expected ~7s from ffprobe, got {resolved}"
        assert resolved != 30.0

    def test_recorded_duration_preferred_over_spawning_ffprobe(
        self, tmp_path: Path
    ):
        """DownloadTopVideos already recorded the duration; reuse it.

        Same number, no subprocess. Pointing clip_path at a nonexistent file
        proves the recorded value is what gets used.
        """
        niche_root = tmp_path / "niche"
        (niche_root / "config").mkdir(parents=True)
        (niche_root / "config" / "visuals.yaml").write_text(
            yaml.safe_dump({"intelligent_transform": {"enabled": False}})
        )
        s = _Strategy("testniche", niche_root)

        resolved = s._resolve_render_duration_seconds(
            {"story_id": "s1"},
            {"clips": {"s1": {"clip_path": "/nope.mp4", "duration_seconds": 42.5}}},
        )
        assert resolved == 42.5


class TestUnresolvableStillReturnsNone:
    def test_nothing_available_returns_none(self, tmp_path: Path):
        """Caller keeps the 30s fallback for this case — but it now WARNs,
        and the mix-time guard degrades on overrun regardless.
        """
        niche_root = tmp_path / "niche"
        (niche_root / "config").mkdir(parents=True)
        (niche_root / "config" / "visuals.yaml").write_text(
            yaml.safe_dump({"intelligent_transform": {"enabled": False}})
        )
        s = _Strategy("testniche", niche_root)

        assert s._resolve_render_duration_seconds({"story_id": "s1"}, None) is None
