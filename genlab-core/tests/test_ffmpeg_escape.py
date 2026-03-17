"""Tests for FFmpeg drawtext escaping and smart-quote normalization.

Covers Bug 1 (Sprint 65): apostrophes in hook text crashing FFmpeg drawtext.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from genlab_core.media.ffmpeg_utils import escape_drawtext

# ── escape_drawtext tests ────────────────────────────────────────


class TestEscapeDrawtextApostrophe:
    """Apostrophes must become Unicode RIGHT SINGLE QUOTATION MARK (U+2019)."""

    def test_escape_drawtext_apostrophe(self):
        result = escape_drawtext("player's move")
        assert "'" not in result
        assert "\u2019" in result
        assert result == "player\u2019s move"

    def test_escape_drawtext_multiple_apostrophes(self):
        result = escape_drawtext("it's the player's best day's end")
        assert result.count("\u2019") == 3
        assert "'" not in result


class TestEscapeDrawtextColon:
    """Colons must be backslash-escaped for FFmpeg filter_complex."""

    def test_escape_drawtext_colon(self):
        result = escape_drawtext("time: 12:30")
        assert ":" not in result.replace("\\:", "")
        assert "\\:" in result


class TestEscapeDrawtextBackslash:
    """Backslashes must be quadruple-escaped for filter_complex."""

    def test_escape_drawtext_backslash(self):
        result = escape_drawtext("path\\to\\file")
        # Original has 2 backslashes; each becomes 4 (\\\\)
        assert "\\\\" in result
        # No raw single backslash should remain (other than escaped sequences)
        assert "path" in result
        assert "file" in result


class TestEscapeDrawtextEmpty:
    """Empty string must pass through unchanged."""

    def test_escape_drawtext_empty(self):
        assert escape_drawtext("") == ""


class TestEscapeDrawtextCombined:
    """Multiple special characters in one string."""

    def test_escape_drawtext_combined(self):
        result = escape_drawtext("it's 10:30 [live]; go\\home")
        # Apostrophe -> U+2019
        assert "'" not in result
        assert "\u2019" in result
        # Colon escaped
        assert "\\:" in result
        # Brackets escaped
        assert "\\[" in result
        assert "\\]" in result
        # Semicolon escaped
        assert "\\;" in result
        # Backslash escaped (quadrupled)
        assert "\\\\" in result

    def test_plain_text_unchanged(self):
        """Text with no special chars passes through unchanged."""
        assert escape_drawtext("hello world") == "hello world"


# ── Smart-quote normalization in hook generators ─────────────────


class TestSmartQuoteInHookNormalized:
    """LLM-generated hooks with smart quotes are normalized to ASCII before storage."""

    def test_smart_quote_in_hook_normalized_video_content_writer(self):
        """write_video_content normalizes smart quotes in the hook."""
        from genlab_core.writing.video_content_writer import write_video_content

        # LLM returns a hook with smart quotes
        fake_response = json.dumps({
            "hook": "Gojo\u2019s return breaks everything",
            "instagram_caption": "test caption #AI",
            "twitter_content": "test",
            "youtube_content": "test?",
            "facebook_content": "test question",
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = fake_response

        video = {
            "title": "Gojo Returns",
            "channel_name": "TestChannel",
            "view_count": 100000,
            "view_velocity": 5000,
            "tags": ["anime"],
            "description_snippet": "Gojo is back",
        }

        result = write_video_content(
            video=video,
            niche_id="anime",
            llm_client=mock_client,
        )

        hook = result["hook"]
        # Smart right single quote should be normalized to ASCII apostrophe
        assert "\u2019" not in hook
        assert "'" in hook
        assert "Gojo's" in hook

    def test_smart_quote_in_hook_normalized_llm_hook_generator(self):
        """generate_hook normalizes smart quotes from the LLM response."""
        import importlib
        import sys


        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text="Jokic\u2019s 40-point Game 7 masterclass")
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        # Create a fake anthropic module since the real import is lazy (inside function)
        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic.return_value = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": fake_anthropic}):
                # Reload to pick up the patched module
                import genlab_core.writing.llm_hook_generator as mod
                importlib.reload(mod)
                hook = mod.generate_hook(
                    story={"title": "Jokic drops 40", "summary": "Big game"},
                    niche_id="sports",
                )

        assert hook is not None
        assert "\u2019" not in hook
        assert "'" in hook
        assert "Jokic's" in hook

    def test_left_smart_quote_normalized(self):
        """Left single smart quote U+2018 is also normalized."""
        from genlab_core.writing.video_content_writer import write_video_content

        fake_response = json.dumps({
            "hook": "\u2018Elden Ring\u2019 hits 5M sales",
            "instagram_caption": "test #Gaming",
            "twitter_content": "test",
            "youtube_content": "test?",
            "facebook_content": "test",
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = fake_response

        result = write_video_content(
            video={"title": "Elden Ring", "channel_name": "Test",
                   "view_count": 1000, "view_velocity": 100,
                   "tags": [], "description_snippet": ""},
            niche_id="gaming",
            llm_client=mock_client,
        )

        assert "\u2018" not in result["hook"]
        assert "\u2019" not in result["hook"]

    def test_double_smart_quotes_normalized(self):
        """Double smart quotes U+201C/U+201D are normalized."""
        from genlab_core.writing.video_content_writer import write_video_content

        fake_response = json.dumps({
            "hook": "\u201cNew era\u201d for competitive gaming",
            "instagram_caption": "test #Gaming",
            "twitter_content": "test",
            "youtube_content": "test?",
            "facebook_content": "test",
        })

        mock_client = MagicMock()
        mock_client.complete.return_value = fake_response

        result = write_video_content(
            video={"title": "Competitive", "channel_name": "Test",
                   "view_count": 1000, "view_velocity": 100,
                   "tags": [], "description_snippet": ""},
            niche_id="gaming",
            llm_client=mock_client,
        )

        assert "\u201c" not in result["hook"]
        assert "\u201d" not in result["hook"]
        assert '"' in result["hook"]
