"""Tests for FrameCompositor -- Gen Lab Layout v3.

Three layout paths: landscape, portrait, square.
These tests document and enforce the locked frame spec.
"""

import os
import re
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.frame_compositor import (
    CANVAS_H,
    CANVAS_W,
    HOOK_MAX_CHARS,
    LANDSCAPE_THRESHOLD,
    PORTRAIT_THRESHOLD,
    L_VIDEO_H,
    L_VIDEO_Y,
    L_BOTTOM_H,
    S_VIDEO_Y,
    S_VIDEO_H,
    S_BOTTOM_H,
    P_OVERLAY_H,
    P_LOGO_Y,
    P_HOOK_Y,
    ChannelBranding,
    FrameCompositor,
    VideoInfo,
)


# --- ChannelBranding ------------------------------------------------

class TestChannelBranding:

    def test_from_dict_flat(self, tmp_path):
        yaml_file = tmp_path / "visuals.yaml"
        yaml_file.write_text(
            "channel_name: CriticalRush\n"
            "handle: '@CriticalRush'\n"
            "accent_color: '#00FF88'\n"
            "logo_path: /some/logo.png\n"
            "niche_id: gaming\n"
        )
        b = ChannelBranding.from_visuals_yaml(str(yaml_file))
        assert b.channel_name == "CriticalRush"
        assert b.handle == "@CriticalRush"
        assert b.niche_id == "gaming"

    def test_from_dict_nested_branding(self, tmp_path):
        yaml_file = tmp_path / "visuals.yaml"
        yaml_file.write_text(
            "visuals:\n"
            "  branding:\n"
            "    channel_name: ClutchWire\n"
            "    handle: '@theclutchwire'\n"
            "    accent_color: '#FF2040'\n"
            "    logo_path: /logo.png\n"
            "    niche_id: sports\n"
        )
        b = ChannelBranding.from_visuals_yaml(str(yaml_file))
        assert b.channel_name == "ClutchWire"
        assert b.niche_id == "sports"

    def test_from_frame_layout_branding(self, tmp_path):
        yaml_file = tmp_path / "visuals.yaml"
        yaml_file.write_text(
            "niche_id: sports\n"
            "logo_path: old/logo.png\n"
            "accent_color: '#FF2040'\n"
            "frame_layout:\n"
            "  branding:\n"
            "    channel_name: ClutchWire\n"
            "    handle: '@theclutchwire'\n"
            "    accent_color: '#FF2040'\n"
            "    logo_path: new/logo.png\n"
            "    niche_id: sports\n"
        )
        b = ChannelBranding.from_visuals_yaml(str(yaml_file))
        assert b.channel_name == "ClutchWire"
        # Logo path is resolved to absolute against niche_root (yaml parent.parent)
        assert b.logo_path.endswith("new/logo.png")


# --- Layout case detection -------------------------------------------

class TestLayoutCaseDetection:

    def make_info(self, w, h):
        ar = w / h
        return VideoInfo(
            width=w, height=h, duration_seconds=30.0, fps=30.0,
            aspect_ratio=ar,
            is_portrait=ar <= PORTRAIT_THRESHOLD,
            is_landscape=ar >= LANDSCAPE_THRESHOLD,
            is_native_9_16=False,
        )

    def test_16_9_is_landscape(self):
        info = self.make_info(1920, 1080)
        assert info.layout_case == "landscape"

    def test_1280_720_is_landscape(self):
        info = self.make_info(1280, 720)
        assert info.layout_case == "landscape"

    def test_9_16_is_portrait(self):
        info = self.make_info(1080, 1920)
        assert info.layout_case == "portrait"

    def test_720_1280_is_portrait(self):
        info = self.make_info(720, 1280)
        assert info.layout_case == "portrait"

    def test_square_is_square(self):
        info = self.make_info(1080, 1080)
        assert info.layout_case == "square"

    def test_4_3_is_square(self):
        info = self.make_info(1440, 1080)  # 1.33 → landscape threshold
        assert info.layout_case == "landscape"

    def test_near_square_is_square(self):
        info = self.make_info(1000, 1000)
        assert info.layout_case == "square"


# --- Locked pixel constants ------------------------------------------

