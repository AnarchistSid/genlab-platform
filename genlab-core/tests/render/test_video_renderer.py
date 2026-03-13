"""Tests for genlab_core.render.video_renderer — Sprint 37."""

import pytest
from unittest.mock import patch, MagicMock

from genlab_core.render.video_renderer import VideoRenderer, NicheVisualConfig, _escape_drawtext


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
    src = tmp_path / "source.mp4"
    src.touch()
    renderer = VideoRenderer(config)
    with patch("genlab_core.render.video_renderer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="fake error")
        with pytest.raises(RuntimeError, match="FFmpeg failed"):
            renderer.render(str(src), "Hook", str(tmp_path / "out.mp4"))


def test_escape_drawtext_handles_special_chars():
    """Verify FFmpeg-unsafe characters are escaped."""
    result = _escape_drawtext("It's a test: 100%")
    assert "'" not in result or "\\'" in result
    assert "%%" in result


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
