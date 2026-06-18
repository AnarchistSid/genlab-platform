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


# ---------------------------------------------------------------------------
# Twitch non-game category filter (2026-06-18 outage fix)
# ---------------------------------------------------------------------------


class TestTwitchNonGameFilter:
    """The Twitch helix top-games endpoint returns non-game categories
    like 'Just Chatting' and 'IRL' mixed with real games. The fetcher
    must filter these out — see the 2026-06-18 outage root-cause.
    """

    def _mock_helix_response(self, games):
        """Build a mocked Twitch helix response with given game dicts."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": games}
        return resp

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_skips_entries_with_empty_igdb_id(self, mock_get, monkeypatch):
        """The canonical "not a real game" signal from Twitch is
        ``igdb_id == ""``. Drop any such entry."""
        monkeypatch.setenv("TWITCH_CLIENT_ID", "test-id")
        monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-secret")

        mock_get.return_value = self._mock_helix_response(
            [
                {"id": "1", "name": "Real Game", "igdb_id": "100", "box_art_url": ""},
                {"id": "2", "name": "Just Chatting", "igdb_id": "", "box_art_url": ""},
                {"id": "3", "name": "Another Game", "igdb_id": "200", "box_art_url": ""},
                {"id": "4", "name": "IRL", "igdb_id": "", "box_art_url": ""},
            ]
        )

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        with patch("niches.gaming.tools._twitch_auth.TwitchTokenManager") as MockToken:
            MockToken.return_value.get_token.return_value = "fake-token"
            fetcher = TwitchTrendingFetcher()
            stories = fetcher.fetch()

        titles = [s["title"] for s in stories]
        assert "Real Game" in titles
        assert "Another Game" in titles
        assert "Just Chatting" not in titles
        assert "IRL" not in titles

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_skips_hardcoded_non_game_category_ids(self, mock_get, monkeypatch):
        """Belt-and-suspenders: even if Twitch populates igdb_id for
        a non-game category, the hardcoded ID list catches it."""
        monkeypatch.setenv("TWITCH_CLIENT_ID", "test-id")
        monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-secret")

        mock_get.return_value = self._mock_helix_response(
            [
                {
                    "id": "509658",  # Just Chatting ID in non-game list
                    "name": "Just Chatting (with bogus igdb)",
                    "igdb_id": "999",  # Twitch could in theory populate this
                    "box_art_url": "",
                },
                {"id": "9999", "name": "Real Game", "igdb_id": "100", "box_art_url": ""},
            ]
        )

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        with patch("niches.gaming.tools._twitch_auth.TwitchTokenManager") as MockToken:
            MockToken.return_value.get_token.return_value = "fake-token"
            fetcher = TwitchTrendingFetcher()
            stories = fetcher.fetch()

        titles = [s["title"] for s in stories]
        assert any("Real Game" in t for t in titles)
        assert not any("Just Chatting" in t for t in titles)

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_returns_at_most_5_real_games(self, mock_get, monkeypatch):
        """Even with 20 real-game entries, we cap at 5 (top-N by Twitch chart rank)."""
        monkeypatch.setenv("TWITCH_CLIENT_ID", "test-id")
        monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-secret")

        many_games = [
            {"id": str(i), "name": f"Game {i}", "igdb_id": str(100 + i), "box_art_url": ""}
            for i in range(20)
        ]
        mock_get.return_value = self._mock_helix_response(many_games)

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        with patch("niches.gaming.tools._twitch_auth.TwitchTokenManager") as MockToken:
            MockToken.return_value.get_token.return_value = "fake-token"
            fetcher = TwitchTrendingFetcher()
            stories = fetcher.fetch()

        assert len(stories) == 5
        # All returned stories must have proper rank ordering (highest score first)
        scores = [s["score"] for s in stories]
        assert scores == sorted(scores, reverse=True)

    @patch("niches.gaming.stages.fetch_gaming_stories.requests.get")
    def test_returns_empty_when_all_entries_are_non_games(self, mock_get, monkeypatch):
        """Edge case: Twitch chart is entirely non-game categories. Should
        return 0 stories cleanly, letting Steam + RSS provide content."""
        monkeypatch.setenv("TWITCH_CLIENT_ID", "test-id")
        monkeypatch.setenv("TWITCH_CLIENT_SECRET", "test-secret")

        mock_get.return_value = self._mock_helix_response(
            [
                {"id": "1", "name": "Just Chatting", "igdb_id": "", "box_art_url": ""},
                {"id": "2", "name": "IRL", "igdb_id": "", "box_art_url": ""},
            ]
        )

        from niches.gaming.stages.fetch_gaming_stories import TwitchTrendingFetcher

        with patch("niches.gaming.tools._twitch_auth.TwitchTokenManager") as MockToken:
            MockToken.return_value.get_token.return_value = "fake-token"
            fetcher = TwitchTrendingFetcher()
            stories = fetcher.fetch()

        assert stories == []
