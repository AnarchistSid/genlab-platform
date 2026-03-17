"""Tests for genlab_core.rendering.video_renderer — Sprint 37."""

from unittest.mock import patch

import pytest
from genlab_core.media.ffmpeg_utils import escape_drawtext
from genlab_core.rendering.video_renderer import NicheVisualConfig, VideoRenderer


@pytest.fixture
def config():
    return NicheVisualConfig(
        niche_id="test_niche",
        channel_name="TestChannel",
        channel_handle="@testhandle",
        accent_color="#3B82F6",
        logo_path="/tmp/logo.png",
    )


def test_render_raises_if_source_missing(config, tmp_path):
    """FileNotFoundError when source clip doesn't exist."""
    renderer = VideoRenderer(config)
    with pytest.raises(FileNotFoundError, match="Source clip not found"):
        renderer.render("/nonexistent/clip.mp4", "Hook", str(tmp_path / "out.mp4"))


def test_render_dry_run_returns_output_path(config, tmp_path):
    """Dry run returns output path without executing FFmpeg."""
    src = tmp_path / "source.mp4"
    src.touch()
    renderer = VideoRenderer(config)
    out = str(tmp_path / "output.mp4")
    result = renderer.render(str(src), "Hook text here", out, dry_run=True)
    assert result == out


def test_get_standard_youtube_returns_h265():
    """YouTube standard uses libx265 codec."""
    from genlab_core.video.standards import Platform, get_standard
    std = get_standard(Platform.YOUTUBE)
    assert std.video.codec == "libx265"


def test_get_standard_instagram_returns_h264():
    """Instagram standard uses libx264 codec."""
    from genlab_core.video.standards import Platform, get_standard
    std = get_standard(Platform.INSTAGRAM)
    assert std.video.codec == "libx264"


def test_render_raises_on_ffmpeg_failure(config, tmp_path):
    """RuntimeError when FFmpeg returns non-zero exit code."""
    import subprocess as sp
    src = tmp_path / "source.mp4"
    src.touch()
    renderer = VideoRenderer(config)
    # run_ffmpeg lives in ffmpeg_utils — mock subprocess.run there
    with patch("genlab_core.media.ffmpeg_utils.subprocess.run") as mock_run:
        # run_ffmpeg uses check=True, so subprocess.run raises CalledProcessError
        mock_run.side_effect = sp.CalledProcessError(
            returncode=1, cmd="ffmpeg", stderr="fake error",
        )
        with pytest.raises(RuntimeError, match="FFmpeg failed"):
            renderer.render(str(src), "Hook", str(tmp_path / "out.mp4"))


def test_escape_drawtext_handles_special_chars():
    """Verify FFmpeg-unsafe characters are escaped (canonical escape_drawtext)."""
    result = escape_drawtext("It's a test: 100%")
    # Apostrophe replaced with Unicode U+2019
    assert "'" not in result
    assert "\u2019" in result
    # Colon escaped
    assert "\\:" in result


def test_niche_visual_config_is_frozen():
    """NicheVisualConfig is immutable."""
    cfg = NicheVisualConfig(
        niche_id="test",
        channel_name="Test",
        channel_handle="@test",
        accent_color="#000",
        logo_path="/tmp/x.png",
    )
    with pytest.raises(AttributeError):
        cfg.niche_id = "other"  # type: ignore[misc]
