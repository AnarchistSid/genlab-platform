"""Tests for FrameCompositor -- the canonical Gen Lab frame layout.

These tests document and enforce the locked frame spec.
If a test fails, the frame layout has drifted -- fix the compositor, not the test.
"""

import os
import re
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.frame_compositor import (
    CANVAS_H,
    CANVAS_W,
    HOOK_MAX_CHARS,
    HOOK_Y_LANDSCAPE,
    LANDSCAPE_THRESHOLD,
    PORTRAIT_THRESHOLD,
    TOP_BAR_H,
    VIDEO_H_LANDSCAPE,
    VIDEO_Y_LANDSCAPE,
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
        """Sprint 56 format: branding nested under frame_layout."""
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
        assert b.handle == "@theclutchwire"
        assert b.logo_path == "new/logo.png"


# --- Layout case detection -------------------------------------------

class TestLayoutCaseDetection:
    """Verifies correct case assignment based on source aspect ratio."""

    def make_info(self, w, h, duration=30.0, fps=30.0):
        ar = w / h
        return VideoInfo(
            width=w, height=h, duration_seconds=duration, fps=fps,
            aspect_ratio=ar,
            is_portrait=ar < PORTRAIT_THRESHOLD,
            is_landscape=ar > LANDSCAPE_THRESHOLD,
            is_native_9_16=(0.50 <= ar <= PORTRAIT_THRESHOLD),
        )

    def test_native_9_16_is_case_1(self):
        info = self.make_info(608, 1080)     # classic 9:16 phone video
        assert info.layout_case == 1
        assert info.is_native_9_16

    def test_portrait_tiktok_is_case_1(self):
        info = self.make_info(720, 1280)     # still 9:16
        assert info.layout_case == 1

    def test_landscape_16_9_is_case_2(self):
        info = self.make_info(1920, 1080)    # standard YouTube
        assert info.layout_case == 2

    def test_landscape_1280_720_is_case_2(self):
        info = self.make_info(1280, 720)
        assert info.layout_case == 2

    def test_square_is_case_2(self):
        # Square (1.0) > LANDSCAPE_THRESHOLD (0.90) -> treated as landscape Case 2
        info = self.make_info(720, 720)
        assert info.layout_case == 2

    def test_wide_banner_is_case_2(self):
        info = self.make_info(1920, 400)     # very wide
        assert info.layout_case == 2


# --- Locked pixel constants ------------------------------------------

class TestLockedConstants:
    """These numbers are the spec. If they change, the layout has changed."""

    def test_canvas_dimensions(self):
        assert CANVAS_W == 1080
        assert CANVAS_H == 1920

    def test_top_bar_height(self):
        assert TOP_BAR_H == 180

    def test_16_9_video_height(self):
        # 1080 * (9/16) = 607.5 -> floor to 607 or 608
        assert VIDEO_H_LANDSCAPE in (607, 608)

    def test_16_9_video_y_start_is_centred(self):
        # Must be exactly (1920 - VIDEO_H_LANDSCAPE) / 2
        expected_y = int((CANVAS_H - VIDEO_H_LANDSCAPE) / 2)
        assert VIDEO_Y_LANDSCAPE == expected_y

    def test_bottom_safe_zone_satisfies_all_platforms(self):
        # Bottom black zone = CANVAS_H - (VIDEO_Y_LANDSCAPE + VIDEO_H_LANDSCAPE)
        bottom_clear = CANVAS_H - (VIDEO_Y_LANDSCAPE + VIDEO_H_LANDSCAPE)
        assert bottom_clear >= 420, (
            f"YouTube needs 420px clear at bottom but only {bottom_clear}px available"
        )
        assert bottom_clear >= 320, "Instagram needs 320px clear at bottom"
        assert bottom_clear >= 340, "Facebook needs 340px clear at bottom"

    def test_hook_max_chars(self):
        assert HOOK_MAX_CHARS == 60


# --- Hook truncation -------------------------------------------------

class TestHookTruncation:

    def _make_compositor(self):
        branding = ChannelBranding(
            channel_name="Test", handle="@test",
            accent_color="#FFF", logo_path="", niche_id="gaming",
        )
        return FrameCompositor(branding)

    def test_long_hook_is_truncated(self, tmp_path):
        comp = self._make_compositor()
        long_hook = "A" * 80

        with patch("genlab_core.media.frame_compositor.probe_video") as mock_probe, \
             patch("genlab_core.media.frame_compositor.subprocess.run") as mock_run:

            mock_probe.return_value = VideoInfo(
                width=1920, height=1080, duration_seconds=30, fps=30.0,
                aspect_ratio=1.778, is_portrait=False, is_landscape=True,
                is_native_9_16=False,
            )
            mock_run.return_value = MagicMock(returncode=0, stderr="")

            out_path = str(tmp_path / "out.mp4")
            comp.compose(
                source_video_path="/fake/clip.mp4",
                hook_text=long_hook,
                output_path=out_path,
            )

            # Extract the filter_complex arg from the ffmpeg call
            call_args = mock_run.call_args[0][0]
            fc_index = call_args.index("-filter_complex")
            filtergraph = call_args[fc_index + 1]

            # The hook text in the filtergraph must be <= 60 chars + "..."
            hook_in_filter = re.search(r"text='([^']*)'.*shadowx", filtergraph)
            if hook_in_filter:
                hook_text_used = hook_in_filter.group(1)
                assert len(hook_text_used) <= HOOK_MAX_CHARS + 3  # +3 for "..."


# --- FFmpeg command structure ----------------------------------------

class TestFFmpegCommandStructure:
    """Verify the FFmpeg commands have the correct structure for each case."""

    def _make_compositor(self, logo_exists=False):
        branding = ChannelBranding(
            channel_name="CriticalRush", handle="@CriticalRush",
            accent_color="#00FF88",
            logo_path="/fake/logo.png" if logo_exists else "",
            niche_id="gaming",
        )
        return FrameCompositor(branding)

    def _make_info(self, w, h):
        ar = w / h
        return VideoInfo(
            width=w, height=h, duration_seconds=30, fps=30.0,
            aspect_ratio=ar,
            is_portrait=ar < PORTRAIT_THRESHOLD,
            is_landscape=ar > LANDSCAPE_THRESHOLD,
            is_native_9_16=(0.50 <= ar <= PORTRAIT_THRESHOLD),
        )

    def test_case1_filtergraph_uses_crop_not_pad(self):
        """Case 1: Native 9:16 uses crop to fill -- not pad (no black bars)."""
        comp = self._make_compositor()
        info = self._make_info(608, 1080)
        cmd = comp._build_cmd_case1("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "crop" in fc, "Case 1 must use crop to fill canvas"
        # No 'pad' filter -- no black bars
        assert "pad" not in fc, "Case 1 must NOT have black padding (video fills canvas)"

    def test_case1_no_black_canvas_input(self):
        """Case 1: No lavfi black canvas -- source video IS the background.
        The drawbox for the top bar uses color=black but there must be no
        color=black:WxH lavfi source (that's Case 2 only)."""
        comp = self._make_compositor()
        info = self._make_info(608, 1080)
        cmd = comp._build_cmd_case1("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        # No lavfi color source (Case 2 creates "color=black:1080x1920")
        assert f"color=black:{CANVAS_W}x{CANVAS_H}" not in fc, (
            "Case 1 must not create a lavfi black canvas"
        )

    def test_case2_has_black_canvas(self):
        """Case 2: Must create a black canvas and overlay the video."""
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_case2("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "color=black" in fc, "Case 2 must create a black canvas"
        assert "overlay" in fc, "Case 2 must overlay source video on canvas"

    def test_case2_video_centred_at_correct_y(self):
        """Case 2: Video must be placed at y=VIDEO_Y_LANDSCAPE (656px)."""
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_case2("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert f"overlay=0:{VIDEO_Y_LANDSCAPE}" in fc, (
            f"Case 2 video must be overlaid at y={VIDEO_Y_LANDSCAPE}, got: {fc[:500]}"
        )

    def test_output_is_bt709(self):
        """All cases must output bt709 colour space."""
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_case2("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        assert "-colorspace" in cmd
        assert "bt709" in cmd

    def test_output_is_h264_aac(self):
        """All cases must output H.264 video with AAC audio."""
        comp = self._make_compositor()
        info = self._make_info(1920, 1080)
        cmd = comp._build_cmd_case2("/src.mp4", "Hook", "/out.mp4", info, 30, 0, 15, "slow", 30)
        assert "libx264" in cmd
        assert "aac" in cmd

    def test_drawtext_escape_special_chars(self):
        """Special characters in hook text must be escaped for FFmpeg."""
        comp = self._make_compositor()
        raw = "Bam's 83-point game: unreal"
        escaped = comp._escape_drawtext(raw)
        assert "'" not in escaped or "\\'" in escaped
        assert ":" not in escaped or "\\:" in escaped


# --- Per-channel visuals.yaml spec check -----------------------------

class TestVisualYamlSpec:
    """Verify each channel's visuals.yaml has the frame_layout spec locked in."""

    CHANNELS = [
        ("Content Scraper/config/visuals.yaml", "ai_news", "@BlackboxBrief"),
        ("CriticalRush/niches/gaming/config/visuals.yaml", "gaming", "@CriticalRush"),
        ("ClutchWire/config/visuals.yaml", "sports", "@theclutchwire"),
        ("SpliceReel/config/visuals.yaml", "movies", "@SpliceReel"),
        ("FrameDrift/config/visuals.yaml", "anime", "@theframedrift"),
    ]

    @pytest.mark.parametrize("yaml_path,expected_niche,expected_handle", CHANNELS)
    def test_frame_layout_present(self, yaml_path, expected_niche, expected_handle):
        import yaml
        # Resolve relative to GenLab root
        full_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", yaml_path)
        if not os.path.exists(full_path):
            pytest.skip(f"{yaml_path} not found")
        with open(full_path) as f:
            cfg = yaml.safe_load(f)
        assert "frame_layout" in cfg, f"{yaml_path} missing frame_layout spec"

    @pytest.mark.parametrize("yaml_path,expected_niche,expected_handle", CHANNELS)
    def test_canvas_is_1080_1920(self, yaml_path, expected_niche, expected_handle):
        import yaml
        full_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", yaml_path)
        if not os.path.exists(full_path):
            pytest.skip(f"{yaml_path} not found")
        with open(full_path) as f:
            cfg = yaml.safe_load(f)
        canvas = cfg.get("frame_layout", {}).get("canvas", {})
        assert canvas.get("width") == 1080
        assert canvas.get("height") == 1920

    @pytest.mark.parametrize("yaml_path,expected_niche,expected_handle", CHANNELS)
    def test_top_bar_height_is_180(self, yaml_path, expected_niche, expected_handle):
        import yaml
        full_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", yaml_path)
        if not os.path.exists(full_path):
            pytest.skip(f"{yaml_path} not found")
        with open(full_path) as f:
            cfg = yaml.safe_load(f)
        top_bar = cfg.get("frame_layout", {}).get("top_bar", {})
        assert top_bar.get("height") == 180

    @pytest.mark.parametrize("yaml_path,expected_niche,expected_handle", CHANNELS)
    def test_native_portrait_has_no_bottom_safe_zone(self, yaml_path, expected_niche, expected_handle):
        import yaml
        full_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", yaml_path)
        if not os.path.exists(full_path):
            pytest.skip(f"{yaml_path} not found")
        with open(full_path) as f:
            cfg = yaml.safe_load(f)
        native = cfg.get("frame_layout", {}).get("layout_cases", {}).get("native_portrait", {})
        assert native.get("bottom_safe_zone") == 0, (
            f"{yaml_path}: native 9:16 must have bottom_safe_zone=0 "
            "(video fills full canvas, runs to edge)"
        )

    @pytest.mark.parametrize("yaml_path,expected_niche,expected_handle", CHANNELS)
    def test_16_9_bottom_zone_satisfies_platforms(self, yaml_path, expected_niche, expected_handle):
        import yaml
        full_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", yaml_path)
        if not os.path.exists(full_path):
            pytest.skip(f"{yaml_path} not found")
        with open(full_path) as f:
            cfg = yaml.safe_load(f)
        landscape = cfg.get("frame_layout", {}).get("layout_cases", {}).get("landscape_16_9", {})
        bottom_h = landscape.get("bottom_black_height", 0)
        assert bottom_h >= 420, (
            f"{yaml_path}: bottom_black_height={bottom_h} < 420 (YouTube minimum)"
        )
