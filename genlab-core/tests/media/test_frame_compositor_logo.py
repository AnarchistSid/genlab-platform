"""R-26: the channel logo is a mandatory invariant.

- A missing logo file must fail LOUD (not silently ship an unbranded reel).
- Portrait reels must carry the logo too (they previously had zero branding).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.media.frame_compositor import (
    LOGO_SIZE,
    ChannelBranding,
    FrameCompositor,
    VideoInfo,
)


def _branding(logo_path: str) -> ChannelBranding:
    return ChannelBranding(
        channel_name="Test",
        handle="@test",
        accent_color="#FFFFFF",
        logo_path=logo_path,
        niche_id="test",
    )


def _portrait_info() -> VideoInfo:
    return VideoInfo(
        width=1080,
        height=1920,
        duration_seconds=30.0,
        fps=30.0,
        aspect_ratio=1080 / 1920,
        is_portrait=True,
        is_landscape=False,
        is_native_9_16=True,
    )


def test_compose_raises_when_logo_file_missing(tmp_path: Path) -> None:
    comp = FrameCompositor(_branding(str(tmp_path / "does_not_exist.png")))
    # Guard fires before probe_video, so the source need not exist.
    with pytest.raises(RuntimeError, match="logo"):
        comp.compose(str(tmp_path / "src.mp4"), "a hook", str(tmp_path / "out.mp4"))


def test_compose_raises_when_logo_path_empty(tmp_path: Path) -> None:
    comp = FrameCompositor(_branding(""))
    with pytest.raises(RuntimeError, match="logo"):
        comp.compose(str(tmp_path / "src.mp4"), "a hook", str(tmp_path / "out.mp4"))


def test_portrait_command_overlays_the_logo(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n")  # dummy file — only existence matters here
    comp = FrameCompositor(_branding(str(logo)))

    cmd = comp._build_cmd_portrait(
        str(tmp_path / "s.mp4"),
        "hook",
        str(tmp_path / "o.mp4"),
        _portrait_info(),
        None,
        0.0,
        20,
        "fast",
        30,
    )

    # The logo is supplied as a second input and overlaid onto the video.
    assert str(logo) in cmd
    fg = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay" in fg
    assert f"scale={LOGO_SIZE}:{LOGO_SIZE}" in fg
    assert "[1:v]" in fg  # the logo input is referenced
