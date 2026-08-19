"""NARR-08 (2026-08-19) — mix-time VO-overrun guard.

The A4 probe in ``GenerateAudio`` was supposed to catch this and structurally
cannot: it compares the VO against ``media["clip_duration_seconds"]`` and
returns early when no duration resolves — the SAME condition that makes the
writer fall back to its 30s baseline and oversize the script in the first
place. Its guard fails on exactly the inputs that need guarding.

This guard runs at the mix callsite where the trimmed reel is on disk, so the
reel length is measured rather than looked up. Tests use real media files for
that reason — mocking ffprobe would test the arithmetic and skip the thing
that was actually broken.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from genlab_core.media.ffmpeg import get_ffmpeg_binary
from genlab_core.media.transformation_orchestrator import (
    _VO_FIT_TOLERANCE_S,
    vo_overruns_reel,
)


def _tone(path: Path, seconds: float) -> Path:
    binary = get_ffmpeg_binary()
    if not binary:
        pytest.skip("ffmpeg not available")
    subprocess.run(
        [
            binary, "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:a", "libmp3lame", str(path),
        ],
        capture_output=True, check=True, timeout=60,
    )
    return path


class TestOverrunDegrades:
    def test_oversized_vo_degrades_and_marks_reason(self, tmp_path: Path):
        """The story_0 shape: a 30s VO against an 18.6s reel."""
        vo = _tone(tmp_path / "vo.mp3", 29.9)
        reel = _tone(tmp_path / "reel.mp3", 18.6)
        ctx: dict = {}

        assert vo_overruns_reel(vo, reel, ctx, "ai_creators") is True
        assert ctx["narration_degraded"] is True
        assert ctx["narration_degraded_reason"] == "vo_overrun"

    def test_fitting_vo_does_not_degrade(self, tmp_path: Path):
        vo = _tone(tmp_path / "vo.mp3", 14.0)
        reel = _tone(tmp_path / "reel.mp3", 16.0)
        ctx: dict = {}

        assert vo_overruns_reel(vo, reel, ctx, "ai_creators") is False
        assert ctx == {}, "a fitting VO must leave the context untouched"

    def test_overrun_within_tolerance_is_allowed(self, tmp_path: Path):
        """Just inside tolerance — the clipped part is TTS trailing silence,
        not words, so refusing the mix here would cost narration for nothing.
        """
        reel_s = 16.0
        vo = _tone(tmp_path / "vo.mp3", reel_s + (_VO_FIT_TOLERANCE_S / 2))
        reel = _tone(tmp_path / "reel.mp3", reel_s)
        ctx: dict = {}

        assert vo_overruns_reel(vo, reel, ctx, "ai_creators") is False
        assert ctx == {}


class TestGuardFailsOpen:
    def test_unprobeable_file_does_not_degrade(self, tmp_path: Path):
        """A probe outage must degrade to today's behaviour, not mute reels."""
        reel = _tone(tmp_path / "reel.mp3", 16.0)
        ctx: dict = {}

        assert vo_overruns_reel(tmp_path / "missing.mp3", reel, ctx, "x") is False
        assert ctx == {}


class TestIndependentOfMetadata:
    def test_guard_consults_no_story_metadata(self, tmp_path: Path):
        """The whole point: the guard takes only file paths.

        If it accepted a story/media dict it could inherit the A4 probe's
        failure mode, where absent metadata silently disables the check.
        """
        import inspect

        params = set(inspect.signature(vo_overruns_reel).parameters)
        assert params == {
            "narration_audio_path",
            "reel_path",
            "ctx",
            "niche_id",
        }, (
            "vo_overruns_reel must derive durations from files only — "
            "accepting story metadata would reintroduce A4's failure mode"
        )
