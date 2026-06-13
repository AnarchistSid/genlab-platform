"""Regression pin: WordByWordAnimator output must fit within the 1080×1920 canvas.

RENDER #1 + TEST #1 fix (2026-06-13). The text_optimizer module was silently
dropped during the 2026-04-04 monorepo conversion (the .py source never
migrated; the .pyc bytecode at
`BlackboxBrief/execution/utils/__pycache__/text_optimizer.cpython-314.pyc`
is the only surviving artifact). For 2.3 months `_HAS_TEXT_OPTIMIZER = False`
permanently, and word_animator fell back to fontsize=160 for every text
type, which overflowed the canvas for any caption ≥ 25 chars + 6 long words.

This test pins:
  1. The text_optimizer is now in-tree (no ImportError fallback)
  2. `calculate_optimal_font_size` returns sane values for each text_type
  3. For known overflow-inducing strings, build_animated_filters' returned
     rendered_bottom_y stays within the canvas
  4. The text_type="caption" parameter (used by render_whisper_captions
     post-fix) produces a small bottom-strip layout, NOT a full-canvas
     hook stack
"""

from __future__ import annotations

import pytest
from genlab_core.rendering.word_animator import (
    _HAS_TEXT_OPTIMIZER,
    FONT_SIZE_CONFIG,
    SAFE_TOP,
    WordByWordAnimator,
    calculate_optimal_font_size,
    calculate_safe_position,
)


def test_text_optimizer_in_tree():
    """The flag MUST be True — flipping it back to False means someone
    re-introduced the regression by removing the inlined optimizer."""
    assert _HAS_TEXT_OPTIMIZER is True


def test_font_size_config_has_all_four_text_types():
    """The recovered schema declares 4 text_types. Test pins them so a
    rename doesn't silently break callers."""
    assert set(FONT_SIZE_CONFIG.keys()) == {"hook", "body", "caption", "credit"}


class TestCalculateOptimalFontSize:
    """Length-aware shrinking is the whole point of the optimizer."""

    def test_short_hook_gets_max_size(self):
        # 4 chars, 1 word → ideal_chars=15 path → max_size=160
        assert calculate_optimal_font_size("Big!", "hook") == 160

    def test_long_hook_shrinks_to_min_size(self):
        # 70 chars → over overflow_chars=40 → char_factor=0 → base=min=120
        text = "x" * 70
        size = calculate_optimal_font_size(text, "hook")
        assert size == 120

    def test_long_caption_stays_at_42(self):
        """Caption font is fixed at 42px regardless of length — that's the
        whole point of the 'caption' text_type. Pre-fix this returned
        160px because text_type="hook" was passed."""
        text = "Analyze earnings and update your investment thesis with Codex"
        size = calculate_optimal_font_size(text, "caption")
        assert size == 42

    def test_body_intermediate_length_returns_intermediate_size(self):
        # 80 chars, between ideal=40 and overflow=180
        text = "x" * 80
        size = calculate_optimal_font_size(text, "body")
        assert 56 <= size <= 72

    def test_credit_always_returns_34(self):
        for text in ["X", "x" * 30, "x" * 100]:
            assert calculate_optimal_font_size(text, "credit") == 34


class TestCalculateSafePosition:
    """Caption text_type MUST anchor at the bottom strip, NOT the top
    hook position — the pre-fix bug had captions starting at y=350 and
    stacking down to y=1974 (over the canvas)."""

    def test_caption_anchors_bottom_strip(self):
        pos = calculate_safe_position("any text", "caption")
        # Bottom strip: should land near SAFE_BOTTOM_Y=1600, not SAFE_TOP+100=350
        assert pos.y >= 1400  # roughly bottom third
        assert pos.y + pos.height <= 1920

    def test_hook_anchors_top(self):
        pos = calculate_safe_position("any text", "hook")
        # Hook: top region
        assert pos.y == SAFE_TOP + 100  # 350
        assert pos.y < 500

    def test_credit_anchors_bottom_right(self):
        pos = calculate_safe_position("Inc.", "credit")
        assert pos.x > 1080 // 2  # right half
        assert pos.y > 1400  # bottom

    def test_all_positions_within_canvas(self):
        for text_type in ("hook", "body", "caption", "credit"):
            pos = calculate_safe_position("test", text_type)
            assert pos.x >= 0
            assert pos.y >= 0
            assert pos.x + pos.width <= 1080
            assert pos.y + pos.height <= 1920


