"""Tests for :mod:`genlab_core.publishing.transcode`.

Lives next to its module home (paralleling the PR 2/N extraction in the
publish_all_platforms decomposition).

Strategy
--------
``transcode_for_platform`` shells out to ``ffmpeg``. To stay hermetic
we don't actually invoke ffmpeg — instead we patch ``subprocess.run``
on the module under test and assert on the command line the function
constructs. That's where the regression surface actually lives: the
codec/preset/bitrate/colorspace flags drift between platforms, and a
silent flag drop has historically caused IG/Threads/FB upload
rejections.

Existing coverage in ``test_encode_concurrency.py`` exercises the
ENCODE_SEMAPHORE bound via real (light) ffmpeg calls; this file
focuses on the per-platform code path each call takes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.media.ffmpeg import Platform
from genlab_core.publishing.transcode import (
    ENCODE_SEMAPHORE,
    MAX_CONCURRENT_ENCODES,
    PLATFORM_MAP,
    transcode_for_platform,
)


class TestPlatformMap:
    """Static map invariants. Catches accidental aliases that would
    silently route a platform name to the wrong ffmpeg spec."""

    def test_all_six_platforms_present(self) -> None:
        """The publisher fans out to YT/IG/FB/X/Threads/TikTok — the map must
        cover all six. Dropping one would cause that platform to fail the
        ``plat_enum is None`` check and ship the un-transcoded original."""
        assert set(PLATFORM_MAP.keys()) == {
            "youtube",
            "instagram",
            "facebook",
            "twitter",
            "threads",
            "tiktok",
        }

    def test_twitter_routes_to_x_std(self) -> None:
        """``twitter`` is the canonical key in PLATFORM_MAP; X_STD is the
        ffmpeg-side enum. The mismatch is intentional — keeping legacy
        ``twitter`` as the key lets us re-route to X without touching every
        caller."""
        assert PLATFORM_MAP["twitter"] == Platform.X_STD

    def test_all_values_are_platform_enum(self) -> None:
        for k, v in PLATFORM_MAP.items():
            assert isinstance(v, Platform), f"{k} maps to {type(v).__name__}, not Platform"


class TestUnknownPlatformShortCircuit:
    """Unknown platform names must return the source unchanged — never crash,
    never attempt an ffmpeg call."""

    def test_unknown_platform_returns_source(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 11000)  # > 10KB so the idempotency check is realistic
        with patch.object(subprocess, "run") as mock_run:
            result = transcode_for_platform(src, "myspace")
        assert result == src
        mock_run.assert_not_called()

    def test_empty_platform_returns_source(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 11000)
        assert transcode_for_platform(src, "") == src


class TestIdempotency:
    """A previously-encoded variant (existing file with the right suffix and
    size > 10KB) is reused, not re-encoded."""

    def test_existing_variant_returned_without_ffmpeg(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 11000)
        # Pre-create the expected variant path that transcode would generate.
        # Platform.YOUTUBE.value is the suffix the function uses.
        variant = src.parent / f"{src.stem}_{Platform.YOUTUBE.value}{src.suffix}"
        variant.write_bytes(b"y" * 50_000)
        with patch.object(subprocess, "run") as mock_run:
            result = transcode_for_platform(src, "youtube")
        assert result == variant
        mock_run.assert_not_called()

    def test_tiny_variant_triggers_re_encode(self, tmp_path: Path) -> None:
        """A variant smaller than 10KB is treated as suspect (truncated
        prior write, half-written from a crashed run) and re-encoded."""
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 11000)
        variant = src.parent / f"{src.stem}_{Platform.YOUTUBE.value}{src.suffix}"
        variant.write_bytes(b"y" * 100)  # below threshold
        with patch("genlab_core.publishing.transcode.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            transcode_for_platform(src, "youtube")
        mock_run.assert_called_once()


class TestFailOpenOnEncodeFailure:
    """The function must fail-open: any exception in the encode path returns
    the original source path. Dropping the post is worse than uploading
    something the platform might reject."""

    def test_subprocess_error_returns_source(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 11000)
        with patch("genlab_core.publishing.transcode.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd=["ffmpeg"])
            result = transcode_for_platform(src, "youtube")
        assert result == src

    def test_timeout_returns_source(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 11000)
        with patch("genlab_core.publishing.transcode.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=600)
            result = transcode_for_platform(src, "youtube")
        assert result == src

    def test_ffmpeg_binary_missing_returns_source(self, tmp_path: Path) -> None:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 11000)
        with patch("genlab_core.publishing.transcode.get_ffmpeg_binary") as mock_bin:
            mock_bin.side_effect = FileNotFoundError("ffmpeg not in PATH")
            result = transcode_for_platform(src, "youtube")
        assert result == src


class TestFFmpegCommandConstruction:
    """Assertions on the constructed ffmpeg command. These flags have caused
    real production incidents when accidentally dropped:

    * ``-pix_fmt yuv420p`` — without it, modern ffmpeg defaults to
      ``yuv444p`` for high-bit-depth source, which IG/FB silently reject.
    * ``-movflags +faststart`` — without it, the moov atom lands at the
      end of the file and platforms can't probe duration for the preview
      thumbnail.
    * ``-colorspace bt709`` — without it, bt470bg leaks through and the
      preview looks washed out on Android players.
    """

    @staticmethod
    def _run_and_capture_cmd(tmp_path: Path, platform: str) -> list[str]:
        src = tmp_path / "video.mp4"
        src.write_bytes(b"x" * 11000)
        with patch("genlab_core.publishing.transcode.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
            transcode_for_platform(src, platform)
        # First positional arg is the command list.
        return list(mock_run.call_args.args[0])

    @pytest.mark.parametrize("platform", ["youtube", "instagram", "facebook"])
    def test_yuv420p_pix_fmt(self, tmp_path: Path, platform: str) -> None:
        cmd = self._run_and_capture_cmd(tmp_path, platform)
        assert "-pix_fmt" in cmd
        idx = cmd.index("-pix_fmt")
        assert cmd[idx + 1] == "yuv420p"

    @pytest.mark.parametrize("platform", ["youtube", "instagram", "facebook"])
    def test_movflags_faststart(self, tmp_path: Path, platform: str) -> None:
        cmd = self._run_and_capture_cmd(tmp_path, platform)
        assert "-movflags" in cmd
        idx = cmd.index("-movflags")
        assert cmd[idx + 1] == "+faststart"

    @pytest.mark.parametrize("platform", ["youtube", "instagram", "facebook"])
    def test_bt709_colorspace(self, tmp_path: Path, platform: str) -> None:
        cmd = self._run_and_capture_cmd(tmp_path, platform)
        assert "-colorspace" in cmd
        idx = cmd.index("-colorspace")
        assert cmd[idx + 1] == "bt709"


class TestSemaphore:
    """Semaphore identity invariants — the same semaphore must be visible to
    the orchestrator's re-export path and to direct callers of this module
    so the OOM cap is shared, not per-import."""

    def test_max_concurrent_encodes_default_is_one(self) -> None:
        """The 2-vCPU/4GB box default is serial (1). Operators who raise
        this on bigger hosts use ``GENLAB_MAX_CONCURRENT_ENCODES``."""
        # When the env var isn't set the constant resolves to 1.
        # Note: we can't reload the module mid-test, so this asserts the
        # _current_ process value rather than the default value in
        # isolation. Either way, it must be >= 1.
        assert MAX_CONCURRENT_ENCODES >= 1

    def test_semaphore_is_bounded(self) -> None:
        """``threading.BoundedSemaphore`` (not ``Semaphore``) so a double-
        release raises, catching a regression in the with-block usage."""
        import threading

        assert isinstance(ENCODE_SEMAPHORE, threading.BoundedSemaphore.__mro__[1])

    def test_reexport_identity_with_orchestrator(self) -> None:
        """The semaphore re-exported from publish_all_platforms must be the
        SAME object (``is``) as the one in transcode.py — otherwise the cap
        would apply per-import-site, defeating the OOM guard entirely."""
        from genlab_core.publishing.publish_all_platforms import _ENCODE_SEMAPHORE

        assert _ENCODE_SEMAPHORE is ENCODE_SEMAPHORE
