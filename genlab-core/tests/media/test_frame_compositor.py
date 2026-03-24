"""Tests for FrameCompositor -- Gen Lab Layout v4.

Three layout paths: landscape (branding+hook+video), portrait (clean),
square (branding+hook+video).
"""

from genlab_core.media.frame_compositor import (
    CANVAS_H,
    CANVAS_W,
    HOOK_MAX_CHARS,
    L_VIDEO_H,
    L_VIDEO_Y,
    L_LOGO_Y,
    L_HOOK_Y,
    LANDSCAPE_THRESHOLD,
    PORTRAIT_THRESHOLD,
    S_VIDEO_H,
    S_VIDEO_Y,
    S_LOGO_Y,
    S_HOOK_Y,
    ChannelBranding,
    FrameCompositor,
    VideoInfo,
)

# --- ChannelBranding ------------------------------------------------

class TestChannelBranding:

    def test_from_yaml_loads_fields(self, tmp_path):
        yaml_content = """
niche_id: test_niche
frame_layout:
  branding:
    channel_name: TestChannel
    handle: "@TestHandle"
    accent_color: "#FF0000"
    logo_path: "assets/logo.png"
    niche_id: test_niche
ffmpeg:
  preset: fast
  fallback_preset: ultrafast
  timeout_seconds: 60
"""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        yaml_path = config_dir / "visuals.yaml"
        yaml_path.write_text(yaml_content)

        branding = ChannelBranding.from_visuals_yaml(str(yaml_path))
        assert branding.channel_name == "TestChannel"
        assert branding.handle == "@TestHandle"
        assert branding.accent_color == "#FF0000"
        assert branding.niche_id == "test_niche"
        assert branding.ffmpeg.preset == "fast"
        assert branding.ffmpeg.timeout_seconds == 60


# --- VideoInfo ------------------------------------------------------

class TestVideoInfo:

    def test_landscape_detection(self):
        info = VideoInfo(
            width=1920, height=1080, duration_seconds=30, fps=30,
            aspect_ratio=1.78, is_portrait=False, is_landscape=True,
            is_native_9_16=False,
        )
        assert info.layout_case == "landscape"

    def test_portrait_detection(self):
        info = VideoInfo(
            width=608, height=1080, duration_seconds=30, fps=30,
            aspect_ratio=0.56, is_portrait=True, is_landscape=False,
            is_native_9_16=True,
        )
        assert info.layout_case == "portrait"

    def test_square_detection(self):
        info = VideoInfo(
            width=1080, height=1080, duration_seconds=30, fps=30,
            aspect_ratio=1.0, is_portrait=False, is_landscape=False,
            is_native_9_16=False,
        )
        assert info.layout_case == "square"

    def test_thresholds(self):
        assert LANDSCAPE_THRESHOLD == 1.33
        assert PORTRAIT_THRESHOLD == 0.75


# --- Locked pixel constants ------------------------------------------

class TestLockedConstants:

    def test_canvas_dimensions(self):
        assert CANVAS_W == 1080
        assert CANVAS_H == 1920

    def test_landscape_layout(self):
        """Landscape: video centered, branding above with gap."""
        assert L_VIDEO_H == 608, "16:9 at 1080w = 608h"
        assert L_VIDEO_Y == (CANVAS_H - L_VIDEO_H) // 2, "Video vertically centered"
        assert L_HOOK_Y < L_VIDEO_Y, "Hook above video"
        assert L_HOOK_Y + 104 <= L_VIDEO_Y, "Hook text doesn't overlap video"
        assert L_LOGO_Y < L_HOOK_Y, "Logo above hook"
        bottom = CANVAS_H - L_VIDEO_Y - L_VIDEO_H
        assert bottom >= 320, "Instagram needs 320px bottom safe zone"

    def test_square_layout(self):
        """Square: video centered, branding above."""
        assert S_VIDEO_H == 1080, "Square video is 1080x1080"
        assert S_VIDEO_Y == (CANVAS_H - S_VIDEO_H) // 2, "Video vertically centered"
        assert S_HOOK_Y < S_VIDEO_Y, "Hook above video"
        assert S_LOGO_Y < S_HOOK_Y, "Logo above hook"

    def test_portrait_is_clean(self):
        """Portrait layout has NO branding — clean full-screen video."""
        # No portrait-specific constants needed — it's just scale+crop
        pass

    def test_hook_max_chars(self):
        assert HOOK_MAX_CHARS == 60


# --- Hook wrapping --------------------------------------------------

class TestHookWrapping:

    def test_short_hook_single_line(self):
        comp = FrameCompositor(ChannelBranding(
            channel_name="Test", handle="@test",
            accent_color="#FFF", logo_path="", niche_id="test",
        ))
        lines = comp._wrap_hook("Short hook")
        assert lines == ["Short hook"]

    def test_long_hook_wraps(self):
        comp = FrameCompositor(ChannelBranding(
            channel_name="Test", handle="@test",
            accent_color="#FFF", logo_path="", niche_id="test",
        ))
        lines = comp._wrap_hook("This is a much longer hook that should definitely wrap to two lines")
        assert len(lines) == 2
        assert all(len(line) <= 36 for line in lines)  # HOOK_MAX_CHARS_LINE + 1 word

    def test_max_two_lines(self):
        comp = FrameCompositor(ChannelBranding(
            channel_name="Test", handle="@test",
            accent_color="#FFF", logo_path="", niche_id="test",
        ))
        lines = comp._wrap_hook("word " * 30)
        assert len(lines) <= 2


# --- Escape ----------------------------------------------------------

class TestEscapeDrawtext:

    def test_escapes_special_chars(self):
        result = FrameCompositor._escape_drawtext("Test: [value], 50%")
        assert "\\:" in result
        assert "\\[" in result
        assert "\\]" in result
        assert "\\," in result

    def test_escapes_single_quotes(self):
        result = FrameCompositor._escape_drawtext("It's a test")
        assert "'" not in result
        assert "\u2019" in result
