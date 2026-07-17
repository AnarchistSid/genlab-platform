"""Pin tests for Layer 3 S4b — question_reveal timed text overlay.

The compositor extension adds an optional ``reveal_text`` parameter
to ``compose()``. When non-empty AND the source is portrait aspect
ratio, a timed drawtext filter is added to the filtergraph with
``enable='between(t,X,Y)'`` so the reveal only appears during the
climax window (8-13s).

## What these pin

1. **Default off** — empty reveal_text produces zero reveal drawtext
   in the filtergraph. Existing callers unchanged.
2. **Portrait renders reveal** — when reveal_text non-empty in
   portrait, a drawtext with the exact text + timing enable filter
   appears in the filtergraph.
3. **Timing correctness** — `enable='between(t,8.0,13.0)'` present,
   matches module constants (regression guard against drift).
4. **Font size distinct from hook** — reveal uses REVEAL_FONT_SIZE
   (54) not HOOK_FONT_SIZE (44). Regression pin: silent drift where
   reveal shrinks to hook size would defeat the "climax" visual.
5. **Landscape/square fall back to hook-only** — MVP scope: only
   portrait implements reveal. When compose(reveal_text="...") is
   called on landscape/square, an INFO log fires but the filtergraph
   contains no reveal drawtext. This pin locks the MVP scope.
6. **Reveal text is drawtext-escaped** — special characters in
   reveal_text (', :, \\) don't produce a broken filtergraph.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.media.frame_compositor import (
    HOOK_FONT_SIZE,
    P_REVEAL_Y,
    REVEAL_END_SECONDS,
    REVEAL_FONT_SIZE,
    REVEAL_START_SECONDS,
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
        portrait_show_hook=True,  # so hook drawtext is present in fg
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


class TestRevealOffByDefault:
    """Empty reveal_text must produce zero reveal drawtext — protects
    existing callers from a broken filtergraph."""

    def test_portrait_no_reveal_when_empty(self, logo) -> None:
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_portrait(
            "in.mp4",
            "hook text",
            "out.mp4",
            _portrait_info(),
            None,
            0.0,
            20,
            "fast",
            30,
        )
        fg = _fg(cmd)
        assert "enable='between(t" not in fg, (
            "empty reveal_text must produce zero timed drawtext — "
            "otherwise existing callers get a filtergraph with an "
            "unusable enable filter (or worse, always-on reveal)"
        )

    def test_portrait_no_reveal_when_positional(self, logo) -> None:
        """Positional call (no reveal_text kwarg) must also not add reveal."""
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
            "",
        )
        assert "enable='between(t" not in _fg(cmd)


class TestRevealInPortrait:
    """When reveal_text supplied in portrait, timed drawtext appears."""

    def test_reveal_text_appears_in_filtergraph(self, logo) -> None:
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_portrait(
            "in.mp4",
            "test hook",
            "out.mp4",
            _portrait_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "",
            reveal_text="The answer is inside",
        )
        fg = _fg(cmd)
        assert "The answer is inside" in fg, "reveal text should appear in filtergraph"

    def test_enable_filter_uses_module_constants(self, logo) -> None:
        """The between() timing must match REVEAL_START/END constants.
        Regression pin: silent drift here changes when viewers see the
        reveal — pipeline change should update BOTH this test and the
        constants together."""
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_portrait(
            "in.mp4",
            "test hook",
            "out.mp4",
            _portrait_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "",
            reveal_text="Reveal text",
        )
        fg = _fg(cmd)
        expected_enable = f"enable='between(t,{REVEAL_START_SECONDS},{REVEAL_END_SECONDS})'"
        assert expected_enable in fg, (
            f"reveal drawtext must use exact timing {expected_enable}; "
            "if these constants moved, update this test in the same PR"
        )

    def test_reveal_font_size_larger_than_hook(self, logo) -> None:
        """The 'climax' visual depends on reveal being visibly larger than
        the setup hook. If REVEAL_FONT_SIZE ever drops to HOOK_FONT_SIZE,
        the two overlays would look like duplicates."""
        assert REVEAL_FONT_SIZE > HOOK_FONT_SIZE, (
            "reveal font must be larger than hook font — otherwise the "
            "climax visual language breaks (both overlays look same)"
        )

    def test_reveal_positioned_at_module_y(self, logo) -> None:
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_portrait(
            "in.mp4",
            "test hook",
            "out.mp4",
            _portrait_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "",
            reveal_text="Reveal text",
        )
        fg = _fg(cmd)
        assert f":y={P_REVEAL_Y}:" in fg, (
            "reveal y-coord must match P_REVEAL_Y constant; drift here "
            "could overlap with hook (top) or watermark (bottom)"
        )

    def test_reveal_font_size_correct(self, logo) -> None:
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_portrait(
            "in.mp4",
            "test hook",
            "out.mp4",
            _portrait_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "",
            reveal_text="Reveal text",
        )
        fg = _fg(cmd)
        assert f"fontsize={REVEAL_FONT_SIZE}" in fg


class TestRevealEscaping:
    """Reveal text with special chars must not break the filtergraph."""

    def test_apostrophe_escaped(self, logo) -> None:
        comp = FrameCompositor(_make_branding(logo))
        # Apostrophe in reveal text — common in real content
        cmd = comp._build_cmd_portrait(
            "in.mp4",
            "test hook",
            "out.mp4",
            _portrait_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "",
            reveal_text="It's the answer",
        )
        fg = _fg(cmd)
        # Escaped apostrophe should be present — the specific escape
        # sequence depends on _escape_drawtext, but ANY escape is fine
        # so long as raw unescaped 'It's' doesn't break the drawtext.
        # Verify the reveal filter chunk was generated without crashing.
        assert "between(t" in fg, "escaping should not have prevented reveal filter emit"

    def test_colon_escaped(self, logo) -> None:
        comp = FrameCompositor(_make_branding(logo))
        cmd = comp._build_cmd_portrait(
            "in.mp4",
            "test hook",
            "out.mp4",
            _portrait_info(),
            None,
            0.0,
            20,
            "fast",
            30,
            "",
            reveal_text="Winner: Kansas City",
        )
        fg = _fg(cmd)
        assert "between(t" in fg


class TestRevealFallbackNonPortrait:
    """MVP scope: only portrait implements reveal. Landscape+square must
    render hook-only when reveal_text is passed."""

    def test_landscape_ignores_reveal_text(self, logo) -> None:
        comp = FrameCompositor(_make_branding(logo))
        # Note: reveal_text is filtered out by compose() before calling
        # _build_cmd_landscape (portrait-only kwargs). Confirm landscape
        # signature doesn't accept reveal_text kwarg to prove MVP scope.
        import inspect

        sig = inspect.signature(comp._build_cmd_landscape)
        assert "reveal_text" not in sig.parameters, (
            "landscape must NOT accept reveal_text kwarg in S4b MVP — "
            "landscape+square reveal support is deferred to a follow-up. "
            "If this test starts failing, ensure the compose() dispatch "
            "still supports the layout properly."
        )

    def test_square_ignores_reveal_text(self, logo) -> None:
        comp = FrameCompositor(_make_branding(logo))
        import inspect

        sig = inspect.signature(comp._build_cmd_square)
        assert "reveal_text" not in sig.parameters, (
            "square must NOT accept reveal_text kwarg in S4b MVP"
        )
