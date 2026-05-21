"""Tests for ClutchWire writing strategy."""

from unittest.mock import MagicMock, patch

import pytest
from cw_strategies.writing import (
    SportWritingStrategy,
    _build_extra_instructions,
    _story_to_video_dict,
)


@pytest.fixture
def strategy():
    return SportWritingStrategy()


def _make_story(**kwargs):
    base = {
        "title": "Lakers beat Celtics 110-105",
        "summary": "LeBron scored 35 points in a thrilling overtime victory",
        "teams": ["Lakers", "Celtics"],
        "players": ["LeBron"],
        "sport": "basketball",
        "story_id": "story-001",
        "content": {},
    }
    base.update(kwargs)
    return base


class TestWriteStory:
    def test_caption_generated(self, strategy):
        strategy._ensure_config()
        story = _make_story()
        result = strategy._write_story(story)
        assert result["content"]["caption"]
        assert result["content"]["written"] is True

    def test_platform_content_created(self, strategy):
        strategy._ensure_config()
        story = _make_story()
        result = strategy._write_story(story)
        for platform in ["instagram", "youtube", "x_twitter", "facebook", "tiktok", "threads"]:
            assert platform in result["content"], f"Missing platform content: {platform}"

    def test_youtube_title_within_limit(self, strategy):
        strategy._ensure_config()
        story = _make_story()
        result = strategy._write_story(story)
        yt_title = result["content"]["youtube"]["title"]
        assert len(yt_title) <= 40


class TestWriteExecute:
    def test_execute_writes_all_stories(self, strategy):
        ctx = {"stories": [_make_story(), _make_story()]}
        result = strategy.execute(ctx)
        assert result["run_stats"]["content_writing"]["written_count"] == 2

    def test_execute_empty_stories(self, strategy):
        ctx = {"stories": []}
        result = strategy.execute(ctx)
        assert result["run_stats"]["content_writing"]["status"] == "no_stories"

    def test_execute_template_fallback_without_api_key(self, strategy, monkeypatch):
        """Without ANTHROPIC_API_KEY, falls back to template writing."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        ctx = {"stories": [_make_story()]}
        result = strategy.execute(ctx)
        assert result["run_stats"]["content_writing"]["status"] == "template_based"
        assert result["run_stats"]["content_writing"]["llm_count"] == 0

    def test_execute_uses_llm_when_api_key_set(self, strategy, monkeypatch):
        """With ANTHROPIC_API_KEY, uses LLM path."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_client = MagicMock()

        with (
            patch("genlab_core.writing.llm_client.AnthropicLLMClient", return_value=mock_client),
            patch(
                "genlab_core.cost.model_router.get_model", return_value="claude-haiku-4-5-20251001"
            ),
            patch("genlab_core.writing.video_content_writer.write_video_content") as mock_wvc,
        ):
            mock_wvc.return_value = {
                "hook": "LLM generated hook",
                "instagram_caption": "Great game #Sports",
                "twitter_content": "What a game",
                "youtube_content": "Did the Lakers just?",
                "facebook_content": "Lakers stunning win",
            }

            ctx = {"stories": [_make_story()]}
            result = strategy.execute(ctx)

        assert result["run_stats"]["content_writing"]["status"] == "llm"
        assert result["run_stats"]["content_writing"]["llm_count"] == 1
        story = ctx["stories"][0]
        assert story["content"]["hook"] == "LLM generated hook"
        assert story["content"]["written_by"] == "llm"

    def test_execute_llm_failure_falls_back_to_template(self, strategy, monkeypatch):
        """When LLM fails, falls back to template writing."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        mock_client = MagicMock()

        with (
            patch("genlab_core.writing.llm_client.AnthropicLLMClient", return_value=mock_client),
            patch(
                "genlab_core.cost.model_router.get_model", return_value="claude-haiku-4-5-20251001"
            ),
            patch(
                "genlab_core.writing.video_content_writer.write_video_content",
                side_effect=Exception("API error"),
            ),
        ):
            ctx = {"stories": [_make_story()]}
            result = strategy.execute(ctx)

        # Should have fallen back successfully
        assert result["run_stats"]["content_writing"]["written_count"] == 1
        story = ctx["stories"][0]
        assert story["content"]["written"] is True


class TestBuildExtraInstructions:
    def test_banned_phrases(self):
        cfg = {"banned_phrases": ["phrase A", "phrase B"]}
        result = _build_extra_instructions(cfg)
        assert "BANNED PHRASES" in result
        assert "phrase A" in result
        assert "phrase B" in result

    def test_hook_examples(self):
        cfg = {"hook_examples": ["Example hook 1"]}
        result = _build_extra_instructions(cfg)
        assert "HOOK EXAMPLES" in result
        assert "Example hook 1" in result

    def test_tone_notes(self):
        cfg = {"tone_notes": "Be exciting and specific."}
        result = _build_extra_instructions(cfg)
        assert "TONE:" in result
        assert "Be exciting and specific." in result

    def test_empty_config(self):
        result = _build_extra_instructions({})
        assert result == ""


class TestStoryToVideoDict:
    def test_basic_conversion(self):
        story = _make_story()
        video = _story_to_video_dict(story)
        assert video["title"] == "Lakers beat Celtics 110-105"
        assert video["channel_name"] == ""  # no "source" in base story
        assert video["description_snippet"] == story["summary"][:300]

    def test_with_clip_index(self):
        story = _make_story(story_id="s1")
        clip_index = {"clips": {"s1": {"video_id": "yt-abc", "success": True}}}
        video = _story_to_video_dict(story, clip_index)
        assert video["video_id"] == "yt-abc"

    def test_without_clip_index(self):
        story = _make_story(story_id="s1", video_id="vid-xyz")
        video = _story_to_video_dict(story)
        assert video["video_id"] == "vid-xyz"
