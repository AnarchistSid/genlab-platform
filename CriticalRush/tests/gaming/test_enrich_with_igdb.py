"""Tests for EnrichWithIGDB stage — all HTTP calls mocked.

The stage always enriches (no feature-flag gating). It calls
client.search_game(game_name) — NOT client.enrich_story().
"""

from unittest.mock import MagicMock, patch


def _make_stage_with_mock_client(search_return=None):
    """Create an EnrichWithIGDB with a mocked IGDB client.

    We patch _get_client so the lazy-init never fires.
    """
    from niches.gaming.stages.enrich_with_igdb import EnrichWithIGDB

    stage = EnrichWithIGDB()
    mock_client = MagicMock()
    mock_client.search_game.return_value = search_return
    stage._get_client = MagicMock(return_value=mock_client)
    return stage, mock_client


class TestNoMatchReturnsUnchanged:
    @patch("niches.gaming.stages.enrich_with_igdb.time.sleep")
    def test_story_unchanged_when_search_returns_none(self, mock_sleep):
        """When search_game returns None, the story is not modified."""
        stage, mock_client = _make_stage_with_mock_client(search_return=None)

        stories = [
            {"title": "Elden Ring", "source": "steam_spike", "score": 0.8},
        ]
        context = {"stories": stories, "run_stats": {}}

        result = stage.execute(context)

        assert "igdb_game_id" not in result["stories"][0]
        assert result["run_stats"]["igdb_enrichment"]["failed_count"] == 1


class TestEnrichment:
    @patch("niches.gaming.stages.enrich_with_igdb.time.sleep")
    def test_enriches_stories_missing_igdb_id(self, mock_sleep):
        """search_game result fields are merged into the story dict."""
        stage, mock_client = _make_stage_with_mock_client(
            search_return={
                "igdb_game_id": "119133",
                "steam_app_id": "1245620",
                "developer": "FromSoftware",
            }
        )

        stories = [
            {"title": "Elden Ring", "source": "rss", "score": 0.8},
        ]
        context = {"stories": stories, "run_stats": {}}

        result = stage.execute(context)

        mock_client.search_game.assert_called_once()
        assert result["stories"][0]["igdb_game_id"] == "119133"
        assert result["run_stats"]["igdb_enrichment"]["enriched_count"] == 1

    @patch("niches.gaming.stages.enrich_with_igdb.time.sleep")
    def test_skips_already_enriched_stories(self, mock_sleep):
        """Stories that already have igdb_game_id + steam_app_id are skipped."""
        stage, mock_client = _make_stage_with_mock_client()

        stories = [
            {
                "title": "Elden Ring",
                "source": "steam_spike",
                "score": 0.8,
                "igdb_game_id": "119133",
                "steam_app_id": "1245620",
            },
        ]
        context = {"stories": stories, "run_stats": {}}

        result = stage.execute(context)

        mock_client.search_game.assert_not_called()
        assert result["run_stats"]["igdb_enrichment"]["skipped_count"] == 1

    @patch("niches.gaming.stages.enrich_with_igdb.time.sleep")
    def test_stats_written_to_context(self, mock_sleep):
        """After execute(), run_stats has igdb_enrichment with counts."""
        stage, mock_client = _make_stage_with_mock_client(search_return=None)

        stories = [
            {"title": "Unknown Game", "source": "rss", "score": 0.3},
            {"title": "Another Game", "source": "rss", "score": 0.2},
        ]
        context = {"stories": stories, "run_stats": {}}

        result = stage.execute(context)

        stats = result["run_stats"]["igdb_enrichment"]
        assert "enriched_count" in stats
        assert "skipped_count" in stats
        assert "failed_count" in stats
        assert stats["enriched_count"] + stats["skipped_count"] + stats["failed_count"] == 2
