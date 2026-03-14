"""Tests for video_content_writer."""
import pytest
from unittest.mock import MagicMock

from genlab_core.writing.video_content_writer import write_video_content, NICHE_VOICE


def _make_video(title="Bam Adebayo drops 83 points", channel="ESPN"):
    return {
        "video_id": "abc123",
        "title": title,
        "channel_name": channel,
        "view_count": 500000,
        "age_hours": 3,
        "view_velocity": 166666,
        "description_snippet": "Historic performance from Bam Adebayo",
        "tags": ["nba", "sports", "highlights"],
    }


def _make_llm(response: str):
    client = MagicMock()
    client.complete.return_value = response
    return client


class TestVideoContentWriter:
    def test_generates_all_platform_fields(self):
        llm = _make_llm(
            '{"hook":"Bam just dropped 83","instagram_caption":"Historic night '
            '#Sports","twitter_content":"83 points","youtube_content":"Did Bam just?",'
            '"facebook_content":"83 points tonight"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert "hook" in result
        assert "instagram_caption" in result
        assert "twitter_content" in result
        assert "youtube_content" in result
        assert "facebook_content" in result

    def test_hook_truncated_to_60_chars(self):
        long_hook = "A" * 100
        llm = _make_llm(
            f'{{"hook":"{long_hook}","instagram_caption":"x",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert len(result["hook"]) <= 60

    def test_fallback_on_llm_failure(self):
        llm = _make_llm("not valid json at all")
        result = write_video_content(_make_video(title="Short title"), "sports", llm)
        assert "hook" in result
        assert result["hook"] == "Short title"

    def test_fallback_truncates_long_title(self):
        long_title = "A" * 100
        llm = _make_llm("invalid")
        result = write_video_content(_make_video(title=long_title), "sports", llm)
        assert len(result["hook"]) <= 60
        assert result["hook"].endswith("...")

    def test_niche_voice_defined_for_all_channels(self):
        for niche in ["gaming", "sports", "movies", "anime", "ai_news"]:
            assert niche in NICHE_VOICE
            voice = NICHE_VOICE[niche]
            assert "account" in voice
            assert "style" in voice
            assert "hashtags" in voice
            assert len(voice["hashtags"]) >= 3

    def test_existing_hooks_passed_to_llm(self):
        llm = _make_llm(
            '{"hook":"New hook","instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(
            _make_video(), "sports", llm,
            existing_hooks=["Old hook 1", "Old hook 2"],
        )
        # Verify the LLM was called with system prompt containing existing hooks
        call_args = llm.complete.call_args
        assert "Old hook 1" in call_args.kwargs.get("system", "")

    def test_instagram_hashtags_added_if_missing(self):
        llm = _make_llm(
            '{"hook":"Test","instagram_caption":"No hashtags here",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert "#Sports" in result["instagram_caption"]

    def test_handles_markdown_code_fences(self):
        llm = _make_llm(
            '```json\n{"hook":"Works","instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}\n```'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert result["hook"] == "Works"
