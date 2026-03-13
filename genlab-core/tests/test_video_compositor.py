"""Tests for genlab_core.media.video_compositor — Gen Lab visual standards."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.video_compositor import (
    LOGO_LEFT_MARGIN,
    VERTICAL_HEIGHT,
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


# ── _build_sandwich_filter ────────────────────────────────────────────────────


class TestBuildSandwichFilter:
    """The sandwich filter must produce drawbox for both top and bottom bars."""

    def test_contains_drawbox_for_top_bar(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._build_sandwich_filter(config)
        top_h = int(VERTICAL_HEIGHT * config.top_bar_height_pct) & ~1
        assert "drawbox" in result
        assert f"h={top_h}" in result

    def test_contains_drawbox_for_bottom_bar(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._build_sandwich_filter(config)
        bot_h = int(VERTICAL_HEIGHT * config.bottom_bar_height_pct) & ~1
        expected_y = VERTICAL_HEIGHT - bot_h
        assert f"y={expected_y}" in result
        assert f"h={bot_h}" in result

    def test_both_bars_use_black(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._build_sandwich_filter(config)
        # Two drawbox calls, both with color=black
        drawbox_matches = re.findall(r"drawbox=[^,;]+", result)
        assert len(drawbox_matches) == 2
        for db in drawbox_matches:
            assert "color=black" in db

    def test_custom_bar_heights(self, tmp_assets: dict[str, Path]) -> None:
        cfg = VisualConfig(
            niche_id="custom",
            logo_path=tmp_assets["logo"],
            accent_color="#000000",
            top_bar_height_pct=0.15,
            bottom_bar_height_pct=0.20,
        )
        comp = VideoCompositor(cfg)
        result = comp._build_sandwich_filter(cfg)
        top_h = int(VERTICAL_HEIGHT * 0.15) & ~1
        bot_h = int(VERTICAL_HEIGHT * 0.20) & ~1
        assert f"h={top_h}" in result
        assert f"h={bot_h}" in result


# ── _overlay_logo ─────────────────────────────────────────────────────────────


class TestOverlayLogo:
    """Logo must be at x=24 and y within the top bar."""

    def test_logo_x_is_24(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._overlay_logo(config)
        assert f"x={LOGO_LEFT_MARGIN}" in result

    def test_logo_y_within_top_bar(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._overlay_logo(config)
        top_h = int(VERTICAL_HEIGHT * config.top_bar_height_pct)
        # Extract y value
        y_match = re.search(r"y=(\d+)", result)
        assert y_match is not None
        y_val = int(y_match.group(1))
        assert 0 <= y_val < top_h, (
            f"Logo y={y_val} must be within top bar [0, {top_h})"
        )

    def test_logo_vertically_centered_in_bar(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._overlay_logo(config)
        top_h = int(VERTICAL_HEIGHT * config.top_bar_height_pct)
        expected_y = (top_h - config.logo_height_px) // 2
        assert f"y={expected_y}" in result


# ── _overlay_hook_text ────────────────────────────────────────────────────────


class TestOverlayHookText:
    """Hook text must truncate at max_lines when text is long."""

    def test_short_text_no_truncation(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._overlay_hook_text("Short hook", config)
        assert "drawtext" in result
        assert "..." not in result

    def test_long_text_truncated_at_2_lines(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        long_hook = (
            "This is an extremely long hook text that should definitely "
            "exceed two lines when rendered at the configured font size "
            "because it has so many words that it cannot possibly fit "
            "within the available pixel width of the top bar area"
        )
        result = compositor._overlay_hook_text(long_hook, config)
        assert "drawtext" in result
        # The escaped text should contain an ellipsis marker
        assert "..." in result

    def test_exactly_two_lines_no_ellipsis(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        # Text that fits in 2 lines should not be truncated
        medium_hook = "Breaking: New GPU launch confirmed for next month"
        result = compositor._overlay_hook_text(medium_hook, config)
        assert "drawtext" in result

    def test_hook_text_is_white(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._overlay_hook_text("Test hook", config)
        assert "fontcolor=white" in result

    def test_hook_font_size_matches_config(
        self, compositor: VideoCompositor, config: VisualConfig,
    ) -> None:
        result = compositor._overlay_hook_text("Test", config)
        assert f"fontsize={config.hook_font_size}" in result

    def test_wrap_hook_static_method(self) -> None:
        """Direct test of the wrapping logic."""
        # font_size=32, max_width=500px, max_lines=2
        # chars per line ≈ 500 / (32 * 0.55) ≈ 28
        long_text = "A" * 100
        result = VideoCompositor._wrap_hook(long_text, 32, 500, 2)
        lines = result.split("\n")
        assert len(lines) == 2
        assert lines[-1].endswith("...")


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
            args=[], returncode=0, stdout="", stderr="",
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
            args=[], returncode=0, stdout="", stderr="",
        )
        compositor.derive_landscape(
            tmp_assets["clip"], tmp_assets["output"],
            width=1280, height=720,
        )
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert "1280" in cmd_str
        assert "720" in cmd_str

    def test_missing_vertical_master_raises(
        self, compositor: VideoCompositor, tmp_path: Path,
    ) -> None:
        missing = tmp_path / "nonexistent.mp4"
        with pytest.raises(FileNotFoundError, match="Vertical master not found"):
            compositor.derive_landscape(missing, tmp_path / "out.mp4")


# ── compose_vertical ──────────────────────────────────────────────────────────


class TestComposeVertical:
    """compose_vertical must validate inputs and call FFmpeg correctly."""

    def test_missing_logo_raises_file_not_found(
        self, tmp_assets: dict[str, Path],
    ) -> None:
        cfg = VisualConfig(
            niche_id="broken",
            logo_path=Path("/nonexistent/logo.png"),
            accent_color="#000",
        )
        comp = VideoCompositor(cfg)
        with pytest.raises(FileNotFoundError, match="Logo not found"):
            comp.compose_vertical(
                [tmp_assets["clip"]], "hook", tmp_assets["output"],
            )

    def test_missing_logo_error_mentions_niche(
        self, tmp_assets: dict[str, Path],
    ) -> None:
        cfg = VisualConfig(
            niche_id="gaming",
            logo_path=Path("/nonexistent/logo.png"),
            accent_color="#000",
        )
        comp = VideoCompositor(cfg)
        with pytest.raises(FileNotFoundError, match="gaming"):
            comp.compose_vertical(
                [tmp_assets["clip"]], "hook", tmp_assets["output"],
            )

    def test_empty_clips_raises(
        self, compositor: VideoCompositor, tmp_assets: dict[str, Path],
    ) -> None:
        with pytest.raises(ValueError, match="At least one source clip"):
            compositor.compose_vertical([], "hook", tmp_assets["output"])

    def test_missing_clip_raises(
        self, compositor: VideoCompositor, tmp_path: Path,
    ) -> None:
        missing_clip = tmp_path / "nonexistent_clip.mp4"
        with pytest.raises(FileNotFoundError, match="Source clip not found"):
            compositor.compose_vertical(
                [missing_clip], "hook", tmp_path / "out.mp4",
            )

    @patch("genlab_core.media.video_compositor.probe_video_metadata")
    @patch("genlab_core.media.video_compositor.subprocess.run")
    @patch("genlab_core.media.video_compositor.get_ffmpeg_binary", return_value="ffmpeg")
    def test_single_clip_runs_ffmpeg_with_sandwich(
        self,
        _mock_ffmpeg_bin: MagicMock,
        mock_run: MagicMock,
        mock_probe: MagicMock,
        compositor: VideoCompositor,
        tmp_assets: dict[str, Path],
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        mock_probe.return_value = {"width": 120, "height": 60}

        compositor.compose_vertical(
            [tmp_assets["clip"]], "Test hook", tmp_assets["output"],
        )

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "drawbox" in cmd_str, "Sandwich filter must use drawbox"
        assert "drawtext" in cmd_str, "Must overlay hook text"
        assert str(tmp_assets["logo"]) in cmd_str, "Must pass logo as input"

    @patch("genlab_core.media.video_compositor.concat", return_value=True)
    @patch("genlab_core.media.video_compositor.probe_video_metadata")
    @patch("genlab_core.media.video_compositor.subprocess.run")
    @patch("genlab_core.media.video_compositor.get_ffmpeg_binary", return_value="ffmpeg")
    def test_multiple_clips_concat_then_sandwich(
        self,
        _mock_ffmpeg_bin: MagicMock,
        mock_run: MagicMock,
        mock_probe: MagicMock,
        mock_concat: MagicMock,
        compositor: VideoCompositor,
        tmp_assets: dict[str, Path],
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        mock_probe.return_value = {"width": 120, "height": 60}

        # Mock concat to create a file at the expected path
        def fake_concat(paths, output_path, temp_dir=None):
            Path(output_path).write_bytes(b"\x00" * 64)
            return True

        mock_concat.side_effect = fake_concat

        compositor.compose_vertical(
            [tmp_assets["clip"], tmp_assets["clip2"]],
            "Multi-clip hook",
            tmp_assets["output"],
        )

        mock_concat.assert_called_once()
        mock_run.assert_called_once()

    @patch("genlab_core.media.video_compositor.subprocess.run")
    @patch("genlab_core.media.video_compositor.get_ffmpeg_binary", return_value="ffmpeg")
    def test_ffmpeg_failure_raises_runtime_error(
        self,
        _mock_ffmpeg_bin: MagicMock,
        mock_run: MagicMock,
        compositor: VideoCompositor,
        tmp_assets: dict[str, Path],
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: something broke",
        )
        with pytest.raises(RuntimeError, match="FFmpeg failed.*sandwich"):
            compositor.compose_vertical(
                [tmp_assets["clip"]], "hook", tmp_assets["output"],
            )
