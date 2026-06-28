"""Pins for RENDER #4 — portrait branding richness via visuals.yaml.

AUTONOMY-GAP Q3 (2026-06-27): `show_name` + `show_handle` defaults
flipped True → portrait reels now render name + handle by default for
brand parity with landscape / square. `show_hook` remains False (hook
overlay can cover content; opt-in creative-test variant). Niches can
still set `show_name: false` / `show_handle: false` to revert to the
older logo-only treatment.

What the tests pin:
  1. **Default behavior**: name + handle drawtext in the filtergraph,
     hook absent (no overlap with content)
  2. Each opt-OUT flag removes exactly its overlay (show_name: false →
     no channel name; show_handle: false → no handle)
  3. show_hook=True adds wrapped drawtext lines for the hook
  4. Combined flags layer in order: name → handle → hook
  5. Y-coordinate adjusts: handle moves down when name is also on
  6. Logo overlay is preserved alongside any text overlays (regression
     pin — branding text must not displace the R-26 logo invariant)
  7. visuals.yaml `portrait_branding:` block loads correctly via
     from_visuals_yaml (with all three keys, including opt-OUT)
  8. Missing block → defaults inherited (name + handle ON, hook OFF)
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
    """Build a ChannelBranding for portrait tests.

    Flag overrides default to the dataclass defaults (name=True,
    handle=True, hook=False) — AUTONOMY-GAP Q3. Pass explicit kwargs
    to opt OUT or to flip the hook flag on for creative-test pins.
    """
    return ChannelBranding(
        niche_id="test",
        channel_name="TestChannel",
        handle="@testchannel",
        accent_color="#FF4500",
        logo_path=str(logo_path),
        portrait_show_name=flag_overrides.get("show_name", True),
        portrait_show_handle=flag_overrides.get("show_handle", True),
        portrait_show_hook=flag_overrides.get("show_hook", False),
    )


def _get_filtergraph(cmd: list[str]) -> str:
    """Extract the filter_complex string from an FFmpeg command list."""
    idx = cmd.index("-filter_complex")
    return cmd[idx + 1]


class TestPortraitDefaultsRenderNameAndHandle:
    """AUTONOMY-GAP Q3 (2026-06-27): with no YAML overrides, portrait
    reels now render logo + name + handle by default — brand parity
    with landscape / square. Hook stays off (creative-test variant)."""

    def test_portrait_cmd_includes_branding_filter(self, logo: Path, portrait_info: VideoInfo):
        """Default portrait filtergraph carries the name + handle
        drawtext filters. Headline pin for the AUTONOMY-GAP Q3 fix —
        mirrors the unconditional ``_build_branding_filters`` call
        that landscape / square layouts have always had."""
        branding = _make_branding(logo)  # defaults: name + handle ON, hook OFF
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
        # Name + handle drawtext present by default
        assert "drawtext" in graph
        assert "TestChannel" in graph
        assert "@testchannel" in graph
        # Hook drawtext stays absent at defaults (creative-test variant)
        assert "Some hook" not in graph
        # Filtergraph still terminates correctly
        assert "[out]" in graph

    def test_portrait_logo_still_present_after_branding_added(
        self, logo: Path, portrait_info: VideoInfo
    ):
        """Regression pin: the R-26 logo invariant survives the
        AUTONOMY-GAP Q3 default-flip. Branding text must layer ON TOP
        of the logo overlay, not displace it."""
        branding = _make_branding(logo)  # defaults: name + handle ON
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
        # R-26 invariants — all still present
        assert "[1:v]" in graph  # logo input wired in
        assert "scale=60:60" in graph  # LOGO_SIZE x LOGO_SIZE
        assert "overlay=45:70" in graph  # P_LOGO_X, P_LOGO_Y
        # Logo input file path appears in the input args
        assert str(logo) in cmd
        # Logo overlay precedes the name drawtext in the filtergraph
        logo_idx = graph.find("overlay=45:70")
        name_idx = graph.find("TestChannel")
        assert logo_idx != -1 and name_idx != -1
        assert logo_idx < name_idx, (
            "Logo overlay must come before name drawtext so the text "
            "layers on top of the logo, not under it."
        )

    def test_explicit_opt_out_returns_to_logo_only(self, logo: Path, portrait_info: VideoInfo):
        """A niche that prefers the older logo-only treatment can opt
        OUT of both defaults via visuals.yaml — the escape hatch must
        keep working after AUTONOMY-GAP Q3."""
        branding = _make_branding(logo, show_name=False, show_handle=False)
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
        # No text overlays
        assert "drawtext" not in graph
        # Logo still overlaid (R-26 invariant)
        assert "overlay=45:70" in graph
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
        empty drawtext (which FFmpeg would error on). Handle disabled
        here so the assertion only covers the name path."""
        branding = _make_branding(logo, show_name=True, show_handle=False)
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
        same pattern as landscape/square layouts. The ``handle alone''
        case has to explicitly disable show_name because that flag now
        defaults True after AUTONOMY-GAP Q3."""
        branding_name_only = _make_branding(logo, show_name=False, show_handle=True)
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
        Long hook → 2 drawtext filters, each with its own y= offset.
        Name + handle disabled here so the drawtext-count assertion
        isolates the hook overlay path."""
        branding = _make_branding(logo, show_name=False, show_handle=False, show_hook=True)
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
        # With name/handle off, only the hook drawtexts remain
        assert drawtext_count >= 2

    def test_hook_skipped_when_empty(self, logo: Path, portrait_info: VideoInfo):
        """Empty hook text + hook flag on must not render an empty
        drawtext. Name + handle disabled so the assertion isolates the
        hook path."""
        branding = _make_branding(logo, show_name=False, show_handle=False, show_hook=True)
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

    def test_visuals_yaml_without_portrait_branding_inherits_defaults(self, tmp_path: Path):
        """AUTONOMY-GAP Q3 (2026-06-27): existing visuals.yaml files
        with no portrait_branding block now inherit name=True +
        handle=True + hook=False — brand parity with landscape /
        square without per-niche YAML updates."""
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
        assert branding.portrait_show_name is True
        assert branding.portrait_show_handle is True
        assert branding.portrait_show_hook is False

    def test_visuals_yaml_with_non_dict_portrait_block_defaults(self, tmp_path: Path):
        """Defensive: a YAML typo like `portrait_branding: true`
        shouldn't crash from_visuals_yaml — fall back to dataclass
        defaults (name + handle ON, hook OFF, post AUTONOMY-GAP Q3)."""
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
        assert branding.portrait_show_name is True
        assert branding.portrait_show_handle is True
        assert branding.portrait_show_hook is False

    def test_visuals_yaml_can_opt_out_of_defaults(self, tmp_path: Path):
        """AUTONOMY-GAP Q3 escape hatch: a niche that wants the older
        logo-only treatment must be able to explicitly opt OUT via
        the same YAML block."""
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
            "    show_name: false\n"
            "    show_handle: false\n"
        )
        branding = ChannelBranding.from_visuals_yaml(str(yaml_path))
        assert branding.portrait_show_name is False
        assert branding.portrait_show_handle is False
        assert branding.portrait_show_hook is False