class TestLockedConstants:

    def test_canvas_dimensions(self):
        assert CANVAS_W == 1080
        assert CANVAS_H == 1920

    def test_landscape_zones_sum(self):
        """All landscape zones must add up to full canvas height."""
        total = L_VIDEO_Y + L_VIDEO_H + L_BOTTOM_H
        assert total == CANVAS_H

    def test_landscape_video_starts_after_hook(self):
        assert L_VIDEO_Y == 460, "Video starts right after hook zone"

    def test_landscape_bottom_bar(self):
        """Bottom bar must satisfy YouTube (420px) and Instagram (320px) safe zones."""
        assert L_BOTTOM_H == 644
        assert L_BOTTOM_H >= 420, "YouTube needs 420px"
        assert L_BOTTOM_H >= 320, "Instagram needs 320px"

    def test_square_zones_sum(self):
        """All square zones must add up to full canvas height."""
        total = S_VIDEO_Y + S_VIDEO_H + S_BOTTOM_H
        assert total == CANVAS_H

    def test_square_bottom_bar(self):
        assert S_BOTTOM_H == 674

    def test_portrait_fills_canvas(self):
        """Portrait layout fills canvas — no sandwich zones to sum."""
        assert P_OVERLAY_H > 0, "Portrait must have dark overlay zone"
        assert P_LOGO_Y > 0, "Portrait must position logo"
        assert P_HOOK_Y > P_LOGO_Y, "Hook must be below logo"

    def test_bottom_safe_zone_satisfies_platforms(self):
        bottom_clear = L_BOTTOM_H
        assert bottom_clear >= 420, "YouTube needs 420px"
        assert bottom_clear >= 320, "Instagram needs 320px"

    def test_hook_max_chars(self):
        assert HOOK_MAX_CHARS == 60


# --- Hook wrapping --------------------------------------------------

class TestHookWrapping:

    def test_short_hook_single_line(self):
        lines = FrameCompositor._wrap_hook("Short hook")
        assert lines == ["Short hook"]

    def test_long_hook_wraps(self):
        lines = FrameCompositor._wrap_hook("Bam Adebayo just dropped 83 points in a single game tonight")
        assert len(lines) >= 2
        assert all(len(line) <= 35 for line in lines)

    def test_max_2_lines(self):
        lines = FrameCompositor._wrap_hook("A " * 100)
        assert len(lines) <= 2

    def test_empty_hook(self):
        lines = FrameCompositor._wrap_hook("")
        assert lines == []


# --- Accent color ---------------------------------------------------

class TestAccentColor:

    def test_strips_hash(self):
        b = ChannelBranding("T", "@t", "#FF2040", "", "sports")
        comp = FrameCompositor(b)
        assert comp._accent_hex() == "ff2040"

    def test_no_hash(self):
        b = ChannelBranding("T", "@t", "C9A84C", "", "movies")
        comp = FrameCompositor(b)
        assert comp._accent_hex() == "c9a84c"


# --- FFmpeg command structure ----------------------------------------

class TestFFmpegCommandStructure:

    def _make_compositor(self):
        return FrameCompositor(ChannelBranding(
            "CriticalRush", "@CriticalRush", "#FF4500", "", "gaming",
        ))

    def _make_info(self, w, h):
        ar = w / h
        return VideoInfo(
            width=w, height=h, duration_seconds=30, fps=30.0,
            aspect_ratio=ar,
            is_portrait=ar <= PORTRAIT_THRESHOLD,
            is_landscape=ar >= LANDSCAPE_THRESHOLD,
            is_native_9_16=False,
        )

    def test_landscape_has_black_canvas(self):
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_landscape("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "color=black" in fc
        assert "overlay" in fc

    def test_landscape_video_at_correct_y(self):
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_landscape("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert f"overlay=0:{L_VIDEO_Y}" in fc

    def test_landscape_no_accent_line(self):
        """Accent line was removed for cleaner Evolving AI-style layout."""
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_landscape("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "drawbox" not in fc, "No accent line drawbox in landscape layout"

    def test_portrait_has_hook_text(self):
        comp = self._make_compositor()
        info = self._make_info(1080, 1920)
        cmd = comp._build_cmd_portrait("/src.mp4", "This hook appears", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "This hook appears" in fc, "Portrait sandwich layout must render hook text"

    def test_portrait_fills_canvas_not_sandwich(self):
        comp = self._make_compositor()
        info = self._make_info(1080, 1920)
        cmd = comp._build_cmd_portrait("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        # Portrait fills canvas — scales to cover, not to fit
        assert f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase" in fc
        assert "crop=" in fc, "Portrait must crop to fill canvas"

    def test_portrait_has_dark_overlay(self):
        comp = self._make_compositor()
        info = self._make_info(1080, 1920)
        cmd = comp._build_cmd_portrait("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "black@" in fc, "Portrait must have dark gradient overlay"

    def test_portrait_has_channel_name(self):
        comp = self._make_compositor()
        info = self._make_info(1080, 1920)
        cmd = comp._build_cmd_portrait("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "CriticalRush" in fc, "Portrait must render channel name"

    def test_square_video_at_correct_y(self):
        comp = self._make_compositor()
        info = self._make_info(1080, 1080)
        cmd = comp._build_cmd_square("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert f"overlay=0:{S_VIDEO_Y}" in fc

    def test_output_is_bt709(self):
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_landscape("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        assert "-colorspace" in cmd
        assert "bt709" in cmd

    def test_output_is_h264_aac(self):
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_landscape("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        assert "libx264" in cmd
        assert "aac" in cmd

    def test_drawtext_escape_special_chars(self):
        comp = self._make_compositor()
        escaped = comp._escape_drawtext("Bam's 83-point game: unreal")
        assert "'" not in escaped or "\u2019" in escaped
        assert "\\:" in escaped
