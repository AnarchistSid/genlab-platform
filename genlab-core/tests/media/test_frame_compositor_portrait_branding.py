"""Pins for RENDER #4 — portrait branding richness, opt-in via visuals.yaml.

Default behavior (all 3 flags False) is byte-identical to pre-RENDER-#4
portrait renders: logo only, no text. Each flag turns on its overlay
independently so the operator can ship logo+name, logo+hook, etc. as
creative tests without touching the others.

What the tests pin:
  1. **Default = unchanged** — the headline pin: no flags → no drawtext
     in the filtergraph (only the logo overlay)
  2. Each flag adds exactly its overlay (name → drawtext fontfile + name
     text; handle → drawtext fontfile + handle text; hook → wrapped
     drawtext lines)
  3. Combined flags layer in order: name first, then handle (positioned
     below name when name is on), then hook
  4. Y-coordinate adjusts: handle moves down when name is also on
  5. visuals.yaml `portrait_branding:` block loads correctly via
     from_visuals_yaml (with all three keys)
  6. Missing block → defaults preserved (False)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.media.frame_compositor import ChannelBranding, FrameCompositor, VideoInfo


@pytest.fixture
def logo(tmp_path: Path) -> Path:
    p = tmp_path / "logo.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return p


@pytest.fixture
def portrait_info() -> VideoInfo:
    """A 9:16 source clip — triggers the portrait layout case."""
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


def _make_branding(logo_path: Path, **flag_overrides) -> ChannelBranding:
    return ChannelBranding(
        niche_id="test",
        channel_name="TestChannel",
        handle="@testchannel",
        accent_color="#FF4500",
        logo_path=str(logo_path),
        portrait_show_name=flag_overrides.get("show_name", False),
        portrait_show_handle=flag_overrides.get("show_handle", False),
        portrait_show_hook=flag_overrides.get("show_hook", False),
    )


def _get_filtergraph(cmd: list[str]) -> str:
    """Extract the filter_complex string from an FFmpeg command list."""
    idx = cmd.index("-filter_complex")
    return cmd[idx + 1]


class TestDefaultsPreserveExistingBehavior:
    """The headline RENDER #4 safety invariant: with no YAML changes,
    portrait renders are exactly what they were before."""

    def test_no_flags_no_drawtext_in_filtergraph(self, logo: Path, portrait_info: VideoInfo):
        branding = _make_branding(logo)  # all defaults False
        comp = FrameCompositor(branding)
        cmd = comp._build_cmd_portrait(
            "src.mp4",
            "Some hook",
            "out.mp4",
            portrait_info,
            duration=15.0,
            trim_start=0.0,
            crf=20,
            preset="fast",
            fps=30,
        )
        graph = _get_filtergraph(cmd)
        # Only the logo path — NO drawtext anywhere in the filtergraph.
        # This is the byte-identical-to-before invariant.
        assert "drawtext" not in graph, (
            "Portrait defaults must NOT add drawtext — flip a "
            "portrait_show_* flag in visuals.yaml to opt in."
        )
        # Logo overlay still present
        assert "overlay=45:70" in graph
        # Final out label still wired correctly
        assert "[out]" in graph


class TestShowName:
    def test_name_overlay_appears_when_flag_set(self, logo: Path, portrait_info: VideoInfo):
        branding = _make_branding(logo, show_name=True)
        comp = FrameCompositor(branding)
        cmd = comp._build_cmd_portrait(
            "src.mp4",
            "h",
            "out.mp4",
            portrait_info,
            duration=15.0,
            trim_start=0.0,
            crf=20,
            preset="fast",
            fps=30,
        )
        graph = _get_filtergraph(cmd)
        # Channel name text in a drawtext filter
        assert "drawtext" in graph
        assert "TestChannel" in graph
        # Positioned next to the logo (x past LOGO + margin)
        assert "x=121" in graph  # P_LOGO_X(45) + LOGO_SIZE(60) + 16 = 121

    def test_name_skipped_when_channel_name_empty(self, logo: Path, portrait_info: VideoInfo):
        """Even with flag on, an empty channel_name must not render an
        empty drawtext (which FFmpeg would error on)."""
        branding = _make_branding(logo, show_name=True)
        branding.channel_name = ""
        comp = FrameCompositor(branding)
        cmd = comp._build_cmd_portrait(
            "src.mp4",
            "h",
            "out.mp4",
            portrait_info,
            duration=15.0,
            trim_start=0.0,
            crf=20,
            preset="fast",
            fps=30,
        )
        graph = _get_filtergraph(cmd)
        assert "drawtext" not in graph


class TestShowHandle:
    def test_handle_overlay_appears_when_flag_set(self, logo: Path, portrait_info: VideoInfo):
        branding = _make_branding(logo, show_handle=True)
        comp = FrameCompositor(branding)
        cmd = comp._build_cmd_portrait(
            "src.mp4",
            "h",
            "out.mp4",
            portrait_info,
            duration=15.0,
            trim_start=0.0,
            crf=20,
            preset="fast",
            fps=30,
        )
        graph = _get_filtergraph(cmd)
        assert "@testchannel" in graph

    def test_handle_y_moves_down_when_name_also_on(self, logo: Path, portrait_info: VideoInfo):
        """When name AND handle are both on, handle stacks below name —
        same pattern as landscape/square layouts."""
        branding_name_only = _make_branding(logo, show_handle=True)
        branding_both = _make_branding(logo, show_name=True, show_handle=True)

        comp_solo = FrameCompositor(branding_name_only)
        comp_stacked = FrameCompositor(branding_both)

        graph_solo = _get_filtergraph(
            comp_solo._build_cmd_portrait(
                "s.mp4",
                "h",
                "o.mp4",
                portrait_info,
                duration=15.0,
                trim_start=0.0,
                crf=20,
                preset="fast",
                fps=30,
            )
        )
        graph_stacked = _get_filtergraph(
            comp_stacked._build_cmd_portrait(
                "s.mp4",
                "h",
                "o.mp4",
                portrait_info,
                duration=15.0,
                trim_start=0.0,
                crf=20,
                preset="fast",
                fps=30,
            )
        )
        # When alone, handle Y = P_LOGO_Y(70) + 8 = 78
        # When stacked under name, handle Y = 78 + NAME_FONT_SIZE(24) + 6 = 108
        # Extract the @testchannel drawtext y= value from each graph
        # (regex-free string match keeps the pin obvious)
        assert "y=78" in graph_solo or ":y=78" in graph_solo
        assert "y=108" in graph_stacked or ":y=108" in graph_stacked


class TestShowHook:
    def test_hook_lines_appear_when_flag_set(self, logo: Path, portrait_info: VideoInfo):
        branding = _make_branding(logo, show_hook=True)
        comp = FrameCompositor(branding)
        cmd = comp._build_cmd_portrait(
            "src.mp4",
            "Big news! Something happened today",
            "out.mp4",
            portrait_info,
            duration=15.0,
            trim_start=0.0,
            crf=20,
            preset="fast",
            fps=30,
        )
        graph = _get_filtergraph(cmd)
        # Hook text appears as drawtext (at least one line)
        assert "drawtext" in graph
        assert "Big news" in graph
        # Center-aligned x expression
        assert "x=(w-text_w)/2" in graph

    def test_long_hook_wraps_to_multiple_drawtext_lines(self, logo: Path, portrait_info: VideoInfo):
        """Hook wrapping reuses _wrap_hook (≤2 lines, ≤35 chars each).
        Long hook → 2 drawtext filters, each with its own y= offset."""
        branding = _make_branding(logo, show_hook=True)
        comp = FrameCompositor(branding)
        long_hook = "This is a deliberately long hook text intended to wrap to two lines"
        cmd = comp._build_cmd_portrait(
            "src.mp4",
            long_hook,
            "out.mp4",
            portrait_info,
            duration=15.0,
            trim_start=0.0,
            crf=20,
            preset="fast",
            fps=30,
        )
        graph = _get_filtergraph(cmd)
        # Two drawtext filters → at least two `text=` occurrences in the graph
        drawtext_count = graph.count("drawtext=fontfile")
        # When show_hook is the only flag → exactly the hook drawtexts
        assert drawtext_count >= 2

    def test_hook_skipped_when_empty(self, logo: Path, portrait_info: VideoInfo):
        branding = _make_branding(logo, show_hook=True)
        comp = FrameCompositor(branding)
        cmd = comp._build_cmd_portrait(
            "src.mp4",
            "",
            "out.mp4",
            portrait_info,
            duration=15.0,
            trim_start=0.0,
            crf=20,
            preset="fast",
            fps=30,
        )
        graph = _get_filtergraph(cmd)
        assert "drawtext" not in graph


class TestCombinedFlags:
    def test_all_three_flags_layer_in_order(self, logo: Path, portrait_info: VideoInfo):
        """name → handle → hook ordering produces a clean filtergraph
        chain with no orphaned labels."""
        branding = _make_branding(logo, show_name=True, show_handle=True, show_hook=True)
        comp = FrameCompositor(branding)
        cmd = comp._build_cmd_portrait(
            "src.mp4",
            "Short hook",
            "out.mp4",
            portrait_info,
            duration=15.0,
            trim_start=0.0,
            crf=20,
            preset="fast",
            fps=30,
        )
        graph = _get_filtergraph(cmd)
        # Logo overlay first, then name, then handle, then hook
        order_check = [
            graph.find("overlay=45:70"),
            graph.find("TestChannel"),
            graph.find("@testchannel"),
            graph.find("Short hook"),
        ]
        assert all(idx != -1 for idx in order_check)
        # Each subsequent overlay appears after the previous in the graph
        for prev, curr in zip(order_check, order_check[1:], strict=False):
            assert prev < curr, f"order broken: {order_check}"
        # Graph still terminates correctly at [out]
        assert "[out]" in graph


class TestYAMLLoading:
    def test_visuals_yaml_with_portrait_branding_block(self, tmp_path: Path):
        """portrait_branding block in visuals.yaml flows through to the
        ChannelBranding dataclass fields."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"\x89PNG" + b"\x00" * 64)
        yaml_path = tmp_path / "visuals.yaml"
        yaml_path.write_text(
            "niche_id: test\n"
            f"logo_path: {logo}\n"
            "channel_name: TestCh\n"
            "handle: '@testch'\n"
            "accent_color: '#FF0000'\n"
            "branding:\n"
            "  portrait_branding:\n"
            "    show_name: true\n"
            "    show_handle: false\n"
            "    show_hook: true\n"
        )
        branding = ChannelBranding.from_visuals_yaml(str(yaml_path))
        assert branding.portrait_show_name is True
        assert branding.portrait_show_handle is False
        assert branding.portrait_show_hook is True

    def test_visuals_yaml_without_portrait_branding_defaults_to_false(self, tmp_path: Path):
        """Critical: existing visuals.yaml files (no portrait_branding
        block) load with all flags False → unchanged portrait renders.
        This is the migration-safety pin."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"\x89PNG" + b"\x00" * 64)
        yaml_path = tmp_path / "visuals.yaml"
        yaml_path.write_text(
            "niche_id: test\n"
            f"logo_path: {logo}\n"
            "channel_name: TestCh\n"
            "handle: '@testch'\n"
            "accent_color: '#FF0000'\n"
        )
        branding = ChannelBranding.from_visuals_yaml(str(yaml_path))
        assert branding.portrait_show_name is False
        assert branding.portrait_show_handle is False
        assert branding.portrait_show_hook is False

    def test_visuals_yaml_with_non_dict_portrait_block_defaults(self, tmp_path: Path):
        """Defensive: a YAML typo like `portrait_branding: true` shouldn't
        crash from_visuals_yaml — fall back to safe defaults."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"\x89PNG" + b"\x00" * 64)
        yaml_path = tmp_path / "visuals.yaml"
        yaml_path.write_text(
            "niche_id: test\n"
            f"logo_path: {logo}\n"
            "channel_name: TestCh\n"
            "handle: '@testch'\n"
            "accent_color: '#FF0000'\n"
            "branding:\n"
            "  portrait_branding: true\n"  # invalid shape
        )
        branding = ChannelBranding.from_visuals_yaml(str(yaml_path))
        assert branding.portrait_show_name is False
        assert branding.portrait_show_handle is False
        assert branding.portrait_show_hook is False
