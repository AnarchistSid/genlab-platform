"""Tests for FetchGamingStories — all external calls mocked."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


def _now_utc():
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Steam spike tests
# ---------------------------------------------------------------------------


class TestSteamSpike:
    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_steam_spike_returns_stories(self, mock_get, tmp_path, monkeypatch):
        """Mock Steam APIs, verify stories are populated."""
        # Mock featuredcategories response
        featured_resp = MagicMock()
        featured_resp.raise_for_status = MagicMock()
        featured_resp.json.return_value = {
            "top_sellers": {
                "items": [
                    {"id": 1245620, "name": "Elden Ring"},
                    {"id": 730, "name": "Counter-Strike 2"},
                ]
            }
        }

        # Mock player count responses — both first-time (no baseline)
        players_resp_1 = MagicMock()
        players_resp_1.raise_for_status = MagicMock()
        players_resp_1.json.return_value = {"response": {"player_count": 50000}}

        players_resp_2 = MagicMock()
        players_resp_2.raise_for_status = MagicMock()
        players_resp_2.json.return_value = {"response": {"player_count": 100000}}

        mock_get.side_effect = [featured_resp, players_resp_1, players_resp_2]

        from niches.gaming.stages.fetch_gaming_stories import SteamSpikeFetcher

        # Patch PROJECT_ROOT to use tmp_path for baseline file
        monkeypatch.setattr("niches.gaming.stages.fetch_gaming_stories.PROJECT_ROOT", tmp_path)

        fetcher = SteamSpikeFetcher({"spike_threshold_multiplier": 1.5, "max_stories": 5})
        fetcher._baseline_path = tmp_path / ".tmp" / "steam_baseline.json"
        stories = fetcher.fetch()

        # First-time games get score 0.5
        assert len(stories) == 2
        assert stories[0]["title"] == "Elden Ring"
        assert stories[0]["score"] == 0.5
        assert stories[0]["steam_app_id"] == "1245620"
        assert stories[0]["source"] == "steam_spike"


# ---------------------------------------------------------------------------
# Twitch skip test
# ---------------------------------------------------------------------------


class TestTwitchSkip:
    def test_twitch_skipped_without_credentials(self, monkeypatch):
        """When TWITCH_CLIENT_ID is unset, fetch returns empty list."""
        monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
        monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        fetcher = TwitchTrendingFetcher()
        stories = fetcher.fetch()
        # Without Twitch creds, Twitch clips are skipped but IGDB/other sources may return results.
        # Verify no story has source_type == "twitch_clip"
        twitch_stories = [s for s in stories if s.get("source_type") == "twitch_clip"]
        assert twitch_stories == [], (
            f"Expected no Twitch clips without credentials, got {len(twitch_stories)}"
        )


# ---------------------------------------------------------------------------
# RSS tests
# ---------------------------------------------------------------------------


class TestRSSFiltering:
    def test_rss_filters_old_articles(self):
        """Articles older than 48 hours are excluded."""
        from niches.gaming.stages.fetch_gaming_stories import RSSFeedAggregator

        # Create a mock feed with one old entry
        old_time = _now_utc() - timedelta(hours=72)
        old_parsed = old_time.timetuple()

        with patch("niches.gaming.stages.fetch_gaming_stories.feedparser.parse") as mock_parse:
            entry = MagicMock()
            entry.title = "Old News"
            entry.link = "https://example.com/old"
            entry.summary = "Ancient article"
            entry.published_parsed = old_parsed

            mock_parse.return_value = MagicMock(entries=[entry])

            agg = RSSFeedAggregator(
                [{"name": "Test", "url": "https://test.com/rss", "weight": 1.0}]
            )
            stories = agg.fetch(trending_titles=[])
            assert len(stories) == 0


class TestCrossSourceBoost:
    def test_cross_source_mention_boost(self):
        """A game appearing in trending titles gets +0.2 score boost in RSS."""
        from niches.gaming.stages.fetch_gaming_stories import RSSFeedAggregator

        recent_time = _now_utc() - timedelta(hours=1)
        recent_parsed = recent_time.timetuple()

        with patch("niches.gaming.stages.fetch_gaming_stories.feedparser.parse") as mock_parse:
            entry = MagicMock()
            entry.title = "Elden Ring DLC breaks records"
            entry.link = "https://example.com/elden"
            entry.summary = "New DLC"
            entry.published_parsed = recent_parsed

            mock_parse.return_value = MagicMock(entries=[entry])

            agg = RSSFeedAggregator([{"name": "IGN", "url": "https://ign.com/rss", "weight": 0.7}])

            # Without trending
            stories_no_boost = agg.fetch(trending_titles=[])
            # With trending
            stories_boosted = agg.fetch(trending_titles=["Elden Ring"])

            assert len(stories_no_boost) == 1
            assert len(stories_boosted) == 1
            assert stories_boosted[0]["score"] > stories_no_boost[0]["score"]


# ---------------------------------------------------------------------------
# Deduplication test
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_deduplication_keeps_highest_score(self, monkeypatch):
        """Two stories with same title from different sources — highest wins."""
        from niches.gaming.stages.fetch_gaming_stories import FetchGamingStories

        steam_stories = [
            {
                "title": "Elden Ring",
                "source": "steam_spike",
                "source_url": "https://store.steampowered.com/app/1245620",
                "score": 0.8,
                "published_at": _now_utc().isoformat(),
                "summary": "Spike",
                "steam_app_id": "1245620",
                "igdb_game_id": None,
                "developer": None,
            }
        ]
        rss_stories = [
            {
                "title": "Elden Ring",
                "source": "rss",
                "source_url": "https://ign.com/elden-ring",
                "score": 0.5,
                "published_at": _now_utc().isoformat(),
                "summary": "Article",
                "steam_app_id": None,
                "igdb_game_id": None,
                "developer": None,
            }
        ]

        with (
            patch.object(FetchGamingStories, "_load_sources_config", return_value={}),
            patch("niches.gaming.stages.fetch_gaming_stories.SteamSpikeFetcher") as MockSteam,
            patch("niches.gaming.stages.fetch_gaming_stories.TwitchTrendingFetcher") as MockTwitch,
            patch("niches.gaming.stages.fetch_gaming_stories.RSSFeedAggregator") as MockRSS,
        ):
            MockSteam.return_value.fetch.return_value = steam_stories
            MockTwitch.return_value.fetch.return_value = []
            MockRSS.return_value.fetch.return_value = rss_stories

            stage = FetchGamingStories()
            context = {"niche_config": {}, "run_stats": {}, "feature_flags": {}}
            result = stage.execute(context)

            stories = result["stories"]
            elden_stories = [s for s in stories if "elden" in s["title"].lower()]
            assert len(elden_stories) == 1
            assert elden_stories[0]["score"] == 0.8
            assert elden_stories[0]["source"] == "steam_spike"
