"""Pin IGDB canonical_name plumbing.

Live-fire 2026-08-13: gaming pipeline was 100% Twitch-sourced
because YT search on raw RSS/trending titles ("ACE COMBAT 8 The
Art of Aircraft Trailer") returned 0 hits universally. Marketing
suffixes and platform tags in titles make YT search fragile.

Fix: IGDB fuzzy-matches the title to the actual game record and
returns the canonical name ("ACE COMBAT 8"). ExtractGamingMedia
now prefers that canonical name when calling ClipSourcer so YT
searches use the actual-game-name query instead of the verbose
original.

## Pin coverage

  * IGDBClient.search_game() returns canonical_name field
  * IGDBClient.enrich_story() merges canonical_name into story
  * ExtractGamingMedia prefers story['canonical_name'] over
    story['title'] when calling source_clip
  * Falls back to title when canonical_name absent (IGDB not
    matched)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestIGDBReturnsCanonicalName:
    def test_search_game_returns_canonical_name_field(self):
        """search_game() returns a dict containing `canonical_name`
        drawn from IGDB's `name` field on the chosen match."""
        from niches.gaming.tools.igdb_client import IGDBClient

        with patch(
            "niches.gaming.tools._twitch_auth.TwitchTokenManager"
        ), patch(
            "genlab_core.settings.settings"
        ) as mock_settings, patch("requests.post") as mock_post:
            mock_settings.twitch_client_id = "fake"
            mock_settings.twitch_client_secret = "fake"
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = [
                {
                    "id": 12345,
                    "name": "ACE COMBAT 8",
                    "external_games": [],
                    "involved_companies": [],
                }
            ]
            mock_post.return_value = resp
            client = IGDBClient()
            client._configured = True
            client._token_manager.get_token = lambda: "token"

            result = client.search_game(
                "ACE COMBAT 8 The Art of Aircraft Trailer"
            )
        assert result is not None
        assert result["canonical_name"] == "ACE COMBAT 8"
        assert result["igdb_game_id"] == "12345"

    def test_search_game_no_results_returns_none(self):
        from niches.gaming.tools.igdb_client import IGDBClient

        with patch(
            "niches.gaming.tools._twitch_auth.TwitchTokenManager"
        ), patch(
            "genlab_core.settings.settings"
        ) as mock_settings, patch("requests.post") as mock_post:
            mock_settings.twitch_client_id = "fake"
            mock_settings.twitch_client_secret = "fake"
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = []
            mock_post.return_value = resp
            client = IGDBClient()
            client._configured = True
            client._token_manager.get_token = lambda: "token"
            assert client.search_game("Unknown Game X") is None


class TestEnrichStoryMergesCanonicalName:
    def test_canonical_name_lands_on_story(self):
        from niches.gaming.tools.igdb_client import IGDBClient

        client = IGDBClient()
        # Force _configured True for the enrich path
        client._configured = True

        with patch.object(
            client, "search_game",
            return_value={
                "igdb_game_id": "12345",
                "canonical_name": "ACE COMBAT 8",
                "steam_app_id": None,
                "developer": None,
                "cover_url": None,
                "rating": None,
            },
        ):
            story = {"title": "ACE COMBAT 8 The Art of Aircraft Trailer"}
            enriched = client.enrich_story(
                story,
                context={"feature_flags": {"use_gaming_clip_sourcer": True}},
            )
        assert enriched["canonical_name"] == "ACE COMBAT 8"
        assert enriched["igdb_game_id"] == "12345"
        # Title untouched — enrich adds fields, doesn't replace
        assert enriched["title"] == "ACE COMBAT 8 The Art of Aircraft Trailer"


class TestExtractGamingMediaPrefersCanonicalName:
    """The core wire — this is what fixes the "100% Twitch" outcome."""

    def _fake_sourcer(self):
        sourcer = MagicMock()
        result = MagicMock()
        result.model_dump.return_value = {"source_tier": "youtube", "file_path": "/tmp/x.mp4"}
        sourcer.source_clip.return_value = result
        return sourcer

    def test_canonical_name_used_when_present(self):
        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = self._fake_sourcer()
        with patch.object(stage, "_get_sourcer", return_value=sourcer):
            story = {
                "title": "ACE COMBAT 8 The Art of Aircraft Trailer",
                "canonical_name": "ACE COMBAT 8",
                "steam_app_id": None,
                "igdb_game_id": "12345",
            }
            stage._source_clip_for_story(story, Path("/tmp"))
        kwargs = sourcer.source_clip.call_args.kwargs
        # This is the whole point: canonical name goes to the sourcer
        assert kwargs["game_title"] == "ACE COMBAT 8"

    def test_falls_back_to_title_when_canonical_missing(self):
        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = self._fake_sourcer()
        with patch.object(stage, "_get_sourcer", return_value=sourcer):
            story = {
                "title": "Some Game",  # no canonical_name
                "steam_app_id": None,
                "igdb_game_id": None,
            }
            stage._source_clip_for_story(story, Path("/tmp"))
        assert sourcer.source_clip.call_args.kwargs["game_title"] == "Some Game"

    def test_falls_back_to_title_when_canonical_empty(self):
        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = self._fake_sourcer()
        with patch.object(stage, "_get_sourcer", return_value=sourcer):
            story = {
                "title": "Some Game",
                "canonical_name": "",  # empty string, not None
                "steam_app_id": None,
                "igdb_game_id": None,
            }
            stage._source_clip_for_story(story, Path("/tmp"))
        assert sourcer.source_clip.call_args.kwargs["game_title"] == "Some Game"

    def test_igdb_ids_still_passed_through(self):
        """Steam + IGDB IDs plumbed through unchanged — canonical_name
        only affects the human-readable game_title parameter."""
        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = self._fake_sourcer()
        with patch.object(stage, "_get_sourcer", return_value=sourcer):
            story = {
                "title": "Verbose Trailer Title",
                "canonical_name": "Simple Name",
                "steam_app_id": "5678",
                "igdb_game_id": "12345",
            }
            stage._source_clip_for_story(story, Path("/tmp"))
        kwargs = sourcer.source_clip.call_args.kwargs
        assert kwargs["steam_app_id"] == "5678"
        assert kwargs["igdb_game_id"] == "12345"

    def test_canonical_matching_title_no_log_line(self, caplog):
        """When canonical == raw title, no confusing 'IGDB canonical: X -> Y'
        log — skip when they're identical (case-insensitive)."""
        import logging

        from niches.gaming.stages.extract_gaming_media import ExtractGamingMedia

        stage = ExtractGamingMedia()
        sourcer = self._fake_sourcer()
        with patch.object(stage, "_get_sourcer", return_value=sourcer), \
             caplog.at_level(logging.INFO):
            story = {
                "title": "Halo Infinite",
                "canonical_name": "Halo Infinite",
                "steam_app_id": None,
                "igdb_game_id": None,
            }
            stage._source_clip_for_story(story, Path("/tmp"))
        assert not any("IGDB canonical:" in r.message for r in caplog.records)
