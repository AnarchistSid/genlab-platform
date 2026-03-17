"""Tests for genlab_core.media.audio_probe.

Covers has_meaningful_audio() (silence gate) and extract_audio_track()
(WAV extraction for Whisper input). All tests are mock-based — no real
FFmpeg/ffprobe execution required.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from genlab_core.media.audio_probe import (
    extract_audio_track,
    has_meaningful_audio,
)

# ── has_meaningful_audio ─────────────────────────────────────────────────────


class TestHasMeaningfulAudio:
    @patch("genlab_core.media.audio_probe._probe_audio_stream", return_value=None)
    def test_no_audio_stream_returns_false(self, mock_probe):
        """Clip with no audio stream at all should return False."""
        result = has_meaningful_audio(Path("/fake/clip.mp4"))
        assert result is False
        mock_probe.assert_called_once()

    @patch("genlab_core.media.audio_probe._measure_volume", return_value=-91.0)
    @patch(
        "genlab_core.media.audio_probe._probe_audio_stream",
        return_value={"codec_name": "aac", "channels": "2"},
    )
    def test_silent_audio_returns_false(self, mock_probe, mock_volume):
        """Audio stream exists but volume is below threshold — effectively silent."""
        result = has_meaningful_audio(Path("/fake/clip.mp4"))
        assert result is False

    @patch("genlab_core.media.audio_probe._measure_volume", return_value=-20.0)
    @patch(
        "genlab_core.media.audio_probe._probe_audio_stream",
        return_value={"codec_name": "aac", "channels": "2"},
    )
    def test_loud_audio_returns_true(self, mock_probe, mock_volume):
        """Audio stream exists and volume is well above threshold — has speech."""
        result = has_meaningful_audio(Path("/fake/clip.mp4"))
        assert result is True

    @patch("genlab_core.media.audio_probe._measure_volume", return_value=-35.0)
    @patch(
        "genlab_core.media.audio_probe._probe_audio_stream",
        return_value={"codec_name": "aac", "channels": "2"},
    )
    def test_custom_threshold(self, mock_probe, mock_volume):
        """Custom threshold should correctly flip the result."""
        # -35.0 > -40 (default) → True
        assert has_meaningful_audio(Path("/fake/clip.mp4")) is True
        # -35.0 > -30 → False (volume is below stricter threshold)
        assert has_meaningful_audio(Path("/fake/clip.mp4"), silence_threshold_db=-30) is False


# ── extract_audio_track ──────────────────────────────────────────────────────


class TestExtractAudioTrack:
    @patch("genlab_core.media.audio_probe.get_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("genlab_core.media.audio_probe.subprocess")
    def test_successful_extraction(self, mock_subprocess, mock_ffmpeg, tmp_path):
        """Successful FFmpeg run returns the output path."""
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        clip = tmp_path / "clip.mp4"
        out = tmp_path / "audio.wav"
        result = extract_audio_track(clip, out)
        assert result == out
        mock_subprocess.run.assert_called_once()
        # Verify key FFmpeg args are in the call
        call_args = mock_subprocess.run.call_args[0][0]
        assert "-ar" in call_args
        assert "16000" in call_args
        assert "-ac" in call_args
        assert "1" in call_args
        assert "pcm_s16le" in call_args

    @patch("genlab_core.media.audio_probe.get_ffmpeg_binary", return_value="/usr/bin/ffmpeg")
    @patch("genlab_core.media.audio_probe.subprocess")
    def test_failed_extraction_returns_none(self, mock_subprocess, mock_ffmpeg, tmp_path):
        """Non-zero FFmpeg exit returns None."""
        mock_subprocess.run.return_value = MagicMock(returncode=1, stderr=b"error")
        clip = tmp_path / "clip.mp4"
        out = tmp_path / "audio.wav"
        result = extract_audio_track(clip, out)
        assert result is None

    @patch(
        "genlab_core.media.audio_probe.get_ffmpeg_binary",
        side_effect=RuntimeError("FFmpeg not found"),
    )
    def test_no_ffmpeg_returns_none(self, mock_ffmpeg, tmp_path):
        """Missing FFmpeg binary returns None instead of raising."""
        clip = tmp_path / "clip.mp4"
        out = tmp_path / "audio.wav"
        result = extract_audio_track(clip, out)
        assert result is None
