"""Pin the highlight detector (PR 12).

Covers:
  1. HighlightWindow model (duration, is_valid)
  2. use_first_frame: window clamped to video duration
  3. silencedetect stderr parsing
  4. Longest non-silent stretch computation
  5. Scene-change stderr parsing
  6. Audio peak detection: success + None fallback paths
  7. Motion peak detection: success + None fallback paths
  8. LLM pick: stub returns None
  9. Dispatcher: known method routing + fallback to first_frame
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.media.highlight_detector import (
    HighlightWindow,
    _longest_non_silent,
    _parse_scene_changes,
    _parse_silence_windows,
    detect_audio_peak,
    detect_highlight,
    detect_llm_pick,
    detect_motion_peak,
    use_first_frame,
)


class TestHighlightWindow:
    def test_valid_window(self) -> None:
        w = HighlightWindow(start_s=5.0, end_s=13.0)
        assert w.duration() == 8.0
        assert w.is_valid() is True

    def test_zero_duration_invalid(self) -> None:
        w = HighlightWindow(start_s=5.0, end_s=5.0)
        assert w.duration() == 0.0
        assert w.is_valid() is False

    def test_negative_duration_invalid(self) -> None:
        w = HighlightWindow(start_s=10.0, end_s=5.0)
        assert w.duration() == 0.0
        assert w.is_valid() is False


class TestUseFirstFrame:
    def test_normal_case(self) -> None:
        w = use_first_frame(video_duration_s=20.0, window_s=8.0)
        assert w.start_s == 0.0
        assert w.end_s == 8.0

    def test_short_video_clamps_end(self) -> None:
        """Video shorter than window → end clamps to duration."""
        w = use_first_frame(video_duration_s=4.0, window_s=8.0)
        assert w.start_s == 0.0
        assert w.end_s == 4.0

    def test_still_valid_when_shorter(self) -> None:
        w = use_first_frame(video_duration_s=4.0, window_s=8.0)
        assert w.is_valid()


class TestParseSilence:
    def test_pairs_start_and_end(self) -> None:
        stderr = """
        [silencedetect @ 0x1] silence_start: 0
        [silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 2.5
        [silencedetect @ 0x1] silence_start: 5.0
        [silencedetect @ 0x1] silence_end: 6.0 | silence_duration: 1.0
        """
        result = _parse_silence_windows(stderr)
        assert result == [(0.0, 2.5), (5.0, 6.0)]

    def test_empty_stderr(self) -> None:
        assert _parse_silence_windows("") == []

    def test_drops_unpaired_start(self) -> None:
        """A silence_start with no matching end (stream ended in silence)
        drops silently."""
        stderr = """
        [silencedetect @ 0x1] silence_start: 0
        [silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 2.5
        [silencedetect @ 0x1] silence_start: 10.0
        """
        result = _parse_silence_windows(stderr)
        # Only 1 complete pair
        assert result == [(0.0, 2.5)]


class TestLongestNonSilent:
    def test_no_silence(self) -> None:
        result = _longest_non_silent([], 20.0)
        assert result == (0.0, 20.0)

    def test_leading_silence(self) -> None:
        result = _longest_non_silent([(0.0, 5.0)], 20.0)
        # Non-silent: (5.0, 20.0) — length 15
        assert result == (5.0, 20.0)

    def test_multiple_silences_picks_longest(self) -> None:
        silences = [(0.0, 3.0), (5.0, 6.0), (15.0, 17.0)]
        # Non-silent: (3, 5) len 2, (6, 15) len 9, (17, 20) len 3
        # Longest is (6, 15).
        result = _longest_non_silent(silences, 20.0)
        assert result == (6.0, 15.0)

    def test_trailing_silence_ignored(self) -> None:
        silences = [(15.0, 20.0)]
        # Non-silent: (0, 15) — length 15
        result = _longest_non_silent(silences, 20.0)
        assert result == (0.0, 15.0)


class TestParseSceneChanges:
    def test_basic(self) -> None:
        stderr = """
        [Parsed_showinfo_1 @ 0x1] n:0 pts:0 pts_time:5.5 pos:100 fmt:yuv420p sar:1/1 s:1920x1080 iw:1920 ih:1080 lavfi.scene:0.45
        [Parsed_showinfo_1 @ 0x1] n:1 pts:100 pts_time:12.3 pos:200 fmt:yuv420p sar:1/1 s:1920x1080 iw:1920 ih:1080 scene:0.6
        """
        result = _parse_scene_changes(stderr)
        # Regex works line-by-line — should match both
        assert len(result) >= 1
        # First scene at 5.5s, score 0.45
        assert any(abs(ts - 5.5) < 0.01 for ts, _ in result)


class TestDetectAudioPeak:
    def test_missing_source(self, tmp_path: Path) -> None:
        result = detect_audio_peak(tmp_path / "no.mp4", video_duration_s=20.0, window_s=8.0)
        assert result is None

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_success_centers_on_longest_non_silent(
        self, mock_binary, mock_run, tmp_path: Path
    ) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        mock_binary.return_value = "/usr/bin/ffmpeg"

        # Silences at start (0-3s) and (15-20s); non-silent (3-15) is
        # longest (12s wide). Center = 9s. Window = 8s → (5, 13).
        stderr = """
        [silencedetect @ 0x1] silence_start: 0
        [silencedetect @ 0x1] silence_end: 3.0 | silence_duration: 3.0
        [silencedetect @ 0x1] silence_start: 15.0
        [silencedetect @ 0x1] silence_end: 20.0 | silence_duration: 5.0
        """
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=stderr
        )
        w = detect_audio_peak(source, video_duration_s=20.0, window_s=8.0)
        assert w is not None
        # Center at 9s, window 8 → 5.0 to 13.0
        assert w.start_s == pytest.approx(5.0)
        assert w.end_s == pytest.approx(13.0)

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_ffmpeg_timeout_returns_none(self, mock_binary, mock_run, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=60)
        assert detect_audio_peak(source, video_duration_s=20.0, window_s=8.0) is None

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_ffmpeg_unavailable(self, mock_binary, mock_run, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        mock_binary.side_effect = RuntimeError("no ffmpeg")
        result = detect_audio_peak(source, video_duration_s=20.0, window_s=8.0)
        assert result is None


class TestDetectMotionPeak:
    def test_missing_source(self, tmp_path: Path) -> None:
        assert detect_motion_peak(tmp_path / "no.mp4", video_duration_s=20.0, window_s=8.0) is None

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_success_centers_on_highest_scene(self, mock_binary, mock_run, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        mock_binary.return_value = "/usr/bin/ffmpeg"

        # Scene at 10s with score 0.5 (higher than 5.5s @ 0.45)
        stderr = """
        [Parsed_showinfo_1 @ 0x1] pts_time:5.5 pos:100 scene:0.45
        [Parsed_showinfo_1 @ 0x1] pts_time:10.0 pos:200 scene:0.5
        """
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=stderr
        )
        w = detect_motion_peak(source, video_duration_s=20.0, window_s=8.0)
        assert w is not None
        # Center at 10s → window (6, 14)
        assert w.start_s == pytest.approx(6.0)
        assert w.end_s == pytest.approx(14.0)

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_no_scenes_detected_returns_none(self, mock_binary, mock_run, tmp_path: Path) -> None:
        """No scene changes above threshold → signal too weak, None."""
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        assert detect_motion_peak(source, video_duration_s=20.0, window_s=8.0) is None


class TestDetectLLMPick:
    def test_returns_none_stub(self, tmp_path: Path) -> None:
        """LLM pick stub — always None until real impl lands."""
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        assert detect_llm_pick(source, video_duration_s=20.0, window_s=8.0) is None


class TestDetectHighlight:
    def test_first_frame_method(self, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        w = detect_highlight(source, "first_frame", video_duration_s=20.0, window_s=8.0)
        assert w == HighlightWindow(0.0, 8.0)

    def test_llm_pick_falls_back(self, tmp_path: Path) -> None:
        """LLM pick returns None → dispatcher falls back to first_frame."""
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        w = detect_highlight(source, "llm_pick", video_duration_s=20.0, window_s=8.0)
        assert w == HighlightWindow(0.0, 8.0)

    def test_unknown_method_falls_back(self, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        w = detect_highlight(source, "mystery_method", video_duration_s=20.0, window_s=8.0)
        assert w == HighlightWindow(0.0, 8.0)

    def test_fallback_disabled_returns_none(self, tmp_path: Path) -> None:
        """fallback_to_first_frame=False → None on failed method."""
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        w = detect_highlight(
            source,
            "llm_pick",
            video_duration_s=20.0,
            window_s=8.0,
            fallback_to_first_frame=False,
        )
        assert w is None

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_audio_peak_routed(self, mock_binary, mock_run, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"a" * 2000)
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="[silencedetect] silence_start: 0\n"
            "[silencedetect] silence_end: 3.0 | silence_duration: 3.0\n",
        )
        w = detect_highlight(source, "audio_peak", video_duration_s=20.0, window_s=8.0)
        assert w is not None
        # Non-silent stretch (3, 20), center=11.5, window (7.5, 15.5)
        assert w.duration() == pytest.approx(8.0)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
