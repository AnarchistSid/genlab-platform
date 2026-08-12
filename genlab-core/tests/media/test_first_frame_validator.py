"""Pin the first-frame brightness validator.

Contract:

  * `check_first_frame_brightness(video_path)` returns FirstFrameQuality
  * Bright frame (YAVG >= 60) -> passed=True, reason="ok"
  * Dark frame (YAVG < 60) -> passed=False, reason="dark"
  * File missing -> passed=True (fail-open), reason="measurement_failed:file_not_found"
  * ffmpeg exit non-zero -> passed=True, reason="measurement_failed:ffmpeg_exit_N"
  * ffmpeg timeout -> passed=True, reason="measurement_failed:timeout"
  * ffmpeg raises -> passed=True, reason="measurement_failed:{ExceptionType}"
  * ffmpeg output missing YAVG -> passed=True, reason="measurement_failed:no_yavg_in_stderr"

  * `log_first_frame_signal(...)` returns FirstFrameQuality and
    emits WARN log when passed=False with a measured YAVG.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.first_frame_validator import (
    FirstFrameQuality,
    _DARK_YAVG_THRESHOLD,
    _parse_yavg,
    check_first_frame_brightness,
    log_first_frame_signal,
)


# ffmpeg signalstats emits these to stderr for a single frame. Realistic
# stderr sample so parser tests reflect actual ffmpeg output shape.
def _stderr_with_yavg(yavg: float) -> str:
    return (
        "ffmpeg version 6.0.1 Copyright (c) 2000-2023\n"
        "[Parsed_signalstats_0 @ 0x7f9] using cpu capabilities\n"
        "Input #0, mov,mp4,m4a: '/tmp/vid.mp4':\n"
        "  Duration: 00:00:15.00, bitrate: 4500 kb/s\n"
        "frame:0    pts:0    pts_time:0\n"
        "lavfi.signalstats.YMIN=16\n"
        "lavfi.signalstats.YLOW=16\n"
        f"lavfi.signalstats.YAVG={yavg:.3f}\n"
        "lavfi.signalstats.YHIGH=245\n"
        "lavfi.signalstats.YMAX=255\n"
    )


@pytest.fixture(autouse=True)
def _clear_ffmpeg_lru_cache():
    """`get_ffmpeg_binary` uses @lru_cache; a test setting
    FFMPEG_BINARY=/fake/ffmpeg would poison the cache for later
    tests in other files. Clear before + after each test in this
    module so the cache never survives a test boundary."""
    from genlab_core.media.ffmpeg import get_ffmpeg_binary
    get_ffmpeg_binary.cache_clear()
    yield
    get_ffmpeg_binary.cache_clear()


@pytest.fixture
def tmp_video(tmp_path):
    """A fake video file (empty). check_first_frame_brightness only
    calls .exists() on it — actual bytes never read (subprocess is
    mocked in tests)."""
    p = tmp_path / "test.mp4"
    p.write_bytes(b"fake")
    return p


class TestParseYavg:
    def test_extracts_valid_yavg(self):
        assert _parse_yavg(_stderr_with_yavg(105.324)) == pytest.approx(105.324)

    def test_missing_yavg_returns_none(self):
        assert _parse_yavg("ffmpeg version 6.0.1") is None

    def test_empty_stderr(self):
        assert _parse_yavg("") is None

    def test_zero_yavg(self):
        assert _parse_yavg(_stderr_with_yavg(0.0)) == 0.0


class TestBrightFrame:
    def test_bright_yavg_passes(self, tmp_video, monkeypatch):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        proc = MagicMock(returncode=0, stderr=_stderr_with_yavg(120.0))
        with patch("subprocess.run", return_value=proc):
            result = check_first_frame_brightness(tmp_video)
        assert result.passed is True
        assert result.yavg == pytest.approx(120.0)
        assert result.reason == "ok"

    def test_threshold_boundary_passes(self, tmp_video, monkeypatch):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        proc = MagicMock(returncode=0, stderr=_stderr_with_yavg(_DARK_YAVG_THRESHOLD))
        with patch("subprocess.run", return_value=proc):
            result = check_first_frame_brightness(tmp_video)
        # Exactly at threshold -> pass (>= check)
        assert result.passed is True
        assert result.reason == "ok"


class TestDarkFrame:
    def test_dark_yavg_fails(self, tmp_video, monkeypatch):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        proc = MagicMock(returncode=0, stderr=_stderr_with_yavg(30.0))
        with patch("subprocess.run", return_value=proc):
            result = check_first_frame_brightness(tmp_video)
        assert result.passed is False
        assert result.yavg == pytest.approx(30.0)
        assert result.reason == "dark"

    def test_black_frame_fails(self, tmp_video, monkeypatch):
        """Solid #000 gives YAVG ≈ 16 (BT.709 floor)."""
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        proc = MagicMock(returncode=0, stderr=_stderr_with_yavg(16.0))
        with patch("subprocess.run", return_value=proc):
            result = check_first_frame_brightness(tmp_video)
        assert result.passed is False
        assert result.yavg == pytest.approx(16.0)


