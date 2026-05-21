"""Tests for SpliceReel writing strategy."""

from unittest.mock import MagicMock, patch

import pytest
from sr_strategies.writing import (
    MovieWritingStrategy,
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
                    "cta_library": ["follow for daily cinema moments"],
                    "hashtag_pool": ["#cinema", "#film", "#movies", "#SpliceReel"],
                    "hashtags_per_post": 4,
                    "franchise_hashtags": {"MCU": "#MCU", "DC": "#DC"},
                },
                "platforms": {},
            }
        )
    )
    with patch("sr_strategies.writing.NICHE_ROOT", tmp_path):
        s = MovieWritingStrategy()
        s._ensure_config()
        yield s


class TestWriting:
    def test_caption_includes_hook_and_summary(self, strategy):
        story = {
            "film_title": "Thunderbolts",
            "summary": "A team of anti-heroes assembles.",
            "content": {"hook": "Marvel just changed everything"},
        }
        result = strategy._write_story(story)
        caption = result["content"]["caption"]
        assert "Marvel just changed everything" in caption
        assert "anti-heroes" in caption

    def test_franchise_hashtag_included(self, strategy):
        story = {
            "film_title": "Spider-Man 4",
            "summary": "New adventure",
            "franchise": "MCU",
            "content": {"hook": "Your friendly neighbourhood..."},
        }
        result = strategy._write_story(story)
        caption = result["content"]["caption"]
        assert "#MCU" in caption

    def test_platform_content_generated(self, strategy):
        story = {
            "film_title": "Oppenheimer",
            "summary": "A biopic about...",
            "content": {},
        }
        result = strategy._write_story(story)
        content = result["content"]
        assert content["written"] is True
        assert "instagram" in content
        assert "youtube" in content
        assert "x_twitter" in content

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
    def test_execute_tracks_stats(self, strategy):
        context = {
            "stories": [
                {"film_title": "Film A", "summary": "...", "content": {}},
                {"film_title": "Film B", "summary": "...", "content": {}},
            ]
        }
        result = strategy.execute(context)
        assert result["run_stats"]["content_writing"]["written_count"] == 2
        assert result["run_stats"]["content_writing"]["status"] == "template_based"


class TestLLMWriting:
    def test_execute_template_fallback_without_api_key(self, strategy, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        ctx = {"stories": [{"film_title": "Test Film", "summary": "...", "content": {}}]}
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
                "hook": "Nolan does it again",
                "instagram_caption": "Incredible #Cinema",
                "twitter_content": "This movie",
                "youtube_content": "Is this the best film?",
                "facebook_content": "Film fans rejoice",
            }

            ctx = {
                "stories": [
                    {
                        "film_title": "Interstellar 2",
                        "summary": "...",
                        "story_id": "s1",
                        "content": {},
                    }
                ]
            }
            result = strategy.execute(ctx)

        assert result["run_stats"]["content_writing"]["status"] == "llm"
        assert result["run_stats"]["content_writing"]["llm_count"] == 1
        story = ctx["stories"][0]
        assert story["content"]["hook"] == "Nolan does it again"
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
            ctx = {"stories": [{"film_title": "Film X", "summary": "...", "content": {}}]}
            result = strategy.execute(ctx)

        assert result["run_stats"]["content_writing"]["written_count"] == 1
        story = ctx["stories"][0]
        assert story["content"]["written"] is True


class TestBuildExtraInstructions:
    def test_banned_phrases(self):
        cfg = {"banned_phrases": ["cinema is back and", "fans can't believe"]}
        result = _build_extra_instructions(cfg)
        assert "BANNED PHRASES" in result
        assert "cinema is back and" in result

    def test_empty_config(self):
        assert _build_extra_instructions({}) == ""


class TestStoryToVideoDict:
    def test_film_title_used(self):
        story = {"film_title": "Avatar 3", "title": "Fallback", "story_id": "s1", "summary": "Wow"}
        video = _story_to_video_dict(story)
        assert video["title"] == "Avatar 3"

    def test_fallback_to_title(self):
        story = {"title": "Untitled Film", "story_id": "s1", "summary": ""}
        video = _story_to_video_dict(story)
        assert video["title"] == "Untitled Film"
