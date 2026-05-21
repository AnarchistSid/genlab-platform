"""Tests for WriteGamingContent stage — all API calls mocked."""

import json
from unittest.mock import MagicMock, patch


def _make_story(title="Elden Ring", score=0.8):
    return {
        "title": title,
        "source": "steam_spike",
        "source_url": "https://store.steampowered.com/app/1245620",
        "score": score,
        "published_at": "2026-03-05T00:00:00Z",
        "summary": "Player count spiking",
        "steam_app_id": "1245620",
        "igdb_game_id": None,
        "developer": None,
    }


def _valid_llm_response():
    return json.dumps(
        {
            "hook": "Elden Ring players are losing their minds rn",
            "instagram": {
                "caption": "Something massive just happened in the Lands Between and the community is going wild. drop a 🎮 if you're playing this",
                "hashtags": ["#gaming", "#gamer", "#games", "#videogames"],
            },
            "youtube": {
                "title": "Did Elden Ring Just Change Everything?",
                "description": "The Elden Ring community is buzzing after a surprise update.",
            },
            "x_twitter": {
                "tweet": "Elden Ring is going absolutely wild right now",
                "hashtags": ["#gaming", "#gamer"],
            },
            "facebook": {
                "caption": "The Elden Ring community can't stop talking about this. What's your take on the latest update?",
            },
        }
    )


def _mock_settings(api_key=None):
    """Create a mock settings with the given api_key."""
    mock = MagicMock()
    mock.anthropic_api_key = api_key
    return mock


class TestSkipWithoutAPIKey:
    @patch("niches.gaming.stages.write_gaming_content.settings", _mock_settings(None))
    def test_skipped_when_no_api_key(self):
        """When ANTHROPIC_API_KEY is unset, context returned unchanged."""
        from niches.gaming.stages.write_gaming_content import WriteGamingContent

        stage = WriteGamingContent()
        stories = [_make_story()]
        context = {"stories": stories, "run_stats": {}}
        result = stage.execute(context)

        assert "content" not in result["stories"][0]
        assert result["run_stats"]["content_writing"]["status"] == "skipped_no_api_key"


class TestSuccessfulWrite:
    @patch("niches.gaming.stages.write_gaming_content.settings", _mock_settings("test-key"))
    @patch("niches.gaming.stages.write_gaming_content.anthropic")
    def test_successful_write(self, mock_anthropic_module):
        """Mock Anthropic API returning valid JSON, verify content populated."""
        from niches.gaming.stages.write_gaming_content import WriteGamingContent

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_valid_llm_response())]
        mock_client.messages.create.return_value = mock_response

        stage = WriteGamingContent()
        context = {"stories": [_make_story()], "run_stats": {}}
        result = stage.execute(context)

        content = result["stories"][0]["content"]
        assert content["hook"] == "Elden Ring players are losing their minds rn"
        assert "caption" in content["instagram"]
        assert len(content["instagram"]["hashtags"]) == 4
        assert result["run_stats"]["content_writing"]["written_count"] == 1


class TestYouTubeTitleValidation:
    @patch("niches.gaming.stages.write_gaming_content.settings", _mock_settings("test-key"))
    @patch("niches.gaming.stages.write_gaming_content.anthropic")
    def test_youtube_title_truncated(self, mock_anthropic_module):
        """If LLM returns title over 40 chars, it gets truncated."""
        from niches.gaming.stages.write_gaming_content import WriteGamingContent

        long_title_response = json.dumps(
            {
                "hook": "test hook",
                "instagram": {"caption": "test caption", "hashtags": ["#gaming"]},
                "youtube": {
                    "title": "Is This Really The Most Amazing Game Update Of All Time?",
                    "description": "test desc",
                },
                "x_twitter": {"tweet": "test tweet", "hashtags": ["#gaming"]},
                "facebook": {"caption": "test caption"},
            }
        )

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=long_title_response)]
        mock_client.messages.create.return_value = mock_response

        stage = WriteGamingContent()
        context = {"stories": [_make_story()], "run_stats": {}}
        result = stage.execute(context)

        yt_title = result["stories"][0]["content"]["youtube"]["title"]
        assert len(yt_title) <= 40
        assert yt_title.endswith("?")


class TestJSONParseFallback:
    @patch("niches.gaming.stages.write_gaming_content.settings", _mock_settings("test-key"))
    @patch("niches.gaming.stages.write_gaming_content.anthropic")
    def test_json_parse_failure_uses_fallback(self, mock_anthropic_module):
        """If LLM returns malformed JSON, fallback is used without crash."""
        from niches.gaming.stages.write_gaming_content import WriteGamingContent

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="This is not valid JSON at all {{{")]
        mock_client.messages.create.return_value = mock_response

        stage = WriteGamingContent()
        context = {"stories": [_make_story()], "run_stats": {}}
        result = stage.execute(context)

        content = result["stories"][0]["content"]
        assert content["hook"]
        assert "Elden Ring" in content["hook"]
        assert result["run_stats"]["content_writing"]["failed_count"] == 1


class TestStatsTracking:
    @patch("niches.gaming.stages.write_gaming_content.settings", _mock_settings("test-key"))
    @patch("niches.gaming.stages.write_gaming_content.anthropic")
    def test_stats_written_to_context(self, mock_anthropic_module):
        """run_stats has written_count and failed_count."""
        from niches.gaming.stages.write_gaming_content import WriteGamingContent

        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=_valid_llm_response())]
        mock_client.messages.create.return_value = mock_response

        stage = WriteGamingContent()
        context = {
            "stories": [_make_story(), _make_story("CS2", 0.7)],
            "run_stats": {},
        }
        result = stage.execute(context)

        stats = result["run_stats"]["content_writing"]
        assert "written_count" in stats
        assert "failed_count" in stats
        assert stats["written_count"] + stats["failed_count"] == 2
