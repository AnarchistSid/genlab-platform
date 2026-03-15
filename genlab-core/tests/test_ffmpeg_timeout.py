"""Tests for run_ffmpeg() timeout-with-preset-fallback and _swap_preset() helper.

Sprint 65 Bug 2: FFmpeg 300s timeout on -preset slow.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.ffmpeg_utils import _swap_preset, run_ffmpeg


# ── _swap_preset ──────────────────────────────────────────────────


class TestSwapPreset:
    def test_replaces_existing(self):
        cmd = ["ffmpeg", "-y", "-i", "in.mp4", "-preset", "slow", "out.mp4"]
        result = _swap_preset(cmd, "fast")
        assert result == ["ffmpeg", "-y", "-i", "in.mp4", "-preset", "fast", "out.mp4"]

    def test_adds_when_missing(self):
        cmd = ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"]
        result = _swap_preset(cmd, "fast")
        assert result == ["ffmpeg", "-y", "-i", "in.mp4", "-preset", "fast", "out.mp4"]

    def test_does_not_mutate_original(self):
        cmd = ["ffmpeg", "-preset", "slow", "out.mp4"]
        original = list(cmd)
        _swap_preset(cmd, "fast")
        assert cmd == original

    def test_replaces_medium(self):
        cmd = ["ffmpeg", "-preset", "medium", "out.mp4"]
        result = _swap_preset(cmd, "ultrafast")
        assert result == ["ffmpeg", "-preset", "ultrafast", "out.mp4"]


# ── run_ffmpeg ────────────────────────────────────────────────────


class TestRunFfmpeg:
    @patch("genlab_core.media.ffmpeg_utils.subprocess.run")
    def test_success(self, mock_run: MagicMock):
        """First call succeeds — returns CompletedProcess directly."""
        expected = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout="ok", stderr=""
        )
        mock_run.return_value = expected

        result = run_ffmpeg(["ffmpeg", "-preset", "slow", "out.mp4"])

        assert result is expected
        assert mock_run.call_count == 1

    @patch("genlab_core.media.ffmpeg_utils.subprocess.run")
    def test_timeout_retries_with_fallback(self, mock_run: MagicMock):
        """First call times out → retries with fallback preset and 180s timeout."""
        fallback_result = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout="ok", stderr=""
        )
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
            fallback_result,
        ]

        cmd = ["ffmpeg", "-y", "-preset", "slow", "out.mp4"]
        result = run_ffmpeg(cmd, timeout=120, fallback_preset="fast")

        assert result is fallback_result
        assert mock_run.call_count == 2

        # Verify the retry command used -preset fast
        retry_cmd = mock_run.call_args_list[1][0][0]
        preset_idx = retry_cmd.index("-preset")
        assert retry_cmd[preset_idx + 1] == "fast"

        # Verify the retry used 180s timeout
        retry_kwargs = mock_run.call_args_list[1][1]
        assert retry_kwargs["timeout"] == 180

    @patch("genlab_core.media.ffmpeg_utils.subprocess.run")
    def test_both_timeout_raises(self, mock_run: MagicMock):
        """Both calls timeout → raises TimeoutExpired from retry."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=180),
        ]

        cmd = ["ffmpeg", "-y", "-preset", "slow", "out.mp4"]
        with pytest.raises(subprocess.TimeoutExpired):
            run_ffmpeg(cmd, timeout=120, fallback_preset="fast")

        assert mock_run.call_count == 2

    @patch("genlab_core.media.ffmpeg_utils.subprocess.run")
    def test_called_process_error_propagates(self, mock_run: MagicMock):
        """CalledProcessError (non-zero exit) on first call propagates immediately."""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd="ffmpeg", stderr="encode error"
        )

        with pytest.raises(subprocess.CalledProcessError):
            run_ffmpeg(["ffmpeg", "-preset", "slow", "out.mp4"])

        # Should NOT retry on CalledProcessError — only on TimeoutExpired
        assert mock_run.call_count == 1

    @patch("genlab_core.media.ffmpeg_utils.subprocess.run")
    def test_no_preset_in_cmd_adds_on_fallback(self, mock_run: MagicMock):
        """If original cmd has no -preset, fallback inserts it before last arg."""
        fallback_result = subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout="", stderr=""
        )
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
            fallback_result,
        ]

        cmd = ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"]
        result = run_ffmpeg(cmd, timeout=120, fallback_preset="fast")

        assert result is fallback_result
        retry_cmd = mock_run.call_args_list[1][0][0]
        assert "-preset" in retry_cmd
        preset_idx = retry_cmd.index("-preset")
        assert retry_cmd[preset_idx + 1] == "fast"
        # -preset fast should come before out.mp4 (last arg)
        assert retry_cmd[-1] == "out.mp4"
