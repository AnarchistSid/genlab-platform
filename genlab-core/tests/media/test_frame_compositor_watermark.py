"""G9 attribution watermark pins (2026-07-13 audit follow-up).

The attribution stack's Layers 1-5 all live in the caption pipeline —
which platforms let operators edit post-publish. A caption edit strips
the credit line; the reel is left uncredited in the audience's
timeline. The frame watermark is the durable defense: burned into the
video, it survives platform-side edits.

Tests here pin:

  1. When ``source_credit=""`` (default), no ``Original:`` drawtext
     appears in the filtergraph — existing callers unchanged.
  2. When a credit is passed, drawtext appears with the exact
     ``Original: {credit}`` text in ALL 3 layouts (landscape,
     portrait, square).
  3. Watermark drawtext sits BEFORE the final [out] label — a
     mis-ordered filtergraph would fail at FFmpeg parse time.
  4. Y-coordinates match the module constants (regression pin —
     silent drift would put the watermark in the pillarbox area
     where it wouldn't survive crops).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.media.frame_compositor import (
    L_WATERMARK_Y,
    P_WATERMARK_Y,
    S_WATERMARK_Y,
    ChannelBranding,
    FrameCompositor,
    VideoInfo,
)


@pytest.fixture
def logo(tmp_path: Path) -> Path:
    p = tmp_path / "logo.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return p


def _make_branding(logo_path: Path) -> ChannelBranding:
    return ChannelBranding(
        niche_id="test",
        channel_name="TestChannel",
        handle="@testchannel",
        accent_color="#FF4500",
        logo_path=str(logo_path),
    )


def _landscape_info() -> VideoInfo:
    return VideoInfo(
        width=1920,
        height=1080,
        duration_seconds=30.0,
        fps=30.0,
        aspect_ratio=1920 / 1080,
        is_portrait=False,
        is_landscape=True,
        is_native_9_16=False,
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


def _square_info() -> VideoInfo:
    return VideoInfo(
        width=1080,
        height=1080,
        duration_seconds=30.0,
        fps=30.0,
        aspect_ratio=1.0,
        is_portrait=False,
        is_landscape=False,
        is_native_9_16=False,
    )


def _fg(cmd: list[str]) -> str:
    """Extract the filter_complex string from an FFmpeg command list."""
    idx = cmd.index("-filter_complex")
    return cmd[idx + 1]


# ── Default off — no watermark unless source_credit provided ──────


class TestWatermarkOffByDefault:
    def test_landscape_no_watermark_when_empty_credit(self, logo):
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_landscape(
            "in.mp4", "hook", "out.mp4", _landscape_info(), None, 0.0, 20, "fast", 30
        )
        fg = _fg(cmd)
        assert "Original:" not in fg, (
            "empty credit must produce zero watermark drawtext — "
            "otherwise existing callers get a broken filtergraph"
        )

    def test_portrait_no_watermark_when_empty_credit(self, logo):
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_portrait(
            "in.mp4", "hook", "out.mp4", _portrait_info(), None, 0.0, 20, "fast", 30
        )
        assert "Original:" not in _fg(cmd)

    def test_square_no_watermark_when_empty_credit(self, logo):
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_square(
            "in.mp4", "hook", "out.mp4", _square_info(), None, 0.0, 20, "fast", 30
        )
        assert "Original:" not in _fg(cmd)


# ── With credit — drawtext appears in all 3 layouts ──────────────


class TestWatermarkAppears:
    def test_landscape_watermark_text_and_y(self, logo):
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_landscape(
            "in.mp4",
            "hook",
            "out.mp4",
            _landscape_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "MAKI",
        )
        fg = _fg(cmd)
        # ``_escape_drawtext`` escapes ``:`` to ``\:`` inside the
        # filtergraph value. Check for the escaped form — that's
        # what actually reaches FFmpeg. If a future refactor
        # changes escape semantics, this pin fires + operator has
        # to decide whether the new escape is still audience-safe.
        assert "Original\\: MAKI" in fg
        # Y anchored to L_WATERMARK_Y — silent drift would put the
        # watermark in the pillarbox where a platform-side crop would
        # eat it
        assert f"y={L_WATERMARK_Y}" in fg
        # Bottom-right positioning: x expression uses text_w so the
        # padding is right-anchored regardless of handle length
        assert "x=w-text_w-30" in fg
        # Watermark must not corrupt the [out] label — final filter
        # still produces [out]
        assert "[out]" in fg

    def test_portrait_watermark_text_and_y(self, logo):
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_portrait(
            "in.mp4",
            "hook",
            "out.mp4",
            _portrait_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "@testcreator",
        )
        fg = _fg(cmd)
        assert "Original\\: @testcreator" in fg
        assert f"y={P_WATERMARK_Y}" in fg
        assert "[out]" in fg

    def test_square_watermark_text_and_y(self, logo):
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_square(
            "in.mp4",
            "hook",
            "out.mp4",
            _square_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "Rockstar Games",
        )
        fg = _fg(cmd)
        assert "Original\\: Rockstar Games" in fg
        assert f"y={S_WATERMARK_Y}" in fg
        assert "[out]" in fg


class TestWatermarkPlacementSurvivesCrops:
    """Y-coordinate constants must anchor inside the video canvas,
    not the pillarbox. A platform-side crop removes black bars —
    which is why we don't put the watermark there."""

    def test_landscape_y_inside_video_area(self):
        # Landscape video runs from L_VIDEO_Y (656) to L_VIDEO_Y +
        # L_VIDEO_H (1264). The watermark Y must be strictly inside.
        from genlab_core.media.frame_compositor import L_VIDEO_H, L_VIDEO_Y

        assert L_VIDEO_Y < L_WATERMARK_Y < L_VIDEO_Y + L_VIDEO_H

    def test_square_y_inside_video_area(self):
        from genlab_core.media.frame_compositor import S_VIDEO_H, S_VIDEO_Y

        assert S_VIDEO_Y < S_WATERMARK_Y < S_VIDEO_Y + S_VIDEO_H

    def test_portrait_y_near_bottom_of_canvas(self):
        from genlab_core.media.frame_compositor import CANVAS_H

        # Portrait is full-bleed video so watermark just needs to be
        # near the bottom of the canvas (with breathing room above the
        # very edge for safe-zone tolerance)
        assert CANVAS_H - 120 < P_WATERMARK_Y < CANVAS_H - 20
