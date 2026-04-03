"""Tests for the IGDB client — all HTTP calls are mocked."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_twitch_env(monkeypatch):
    """Ensure Twitch env vars are unset unless a test sets them."""
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)


def _make_client(monkeypatch, client_id="test_id", client_secret="test_secret"):
    """Create an IGDBClient with mocked settings credentials."""
    with patch("niches.gaming.tools.igdb_client.TwitchTokenManager"):
        with patch("genlab_core.settings.settings") as mock_settings:
            mock_settings.twitch_client_id = client_id
            mock_settings.twitch_client_secret = client_secret
            from niches.gaming.tools.igdb_client import IGDBClient
            client = IGDBClient()
    return client


def _mock_token_response():
    """Return a mock token response."""
    resp = MagicMock()
    resp.json.return_value = {"access_token": "test_token", "expires_in": 3600}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_search_response(results):
    """Return a mock IGDB search response."""
    resp = MagicMock()
    resp.json.return_value = results
    resp.raise_for_status = MagicMock()
    return resp


class TestSearchGameUnconfigured:
    def test_returns_none_when_no_credentials(self, monkeypatch):
        """search_game returns None when credentials are empty."""
        with patch("genlab_core.settings.settings") as mock_settings:
            mock_settings.twitch_client_id = ""
            mock_settings.twitch_client_secret = ""
            from niches.gaming.tools.igdb_client import IGDBClient
            client = IGDBClient()
        assert client.search_game("Elden Ring") is None

    def test_not_configured_flag(self, monkeypatch):
        with patch("genlab_core.settings.settings") as mock_settings:
            mock_settings.twitch_client_id = ""
            mock_settings.twitch_client_secret = ""
            from niches.gaming.tools.igdb_client import IGDBClient
            client = IGDBClient()
        assert client._configured is False


class TestSearchGameConfigured:
    @patch("niches.gaming.tools.igdb_client.requests.post")
    def test_extracts_steam_app_id_from_external_games(
        self, mock_post, monkeypatch
    ):
        """steam_app_id is extracted from external_games where category == 1."""
        client = _make_client(monkeypatch)

        # Mock the _headers method to return valid headers
        client._headers = MagicMock(return_value={
            "Authorization": "Bearer test_token",
            "Client-Id": "test_id",
            "Content-Type": "text/plain",
        })

        mock_post.return_value = _mock_search_response([
            {
                "id": 119133,
                "name": "Elden Ring",
                "external_games": [
                    {"category": 5, "uid": "some_gog_id"},
                    {"category": 1, "uid": "1245620"},
                ],
                "involved_companies": [
                    {"developer": True, "company": {"name": "FromSoftware"}},
                ],
                "cover": {"url": "//images.igdb.com/cover.jpg"},
                "total_rating": 95.0,
            }
        ])

        result = client.search_game("Elden Ring")
        assert result is not None
        assert result["steam_app_id"] == "1245620"
        assert result["igdb_game_id"] == "119133"
        assert result["developer"] == "FromSoftware"
        assert result["cover_url"] == "https://images.igdb.com/cover.jpg"
        assert result["rating"] == 95.0

    @patch("niches.gaming.tools.igdb_client.requests.post")
    def test_returns_none_on_empty_results(
        self, mock_post, monkeypatch
    ):
        client = _make_client(monkeypatch)
        client._headers = MagicMock(return_value={
            "Authorization": "Bearer test_token",
            "Client-Id": "test_id",
            "Content-Type": "text/plain",
        })
        mock_post.return_value = _mock_search_response([])

        result = client.search_game("Nonexistent Game XYZZY")
        assert result is None


class TestEnrichStory:
    def test_noop_when_flag_false(self, monkeypatch):
        """enrich_story is a no-op when use_gaming_clip_sourcer flag is False."""
        client = _make_client(monkeypatch)
        story = {"title": "Elden Ring DLC Announced"}
        context = {"feature_flags": {"use_gaming_clip_sourcer": False}}

        result = client.enrich_story(story, context=context)
        assert result == {"title": "Elden Ring DLC Announced"}

    @patch("niches.gaming.tools.igdb_client.requests.post")
    def test_merges_fields_when_flag_true(
        self, mock_post, monkeypatch
    ):
        """enrich_story merges IGDB fields into story when flag is True."""
        client = _make_client(monkeypatch)
        client._headers = MagicMock(return_value={
            "Authorization": "Bearer test_token",
            "Client-Id": "test_id",
            "Content-Type": "text/plain",
        })
        mock_post.return_value = _mock_search_response([
            {
                "id": 119133,
                "name": "Elden Ring",
                "external_games": [{"category": 1, "uid": "1245620"}],
                "involved_companies": [
                    {"developer": True, "company": {"name": "FromSoftware"}},
                ],
                "cover": {"url": "//images.igdb.com/cover.jpg"},
                "total_rating": 95.0,
            }
        ])

        story = {"title": "Elden Ring"}
        context = {"feature_flags": {"use_gaming_clip_sourcer": True}}

        result = client.enrich_story(story, context=context)
        assert result["igdb_game_id"] == "119133"
        assert result["steam_app_id"] == "1245620"
        assert result["developer"] == "FromSoftware"