class TestFailOpen:
    def test_missing_file_returns_pass(self, tmp_path):
        result = check_first_frame_brightness(tmp_path / "nonexistent.mp4")
        assert result.passed is True
        assert result.yavg is None
        assert result.reason == "measurement_failed:file_not_found"

    def test_ffmpeg_exit_nonzero_returns_pass(self, tmp_video, monkeypatch):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        proc = MagicMock(returncode=1, stderr="ffmpeg error")
        with patch("subprocess.run", return_value=proc):
            result = check_first_frame_brightness(tmp_video)
        assert result.passed is True
        assert result.yavg is None
        assert "ffmpeg_exit_1" in result.reason

    def test_ffmpeg_timeout_returns_pass(self, tmp_video, monkeypatch):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=15),
        ):
            result = check_first_frame_brightness(tmp_video)
        assert result.passed is True
        assert result.reason == "measurement_failed:timeout"

    def test_ffmpeg_generic_exception_returns_pass(self, tmp_video, monkeypatch):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        with patch("subprocess.run", side_effect=OSError("simulated")):
            result = check_first_frame_brightness(tmp_video)
        assert result.passed is True
        assert "OSError" in result.reason

    def test_no_yavg_in_stderr_returns_pass(self, tmp_video, monkeypatch):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        proc = MagicMock(returncode=0, stderr="ffmpeg version 6.0.1")
        with patch("subprocess.run", return_value=proc):
            result = check_first_frame_brightness(tmp_video)
        assert result.passed is True
        assert result.reason == "measurement_failed:no_yavg_in_stderr"

    def test_ffmpeg_binary_missing_returns_pass(self, tmp_video, monkeypatch):
        # Force get_ffmpeg_binary to raise
        import genlab_core.media.first_frame_validator as mod

        def _boom() -> str:
            raise RuntimeError("FFmpeg not found")

        # Patch at the module the primitive imports (deferred inside
        # function, so patch what it looks up)
        with patch("genlab_core.media.ffmpeg.get_ffmpeg_binary", side_effect=_boom):
            result = check_first_frame_brightness(tmp_video)
        assert result.passed is True
        assert "ffmpeg_binary_missing" in result.reason


class TestLogSignal:
    def test_dark_frame_emits_warn(self, tmp_video, monkeypatch, caplog):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        proc = MagicMock(returncode=0, stderr=_stderr_with_yavg(25.0))
        with patch("subprocess.run", return_value=proc), caplog.at_level(logging.WARNING):
            result = log_first_frame_signal(tmp_video, niche_id="anime")
        assert result.passed is False
        msg = next(r.message for r in caplog.records if "DARK_FIRST_FRAME" in r.message)
        assert "niche=anime" in msg
        assert "platform=youtube" in msg
        assert "yavg=25.0" in msg
        assert "threshold=60" in msg

    def test_bright_frame_no_warn(self, tmp_video, monkeypatch, caplog):
        monkeypatch.setenv("FFMPEG_BINARY", "/fake/ffmpeg")
        proc = MagicMock(returncode=0, stderr=_stderr_with_yavg(140.0))
        with patch("subprocess.run", return_value=proc), caplog.at_level(logging.WARNING):
            log_first_frame_signal(tmp_video, niche_id="sports")
        assert not any("DARK_FIRST_FRAME" in r.message for r in caplog.records)

    def test_measurement_failed_no_warn(self, tmp_path, caplog):
        """When we can't measure (yavg=None), don't emit WARN — the
        WARN specifically means 'we measured a dark frame', not
        'validator broke'."""
        with caplog.at_level(logging.WARNING):
            log_first_frame_signal(tmp_path / "missing.mp4", niche_id="sports")
        assert not any("DARK_FIRST_FRAME" in r.message for r in caplog.records)


class TestYouTubeWire:
    def test_youtube_publish_source_has_wire(self):
        """Structural pin: platforms/youtube.py wires the validator
        under GENLAB_FIRST_FRAME_VALIDATOR_ENABLED. Guards against
        the wire being deleted."""
        import pathlib

        yt_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "platforms"
            / "youtube.py"
        )
        src = yt_path.read_text()
        assert "GENLAB_FIRST_FRAME_VALIDATOR_ENABLED" in src
        assert "log_first_frame_signal" in src
