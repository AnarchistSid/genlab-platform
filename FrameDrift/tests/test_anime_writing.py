"""Tests for FrameDrift writing strategy."""

from unittest.mock import MagicMock, patch

import pytest
from fd_strategies.writing import (
    AnimeWritingStrategy,
    _build_extra_instructions,
    _story_to_video_dict,
)


@pytest.fixture
def strategy(tmp_path):
    """Create strategy with test templates config."""
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "templates.yaml").write_text(
        yaml.dump(
            {
                "captions": {
                    "target_length": 300,
                    "cta_library": ["follow for daily anime moments"],
                    "hashtag_pool": ["#anime", "#manga", "#otaku", "#FrameDrift"],
                    "hashtags_per_post": 4,
                },
                "platforms": {},
            }
        )
    )
    with patch("fd_strategies.writing.NICHE_ROOT", tmp_path):
        s = AnimeWritingStrategy()
        s._ensure_config()
        yield s


def _make_story(**kwargs):
    base = {
        "title": "Jujutsu Kaisen S3 drops insane fight scene",
        "summary": "Gojo returns in an epic battle against Sukuna",
        "story_id": "anime-001",
        "content": {},
    }
    base.update(kwargs)
    return base


class TestTemplateWriting:
    def test_caption_generated(self, strategy):
        story = _make_story()
        result = strategy._write_story(story)
        assert result["content"]["caption"]
        assert result["content"]["written"] is True

    def test_platform_content_created(self, strategy):
        story = _make_story()
        result = strategy._write_story(story)
        for platform in ["instagram", "youtube", "x_twitter", "facebook", "tiktok", "threads"]:
            assert platform in result["content"], f"Missing: {platform}"

    def test_execute_empty_stories(self, strategy):
        ctx = {"stories": []}
        result = strategy.execute(ctx)
        assert result["run_stats"]["content_writing"]["status"] == "no_stories"

    def test_execute_writes_all_stories(self, strategy):
        ctx = {"stories": [_make_story(), _make_story()]}
        result = strategy.execute(ctx)
        assert result["run_stats"]["content_writing"]["written_count"] == 2


class TestLLMWriting:
    def test_execute_template_fallback_without_api_key(self, strategy, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        ctx = {"stories": [_make_story()]}
        result = strategy.execute(ctx)
        assert result["run_stats"]["content_writing"]["status"] == "template_based"
        assert result["run_stats"]["content_writing"]["llm_count"] == 0

    def test_execute_uses_llm_when_api_key_set(self, strategy, monkeypatch):
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
                "hook": "Gojo just went limitless",
                "instagram_caption": "Best anime scene #Anime",
                "twitter_content": "JJK S3 is insane",
                "youtube_content": "Did Gojo just?",
                "facebook_content": "Anime fans this is it",
            }

            ctx = {"stories": [_make_story()]}
            result = strategy.execute(ctx)

        assert result["run_stats"]["content_writing"]["status"] == "llm"
        assert result["run_stats"]["content_writing"]["llm_count"] == 1
        story = ctx["stories"][0]
        assert story["content"]["hook"] == "Gojo just went limitless"
        assert story["content"]["written_by"] == "llm"

    def test_execute_llm_failure_falls_back(self, strategy, monkeypatch):
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

        assert result["run_stats"]["content_writing"]["written_count"] == 1
        story = ctx["stories"][0]
        assert story["content"]["written"] is True


class TestBuildExtraInstructions:
    def test_full_config(self):
        cfg = {
            "banned_phrases": ["anime fans are shaking"],
            "hook_examples": ["Studio MAPPA outdid themselves"],
            "tone_notes": "Passionate otaku energy.",
        }
        result = _build_extra_instructions(cfg)
        assert "BANNED PHRASES" in result
        assert "anime fans are shaking" in result
        assert "HOOK EXAMPLES" in result
        assert "TONE:" in result

    def test_empty_config(self):
        assert _build_extra_instructions({}) == ""


class TestStoryToVideoDict:
    def test_basic_conversion(self):
        story = _make_story(source="Crunchyroll")
        video = _story_to_video_dict(story)
        assert video["title"] == "Jujutsu Kaisen S3 drops insane fight scene"
        assert video["channel_name"] == "Crunchyroll"
        assert video["video_id"] == "anime-001"

    def test_with_clip_index(self):
        story = _make_story(story_id="a1")
        clip_index = {"clips": {"a1": {"video_id": "yt-jjk", "success": True}}}
        video = _story_to_video_dict(story, clip_index)
        assert video["video_id"] == "yt-jjk"
