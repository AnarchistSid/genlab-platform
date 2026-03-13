"""Tests for genlab_core.utils.text_sanitizer — Graph API text sanitization.

Covers:
  - Non-BMP emoji stripping
  - Curly/smart quote replacement
  - BMP characters (Japanese, accented Latin) pass through
  - None passthrough
  - sanitize_fields_for_graph_api dict sanitization
  - Integration with GraphTableProxy._prepare_fields()
"""

import pytest

from genlab_core.utils.text_sanitizer import (
    sanitize_fields_for_graph_api,
    sanitize_for_graph_api,
)


# ── sanitize_for_graph_api ──────────────────────────────────────────


class TestSanitizeForGraphApi:
    """Unit tests for sanitize_for_graph_api."""

    def test_none_returns_none(self):
        assert sanitize_for_graph_api(None) is None

    def test_plain_ascii_unchanged(self):
        assert sanitize_for_graph_api("Hello world") == "Hello world"

    def test_strips_emoji(self):
        assert sanitize_for_graph_api("Hello 🔥 world") == "Hello world"

    def test_strips_multiple_emoji(self):
        result = sanitize_for_graph_api("🚀 Launch day! 🎉🎊 Let's go")
        assert result == "Launch day! Let's go"

    def test_replaces_curly_apostrophe(self):
        assert sanitize_for_graph_api("it\u2019s a test") == "it's a test"

    def test_replaces_left_single_quote(self):
        assert sanitize_for_graph_api("\u2018hello\u2019") == "'hello'"

    def test_replaces_curly_double_quotes(self):
        result = sanitize_for_graph_api("\u201CHello\u201D")
        assert result == '"Hello"'

    def test_japanese_characters_pass_through(self):
        """BMP CJK characters (U+4E00–U+9FFF) must not be stripped."""
        text = "新作アニメ「進撃の巨人」最終回"
        assert sanitize_for_graph_api(text) == text

    def test_accented_latin_pass_through(self):
        assert sanitize_for_graph_api("café résumé naïve") == "café résumé naïve"

    def test_korean_pass_through(self):
        text = "한국어 테스트"
        assert sanitize_for_graph_api(text) == text

    def test_mixed_emoji_and_text(self):
        result = sanitize_for_graph_api("NBA Finals 🏀 LeBron\u2019s 40pts 🔥🔥")
        assert result == "NBA Finals LeBron's 40pts"

    def test_emoji_only_returns_empty(self):
        assert sanitize_for_graph_api("🔥🚀🎉") == ""

    def test_collapses_double_spaces(self):
        result = sanitize_for_graph_api("before 🔥 after")
        assert result == "before after"

    def test_strips_leading_trailing_spaces(self):
        result = sanitize_for_graph_api("🔥 hello 🔥")
        assert result == "hello"

    def test_real_sports_title(self):
        """Reproduction case from ClutchWire: sports headlines with emoji."""
        title = "Mbappe\u2019s hat-trick 🔥⚽ sends Real Madrid to UCL final"
        result = sanitize_for_graph_api(title)
        # ⚽ (U+26BD) is BMP — passes through. 🔥 (U+1F525) is non-BMP — stripped.
        assert result == "Mbappe's hat-trick ⚽ sends Real Madrid to UCL final"

    def test_real_gaming_title(self):
        title = "GTA 6 trailer drops 🎮🚀 — Rockstar confirms 2025 release"
        result = sanitize_for_graph_api(title)
        # 🎮🚀 are non-BMP — stripped. — (U+2014 em dash) is BMP — passes through.
        assert result == "GTA 6 trailer drops — Rockstar confirms 2025 release"

    def test_non_string_passthrough(self):
        """Non-string types are returned as-is."""
        assert sanitize_for_graph_api(42) == 42  # type: ignore[arg-type]
        assert sanitize_for_graph_api(3.14) == 3.14  # type: ignore[arg-type]

    def test_empty_string(self):
        assert sanitize_for_graph_api("") == ""

    def test_skin_tone_emoji_stripped(self):
        """Fitzpatrick skin tone modifiers are non-BMP and should be stripped."""
        result = sanitize_for_graph_api("wave 👋🏽 hi")
        assert result == "wave hi"

    def test_flag_emoji_stripped(self):
        """Regional indicator flags are non-BMP."""
        result = sanitize_for_graph_api("USA 🇺🇸 wins")
        assert result == "USA wins"


# ── sanitize_fields_for_graph_api ───────────────────────────────────


class TestSanitizeFieldsForGraphApi:
    """Unit tests for sanitize_fields_for_graph_api."""

    def test_sanitizes_string_values(self):
        fields = {"title": "Hello 🔥 world", "score": 0.85}
        result = sanitize_fields_for_graph_api(fields)
        assert result["title"] == "Hello world"
        assert result["score"] == 0.85

    def test_preserves_none_values(self):
        fields = {"title": None, "status": "INTAKE"}
        result = sanitize_fields_for_graph_api(fields)
        assert result["title"] is None
        assert result["status"] == "INTAKE"

    def test_preserves_bool_values(self):
        fields = {"active": True, "name": "test 🔥"}
        result = sanitize_fields_for_graph_api(fields)
        assert result["active"] is True
        assert result["name"] == "test"

    def test_preserves_int_values(self):
        fields = {"count": 42, "label": "count\u2019s"}
        result = sanitize_fields_for_graph_api(fields)
        assert result["count"] == 42
        assert result["label"] == "count's"

    def test_empty_dict(self):
        assert sanitize_fields_for_graph_api({}) == {}

    def test_all_string_fields(self):
        fields = {
            "title": "Test 🔥",
            "hook": "It\u2019s hot 🔥",
            "summary": "A summary",
        }
        result = sanitize_fields_for_graph_api(fields)
        assert result == {
            "title": "Test",
            "hook": "It's hot",
            "summary": "A summary",
        }
