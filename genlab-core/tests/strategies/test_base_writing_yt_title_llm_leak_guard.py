"""Regression pin for the 2026-06-21 YouTube title LLM-error-leak guard.

Production bug (caught via R7 browser verification 2026-06-20): SpliceReel
YouTube short ``QrNDe-egDrg`` shipped with title
``"I need more story details to write an effective hook...."`` — a literal
LLM meta-response (asking for clarification instead of producing a hook)
that got published verbatim as the YouTube video title.

Same content's IG hook + FB hook were correct, so the failure was isolated
to the YouTube title path:

  ``content["youtube"] = {"title": hook if hook else ..., ...}``

at ``base_writing.py:362``. The hook string contained an LLM error
response; rule 10 of HookValidator now detects it and the YT title path
falls back to ``story["title"]`` so the published video has a real title
instead of a literal AI refusal.

These tests exercise the REAL ``_write_story_llm`` method (not a
replicated copy) by mocking the LLM client + intercepting the result
shape. That way if anyone changes the real method, the test breaks.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_strategy():
    """Build a BaseWritingStrategy stub with minimal config to call
    ``_write_story_llm`` directly."""
    from genlab_core.strategies.base_writing import BaseWritingStrategy

    s = BaseWritingStrategy.__new__(BaseWritingStrategy)
    s.niche_id = "movies"
    s._niche_id = "movies"  # _write_story_llm reads _niche_id (private)
    s._templates = {}
    s._writing_config = {}
    s._writing_cfg = {}
    return s


def _make_story(title: str = "Pressure - International Trailer 2") -> dict:
    return {
        "title": title,
        "story_id": "s-001",
        "source_url": "https://example.com/pressure",
    }


class TestYouTubeTitleLLMErrorLeakGuard:
    """Pin the 2026-06-21 fix that intercepts LLM error responses before
    they ship as YouTube video titles.
    """

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_llm_error_hook_falls_back_to_story_title(self, mock_write_content):
        """The exact prod symptom: LLM returns a meta-response as the hook;
        the YT title path MUST fall back to ``story["title"]`` instead of
        publishing the LLM error string."""
        # LLM returns the exact 2026-06-20 prod-leak string
        mock_write_content.return_value = {
            "hook": "I need more story details to write an effective hook....",
            "instagram_caption": "A submarine crew trapped 300 meters down.",
            "facebook_content": "Trailer just dropped",
            "youtube_content": "Pressure trailer breakdown",
            "twitter_content": "Pressure!",
            "tiktok_content": "Pressure",
            "threads_content": "Pressure threads",
        }

        s = _make_strategy()
        story = _make_story(title="Pressure - International Trailer 2")
        s._write_story_llm(story, MagicMock(), "", [], None)

        yt_title = story["content"]["youtube"]["title"]
        assert "I need more story details" not in yt_title, (
            f"YouTube title MUST NOT contain the LLM error string. Got: {yt_title!r}. "
            f"Pre-fix this WAS the published title for SpliceReel YT short QrNDe-egDrg."
        )
        # Falls back to story.title
        assert yt_title == "Pressure - International Trailer 2"

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_legitimate_hook_passes_through_unchanged(self, mock_write_content):
        """A clean hook (no LLM-error pattern) must reach the YT title
        field unmodified. Pin against over-eager fallback."""
        mock_write_content.return_value = {
            "hook": "Pressure: 300m down, oxygen running out",
            "instagram_caption": "A submarine crew trapped 300 meters down.",
            "facebook_content": "Trailer just dropped",
            "youtube_content": "Pressure trailer breakdown",
            "twitter_content": "Pressure!",
            "tiktok_content": "Pressure",
            "threads_content": "Pressure threads",
        }

        s = _make_strategy()
        story = _make_story()
        s._write_story_llm(story, MagicMock(), "", [], None)

        # Clean hook used as YT title verbatim (unchanged behavior)
        assert story["content"]["youtube"]["title"] == "Pressure: 300m down, oxygen running out"

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_llm_cannot_response_also_caught(self, mock_write_content):
        """Other LLM error patterns (not just 'I need more...') are also
        caught. Headline test covers 'I need more'; this one pins 'I cannot'."""
        mock_write_content.return_value = {
            "hook": "I cannot generate a hook for this story",
            "instagram_caption": "Caption A",
            "facebook_content": "FB",
            "youtube_content": "YT desc",
            "twitter_content": "T",
            "tiktok_content": "TK",
            "threads_content": "TH",
        }

        s = _make_strategy()
        story = _make_story(title="Real Story Title")
        s._write_story_llm(story, MagicMock(), "", [], None)

        # Fallback fires for the I-cannot pattern too
        assert "I cannot" not in story["content"]["youtube"]["title"]
        assert story["content"]["youtube"]["title"] == "Real Story Title"

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_yt_title_truncated_to_100_chars(self, mock_write_content):
        """YouTube title hard limit is 100 chars. When falling back to
        story.title and the title is longer than 100 chars, truncate."""
        long_title = "X" * 250  # 250-char story title (way over YT's 100 limit)
        mock_write_content.return_value = {
            "hook": "I need more story details to write an effective hook",
            "instagram_caption": "Cap",
            "facebook_content": "FB",
            "youtube_content": "YT",
            "twitter_content": "T",
            "tiktok_content": "TK",
            "threads_content": "TH",
        }

        s = _make_strategy()
        story = _make_story(title=long_title)
        s._write_story_llm(story, MagicMock(), "", [], None)

        yt_title = story["content"]["youtube"]["title"]
        assert len(yt_title) <= 100, (
            f"YouTube title must respect 100-char limit. Got {len(yt_title)} chars."
        )
        assert yt_title == "X" * 100

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_legitimate_hook_with_cannot_midstring_passes(self, mock_write_content):
        """Critical false-positive guard: a legitimate hook that happens
        to contain LLM-error keywords MID-string must NOT trip the fallback.
        Rule 10 is anchored at ``^`` for exactly this reason."""
        mock_write_content.return_value = {
            "hook": "Why I cannot stop watching this trailer",
            "instagram_caption": "Cap",
            "facebook_content": "FB",
            "youtube_content": "YT",
            "twitter_content": "T",
            "tiktok_content": "TK",
            "threads_content": "TH",
        }

        s = _make_strategy()
        story = _make_story()
        s._write_story_llm(story, MagicMock(), "", [], None)

        # Legitimate hook with mid-string "I cannot" preserved
        assert story["content"]["youtube"]["title"] == "Why I cannot stop watching this trailer"

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_fallback_uses_untitled_when_story_title_missing(self, mock_write_content):
        """Edge case: LLM error AND story has no title field. Fall back
        to the literal 'Untitled' so YouTube still gets SOMETHING (better
        than empty string or KeyError)."""
        mock_write_content.return_value = {
            "hook": "I need more context to proceed",
            "instagram_caption": "Cap",
            "facebook_content": "FB",
            "youtube_content": "YT",
            "twitter_content": "T",
            "tiktok_content": "TK",
            "threads_content": "TH",
        }

        s = _make_strategy()
        # Story with NO title field
        story = {"story_id": "s-no-title"}
        s._write_story_llm(story, MagicMock(), "", [], None)

        yt_title = story["content"]["youtube"]["title"]
        # Must be non-empty (better than blank)
        assert yt_title
        # Must not be the LLM error
        assert "I need more context" not in yt_title
