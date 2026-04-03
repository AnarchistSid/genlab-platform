"""Tests for render text overlays — hook text wrapping."""

from niches.gaming.stages.render_gaming_video import (
    HOOK_MAX_CHARS_PER_LINE,
    HOOK_MAX_TOTAL_CHARS,
    wrap_hook_text,
)


class TestWrapHookText:
    """Verify hook text wrapping for 9:16 safe zone."""

    def test_short_text_no_wrap(self):
        """Text under 32 chars stays on one line."""
        result = wrap_hook_text("this is short")
        assert "\n" not in result
        assert result == "this is short"

    def test_long_text_wraps_at_word_boundary(self):
        """Text over 32 chars gets word-wrapped."""
        hook = "this game just changed everything and nobody is ready for it"
        result = wrap_hook_text(hook)
        lines = result.split("\n")
        assert len(lines) >= 2
        for line in lines:
            assert len(line) <= HOOK_MAX_CHARS_PER_LINE

    def test_max_total_chars_enforced(self):
        """Text over 80 chars gets truncated before wrapping."""
        hook = "a" * 100 + " word"
        result = wrap_hook_text(hook)
        assert len(result.replace("\n", "")) <= HOOK_MAX_TOTAL_CHARS

    def test_word_boundary_truncation(self):
        """Truncation at 80 chars respects word boundaries."""
        hook = "word " * 20  # 100 chars
        result = wrap_hook_text(hook)
        total_chars = len(result.replace("\n", ""))
        assert total_chars <= HOOK_MAX_TOTAL_CHARS
        # Should not cut mid-word
        assert not result.replace("\n", " ").endswith("wor")

    def test_all_lines_fit_safe_zone(self):
        """Every line must be <= 32 chars for safe zone compliance."""
        hook = "the gaming community just went absolutely insane over this update"
        result = wrap_hook_text(hook)
        for line in result.split("\n"):
            assert len(line) <= HOOK_MAX_CHARS_PER_LINE, f"Line too long: '{line}'"

    def test_empty_string(self):
        result = wrap_hook_text("")
        assert result == ""

    def test_exactly_32_chars(self):
        hook = "a" * HOOK_MAX_CHARS_PER_LINE
        result = wrap_hook_text(hook)
        assert "\n" not in result