class TestBuildAnimatedFiltersFitsCanvas:
    """The end-to-end signal: rendered_bottom_y returned by
    build_animated_filters MUST stay within the canvas for every
    text_type at every reasonable length.

    Pre-fix: "Analyze earnings and update your investment thesis with Codex"
    + text_type="hook" + 160px fallback → rendered_bottom = 1974 > 1920.
    """

    LONG_CAPTION = (
        "Analyze earnings and update your investment thesis with Codex "
        "running data analysis on multiple companies."
    )
    SHORT_HOOK = "Wall Street is panicking"
    LONG_HOOK = "Wall Street analysts won't like this Codex demo from OpenAI today"

    def test_long_caption_fits_canvas_with_caption_type(self):
        """The user-shared screenshot text + text_type='caption' must fit."""
        animator = WordByWordAnimator(font_path=None)
        filters, duration, rendered_bottom = animator.build_animated_filters(
            self.LONG_CAPTION,
            text_type="caption",
        )
        # rendered_bottom is the y-coord of the last line's bottom edge.
        # Must stay within the 1920px canvas.
        assert rendered_bottom <= 1920, (
            f"caption rendered_bottom={rendered_bottom} > 1920 canvas — the "
            f"text_optimizer regression has re-appeared. See "
            f"[[session-2026-06-13-render-audit-findings]]"
        )

    def test_long_caption_fits_canvas_with_hook_type(self):
        """Even with text_type='hook' (legacy callers), the shrink-floor
        algorithm should prevent overflow."""
        animator = WordByWordAnimator(font_path=None)
        filters, duration, rendered_bottom = animator.build_animated_filters(
            self.LONG_CAPTION,
            text_type="hook",
        )
        assert rendered_bottom <= 1920, (
            f"hook rendered_bottom={rendered_bottom} > 1920 canvas — "
            f"calculate_optimal_font_size's max_lines shrink-floor failed"
        )

    def test_short_hook_fits_canvas(self):
        animator = WordByWordAnimator(font_path=None)
        _filters, _duration, rendered_bottom = animator.build_animated_filters(
            self.SHORT_HOOK,
            text_type="hook",
        )
        assert rendered_bottom <= 1920

    def test_long_hook_fits_canvas(self):
        animator = WordByWordAnimator(font_path=None)
        _filters, _duration, rendered_bottom = animator.build_animated_filters(
            self.LONG_HOOK,
            text_type="hook",
        )
        assert rendered_bottom <= 1920

    @pytest.mark.parametrize(
        "text_type,text",
        [
            ("hook", "Short hook"),
            ("hook", "Slightly longer hook with more words to fit"),
            ("body", "Body text " * 5),
            ("body", "Body text " * 20),
            ("caption", "Short caption"),
            ("caption", "Whisper caption " * 8),
            ("credit", "Inc. 2026"),
        ],
    )
    def test_various_inputs_all_fit(self, text_type, text):
        """Coverage matrix: every text_type at every reasonable length
        must produce a layout that fits the canvas. If any of these
        fail, the optimizer's shrink-floor is broken for that combination."""
        animator = WordByWordAnimator(font_path=None)
        _filters, _duration, rendered_bottom = animator.build_animated_filters(
            text,
            text_type=text_type,
        )
        assert rendered_bottom <= 1920, (
            f"{text_type}: text={text!r} produced rendered_bottom={rendered_bottom}"
        )
