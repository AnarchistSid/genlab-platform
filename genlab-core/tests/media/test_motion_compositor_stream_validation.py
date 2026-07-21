"""Pin tests for the 2026-07-21 pre-concat stream validation fix
(motion_compositor task #632).

Live prod evidence 2026-07-21: motion pipeline still failing with
`Task finished with error code: -22 (Invalid argument)` on both
primary + fallback intro attempts. Prior investigation
(docs/INVESTIGATION-motion-compositor-concat-eninval-2026-07-09.md)
ruled out static causes; content-dependent stream degradation was
the outstanding hypothesis.

Fix: `_validate_segment_streams` runs ffprobe on each concat input
BEFORE ffmpeg concat, fails fast on missing streams / zero-duration
files. Prevents ~1s of wasted ffmpeg work per doomed segment AND
gives operator an actionable reason string.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from genlab_core.media.motion_compositor import _validate_segment_streams


def _make_probe_result(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestFileMissing:
    def test_ffprobe_error_returns_reason(self):
        """ffprobe rejects the file (parse error, corrupt container).
        Return a short reason string starting with 'ffprobe_failed'."""
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(1, "", "Invalid data found")
            result = _validate_segment_streams(Path("/tmp/nope.mp4"))
        assert result is not None
        assert result.startswith("ffprobe_failed")


class TestStreamContents:
    def test_valid_video_and_audio_returns_none(self):
        """Segment with video + audio + duration ≥ 0.5s is valid."""
        good = '{"streams":[{"codec_type":"video"},{"codec_type":"audio"}],"format":{"duration":"12.3"}}'
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(0, good, "")
            result = _validate_segment_streams(Path("/tmp/good.mp4"))
        assert result is None

    def test_missing_video_stream_flagged(self):
        """Audio-only file — concat filter's v=1 output needs video."""
        audio_only = '{"streams":[{"codec_type":"audio"}],"format":{"duration":"5.0"}}'
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(0, audio_only, "")
            result = _validate_segment_streams(Path("/tmp/audio.mp4"))
        assert result == "no_video_stream"

    def test_missing_audio_stream_flagged(self):
        """Video-only file — concat filter's a=1 output needs audio.
        This is the primary hypothesis for the -22 EINVAL failure."""
        video_only = '{"streams":[{"codec_type":"video"}],"format":{"duration":"5.0"}}'
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(0, video_only, "")
            result = _validate_segment_streams(Path("/tmp/video.mp4"))
        assert result == "no_audio_stream"

    def test_zero_duration_flagged(self):
        """Streams present but 0-duration — the music_mood audio
        replacement failure mode."""
        empty = '{"streams":[{"codec_type":"video"},{"codec_type":"audio"}],"format":{"duration":"0.0"}}'
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(0, empty, "")
            result = _validate_segment_streams(Path("/tmp/empty.mp4"))
        assert result is not None
        assert result.startswith("duration_too_short")


class TestFailOpen:
    """Validation must NEVER block concat on tooling failures — those
    are our-side issues (ffprobe missing / crash). Concat gets a
    chance to try and can surface its own error."""

    def test_ffprobe_not_installed_returns_none(self):
        with patch(
            "genlab_core.media.motion_compositor.subprocess.run",
            side_effect=FileNotFoundError("no ffprobe"),
        ):
            result = _validate_segment_streams(Path("/tmp/x.mp4"))
        assert result is None

    def test_ffprobe_timeout_returns_none(self):
        with patch(
            "genlab_core.media.motion_compositor.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=15),
        ):
            result = _validate_segment_streams(Path("/tmp/x.mp4"))
        assert result is None

    def test_malformed_json_returns_reason(self):
        """ffprobe returned success but stdout isn't valid JSON —
        deterministic failure signal (not fail-open) because we can't
        tell what happened."""
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(0, "not json{}", "")
            result = _validate_segment_streams(Path("/tmp/x.mp4"))
        assert result == "ffprobe_json_parse"

    def test_empty_stdout_returns_none(self):
        """Empty stdout usually means subprocess mock in tests OR a
        weird ffprobe state — fail-open so concat gets a chance."""
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(0, "", "")
            result = _validate_segment_streams(Path("/tmp/x.mp4"))
        assert result is None


class TestEdgeCases:
    def test_missing_duration_field_defaults_to_zero(self):
        """If format.duration is absent, treat as 0 (fail fast). Some
        streaming exports lack duration until the file is fully
        remuxed."""
        no_dur = '{"streams":[{"codec_type":"video"},{"codec_type":"audio"}],"format":{}}'
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(0, no_dur, "")
            result = _validate_segment_streams(Path("/tmp/nod.mp4"))
        assert result is not None
        assert "duration_too_short" in result

    def test_non_numeric_duration_defaults_to_zero(self):
        weird = '{"streams":[{"codec_type":"video"},{"codec_type":"audio"}],"format":{"duration":"N/A"}}'
        with patch("genlab_core.media.motion_compositor.subprocess.run") as m:
            m.return_value = _make_probe_result(0, weird, "")
            result = _validate_segment_streams(Path("/tmp/nod.mp4"))
        assert result is not None
        assert "duration_too_short" in result
