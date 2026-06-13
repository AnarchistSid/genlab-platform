"""Tests for genlab_core.media.video_compositor — Gen Lab visual standards."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.media.video_compositor import (
    VideoCompositor,
    VisualConfig,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_assets(tmp_path: Path) -> dict[str, Path]:
    """Create minimal fake assets for tests."""
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 256)

    clip2 = tmp_path / "clip2.mp4"
    clip2.write_bytes(b"\x00" * 256)

    output = tmp_path / "output.mp4"
    return {"logo": logo, "clip": clip, "clip2": clip2, "output": output}


@pytest.fixture()
def config(tmp_assets: dict[str, Path]) -> VisualConfig:
    return VisualConfig(
        niche_id="test_niche",
        logo_path=tmp_assets["logo"],
        accent_color="#FF4500",
    )


@pytest.fixture()
def compositor(config: VisualConfig) -> VideoCompositor:
    return VideoCompositor(config)


# ── Sandwich filter / logo overlay / hook overlay / hook wrap tests ──────────
# REMOVED in DEAD #1 (2026-06-13). These pinned private methods
# (_build_sandwich_filter, _overlay_logo, _overlay_hook_text, _wrap_hook)
# that were called only by compose_vertical, which itself was deleted
# because all 5 niches now render through FrameCompositor.


# ── derive_landscape ──────────────────────────────────────────────────────────


class TestDeriveLandscape:
    """derive_landscape must use blurred pillarbox (scale+blur bg, centered fg)."""

    @patch("genlab_core.media.video_compositor.subprocess.run")
    @patch("genlab_core.media.video_compositor.get_ffmpeg_binary", return_value="ffmpeg")
    def test_calls_ffmpeg_with_blur_and_overlay(
        self,
        _mock_ffmpeg_bin: MagicMock,
        mock_run: MagicMock,
        compositor: VideoCompositor,
        tmp_assets: dict[str, Path],
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        vertical = tmp_assets["clip"]
        output = tmp_assets["output"]

        compositor.derive_landscape(vertical, output)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)

        # Must contain the blur background approach
        assert "gblur=sigma=20" in cmd_str, "Must apply Gaussian blur to background"
        # Must contain the centered overlay
        assert "overlay=(W-w)/2:(H-h)/2" in cmd_str, "Foreground must be centered"
        # Must use split to create bg and fg from same input
        assert "split" in cmd_str, "Must split input into bg and fg"
        # Must not crop the foreground
        assert "crop" in cmd_str, "Background is cropped to fill, not fg"

    @patch("genlab_core.media.video_compositor.subprocess.run")
    @patch("genlab_core.media.video_compositor.get_ffmpeg_binary", return_value="ffmpeg")
    def test_custom_dimensions(
        self,
        _mock_ffmpeg_bin: MagicMock,
        mock_run: MagicMock,
        compositor: VideoCompositor,
        tmp_assets: dict[str, Path],
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )
        compositor.derive_landscape(
            tmp_assets["clip"],
            tmp_assets["output"],
            width=1280,
            height=720,
        )
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "1280" in cmd_str
        assert "720" in cmd_str

    def test_missing_vertical_master_raises(
        self,
        compositor: VideoCompositor,
        tmp_path: Path,
    ) -> None:
        missing = tmp_path / "nonexistent.mp4"
        with pytest.raises(FileNotFoundError, match="Vertical master not found"):
            compositor.derive_landscape(missing, tmp_path / "out.mp4")


# ── compose_vertical tests REMOVED in DEAD #1 (2026-06-13) ────────────────────
# `VideoCompositor.compose_vertical` was deleted because all 5 niches render
# through FrameCompositor instead. The 8 tests in TestComposeVertical pinned
# the sandwich/concat/logo-overlay/hook-overlay behavior of code that no
# longer exists. derive_landscape tests remain above.
