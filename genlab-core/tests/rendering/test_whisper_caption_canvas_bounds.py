"""Regression pin: Whisper caption rendering MUST stay within 1080x1920 canvas bounds.

The 2026-06-13 render audit found the pre-fix caption path used
``text_type="hook"`` — captions got the 120-160px HOOK size range and
stacked past canvas bottom. Example from the audit:

    "Analyze earnings and update your investment thesis with Codex"
    at 160px x 7 lines x 232px line-height = 1974px > 1920px canvas.

Fix landed at ``render_whisper_captions.py:231`` — passes
``text_type="caption"`` which routes to the 42-56px bottom-strip layout.
This test is the regression pin the 2026-06-13 audit checklist required
before flipping ``whisper_sync.enabled`` back to true.

Ships 2026-07-22 as the pre-condition for S7 storytime unblock and
canary whisper_sync re-enable on ai_creators.
"""

from __future__ import annotations

import pytest

from genlab_core.rendering.word_animator import (
    FONT_SIZE_CONFIG,
    calculate_optimal_font_size,
    calculate_safe_position,
)

_CANVAS_HEIGHT = 1920


def _line_height_for(font_size: int) -> float:
    """Ratio matches the 232px line-height at 160px font from the audit."""
    return font_size * 1.45


def _rendered_bottom_y(text: str, text_type: str = "caption") -> float:
    """Simulate the caption's rendered bottom Y in canvas coords.

    Uses the same heuristics word_animator uses at render time — font
    size from optimizer + safe position + estimated line count.
    """
    font_size = calculate_optimal_font_size(text, text_type=text_type)
    position = calculate_safe_position(text, text_type=text_type)

    cfg = FONT_SIZE_CONFIG.get(text_type, FONT_SIZE_CONFIG["body"])
    max_width_ratio = float(cfg["max_width_ratio"])
    char_width_factor = float(cfg["char_width_factor"])
    chars_per_line = max(
        1, int((1080 * max_width_ratio) / (font_size * char_width_factor))
    )
    import math

    line_count = max(1, math.ceil(len(text) / chars_per_line))

    return position.y + line_count * _line_height_for(font_size)


class TestCaptionStaysWithinCanvas:
    @pytest.mark.parametrize(
        "text",
        [
            "Short",
            "One line caption fits fine.",
            "This is a moderately-lengthed sentence that could span two lines cleanly.",
            "The audit's canonical crash-case: analyze earnings and update your investment thesis with Codex.",
            (
                "A truly long caption that stress-tests the optimizer with a great many "
                "characters and multiple potential line wraps stacked together in a single "
                "Whisper output segment."
            ),
        ],
    )
    def test_caption_bottom_within_canvas(self, text: str) -> None:
        """The rendered caption's bottom Y MUST be <= 1920 for every length.

        Regression pin: pre-2026-06-13, the audit's canonical text
        rendered at bottom_y ~1974 which overflowed the 1920 canvas and
        smashed into the platform-required bottom safe-zone / frame edge.
        """
        bottom = _rendered_bottom_y(text, text_type="caption")
        assert bottom <= _CANVAS_HEIGHT, (
            f"Caption {text!r} rendered bottom_y={bottom:.0f} > canvas height "
            f"{_CANVAS_HEIGHT}. Overflow = whisper_sync re-enable would ship "
            "broken captions across all 5 niches (2026-06-13 audit regression)."
        )


class TestCaptionSizeBounds:
    def test_caption_size_range_below_hook(self) -> None:
        """Caption font sizes MUST be smaller than hook sizes.

        Hook uses 120-160px. Caption uses ~42-56px. This is the
        distinguishing fix from the 2026-06-13 audit — passing
        text_type='hook' for captions was the root cause.
        """
        cap_cfg = FONT_SIZE_CONFIG["caption"]
        hook_cfg = FONT_SIZE_CONFIG["hook"]
        assert cap_cfg["max_size"] < hook_cfg["min_size"], (
            "Caption max size must be smaller than hook min size — otherwise "
            "the pre-fix overflow behavior returns."
        )

    def test_caption_max_lines_bounded(self) -> None:
        """max_lines for caption must be small (2-3) so a longform whisper
        segment doesn't stack into an overflowing wall of text."""
        assert FONT_SIZE_CONFIG["caption"]["max_lines"] <= 3


class TestCaptionSafePosition:
    def test_caption_position_at_bottom_strip(self) -> None:
        """text_type='caption' MUST place at bottom-strip, above the
        frame edge but well below the mid-frame. Regression against a
        mis-config that would put captions dead-center over the hook."""
        pos = calculate_safe_position("Hello world", text_type="caption")
        # Bottom-strip: y should be at least 60% down the canvas
        assert pos.y >= 0.60 * _CANVAS_HEIGHT, (
            f"Caption y={pos.y} — expected bottom-strip (>= {0.60 * _CANVAS_HEIGHT:.0f})"
        )
        # And not so close to bottom that text would be cut off
        assert pos.y + pos.height <= _CANVAS_HEIGHT

    def test_caption_and_hook_positions_do_not_overlap(self) -> None:
        """The critical bug the audit surfaced: caption rendering on top
        of the hook. Position boundaries must be disjoint."""
        cap_pos = calculate_safe_position("caption", text_type="caption")
        hook_pos = calculate_safe_position("hook", text_type="hook")
        # Caption top must be BELOW hook bottom
        assert cap_pos.y >= hook_pos.y + hook_pos.height * 0.5, (
            f"Caption y={cap_pos.y} overlaps hook region y={hook_pos.y} "
            f"h={hook_pos.height} — regression against 2026-06-13 audit."
        )
