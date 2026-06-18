"""Tests for ClutchWire ESPN + RSS fetcher."""

from unittest.mock import MagicMock, patch

import pytest
from cw_strategies.fetch_sports_news import (
    ESPNFetcher,
    RSSFetcher,
    SportsStoryItem,
)

# --- ESPN Scoreboard ---

MOCK_SCOREBOARD_RESPONSE = {
    "events": [
        {
            "name": "Lakers vs Celtics",
            "date": "2026-03-10T01:00Z",
            "status": {
                "type": {
                    "state": "in",
                    "shortDetail": "Q3 5:42",
                }
            },
            "season": {"slug": "regular-season"},
            "competitions": [
                {
                    "competitors": [
                        {"team": {"displayName": "Los Angeles Lakers"}},
                        {"team": {"displayName": "Boston Celtics"}},
                    ]
                }
            ],
            "links": [{"href": "https://espn.com/game/123"}],
        },
        {
            "name": "Warriors vs Nuggets",
            "date": "2026-03-10T02:00Z",
            "status": {"type": {"state": "post", "shortDetail": "Final"}},
            "season": {"slug": "playoff"},
            "competitions": [
                {
                    "competitors": [
                        {"team": {"displayName": "Golden State Warriors"}},
                        {"team": {"displayName": "Denver Nuggets"}},
                    ]
                }
            ],
            "links": [],
        },
    ]
}


@pytest.fixture
def espn_config():
    return {
        "espn_api": {
            "scoreboard": {
                "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
            },
            "news": {
                "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news",
            },
            "timeout_seconds": 5,
        }
    }


class TestESPNScoreboard:
    def test_parses_events(self, espn_config):
        fetcher = ESPNFetcher(espn_config)
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_SCOREBOARD_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("cw_strategies.fetch_sports_news.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            items = fetcher.fetch_scoreboard()

        assert len(items) == 2

    def test_live_game_detected(self, espn_config):
        fetcher = ESPNFetcher(espn_config)
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_SCOREBOARD_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("cw_strategies.fetch_sports_news.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            items = fetcher.fetch_scoreboard()

        live_items = [i for i in items if i.is_live]
        assert len(live_items) == 1
        assert "Lakers" in live_items[0].title

    def test_teams_extracted(self, espn_config):
        fetcher = ESPNFetcher(espn_config)
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_SCOREBOARD_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("cw_strategies.fetch_sports_news.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            items = fetcher.fetch_scoreboard()

        assert "Los Angeles Lakers" in items[0].teams
        assert "Boston Celtics" in items[0].teams

    def test_playoff_classified(self, espn_config):
        fetcher = ESPNFetcher(espn_config)
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_SCOREBOARD_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("cw_strategies.fetch_sports_news.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            items = fetcher.fetch_scoreboard()

        assert items[1].game_type == "playoff_game"

    def test_missing_fields_handled_gracefully(self, espn_config):
        """ESPN API response with missing optional fields doesn't crash."""
        fetcher = ESPNFetcher(espn_config)
        sparse_response = {
            "events": [
                {
                    "name": "Sparse Event",
                    "status": {},
                    "season": {},
                    "competitions": [],
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = sparse_response
        mock_resp.raise_for_status = MagicMock()

        with patch("cw_strategies.fetch_sports_news.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            items = fetcher.fetch_scoreboard()

        assert len(items) == 1
        assert items[0].title == "Sparse Event"
        assert items[0].teams == []


# --- ESPN News ---

MOCK_NEWS_RESPONSE = {
    "articles": [
        {
            "headline": "LeBron passes Kareem",
            "description": "Historic scoring record broken",
            "published": "2026-03-10T00:00:00Z",
            "links": {"web": {"href": "https://espn.com/article/1"}},
        },
        {
            "headline": "Trade deadline recap",
            "description": "All the moves",
            "published": "2026-03-09T23:00:00Z",
            "links": {},
        },
    ]
}


class TestESPNNews:
    def test_parses_articles(self, espn_config):
        fetcher = ESPNFetcher(espn_config)
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_NEWS_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("cw_strategies.fetch_sports_news.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            items = fetcher.fetch_news()

        assert len(items) == 2
        assert items[0].title == "LeBron passes Kareem"
        assert items[0].source == "espn_news"

    def test_missing_links_handled(self, espn_config):
        fetcher = ESPNFetcher(espn_config)
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_NEWS_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("cw_strategies.fetch_sports_news.httpx.Client") as MockClient:
            MockClient.return_value.__enter__ = MagicMock(
                return_value=MagicMock(get=MagicMock(return_value=mock_resp))
            )
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            items = fetcher.fetch_news()

        # Second article has empty links dict
        assert items[1].source_url == ""


# --- RSS Fetcher ---


class TestRSSFetcher:
    def test_fetches_from_rss_sources(self):
        config = {
            "tier_1": {
                "sources": [
                    {"name": "ESPN", "type": "rss", "url": "https://espn.com/rss", "weight": 1.0},
                ]
            },
            "tier_2": {"sources": []},
            "tier_3": {"sources": []},
        }
        fetcher = RSSFetcher(config)

        mock_feed = {
            "entries": [
                {"title": "Story 1", "link": "https://espn.com/1", "summary": "Summary 1"},
                {"title": "Story 2", "link": "https://espn.com/2", "summary": "Summary 2"},
            ]
        }

        with patch("cw_strategies.fetch_sports_news.feedparser.parse", return_value=mock_feed):
            items = fetcher.fetch_all()

        assert len(items) == 2
        assert items[0].source == "rss_espn"
        assert items[0].extra["tier"] == "tier_1"

    def test_skips_non_rss_sources(self):
        config = {
            "tier_1": {"sources": []},
            "tier_2": {
                "sources": [
                    {"name": "Reddit", "type": "reddit", "subreddit": "nba", "weight": 0.9},
                ]
            },
            "tier_3": {"sources": []},
        }
        fetcher = RSSFetcher(config)
        items = fetcher.fetch_all()
        assert len(items) == 0


# --- SportsStoryItem ---


class TestSportsStoryItem:
    def test_to_dict(self):
        item = SportsStoryItem(
            title="Test",
            source="test",
            teams=["Lakers", "Celtics"],
        )
        d = item.to_dict()
        assert d["title"] == "Test"
        assert d["teams"] == ["Lakers", "Celtics"]
        assert d["game_type"] == "regular_season"
        assert d["is_live"] is False


# --- 2026-06-18: ESPN news opt-in fix ---


class TestEspnNewsOptIn:
    """ESPN ``fetch_news`` returns TEXT-ONLY articles. Sports is a
    video-only niche per ClutchWire/CLAUDE.md — fetching text articles
    polluted the candidate pool, displaced video stories in the top-N
    cut, then died at DownloadTopVideos for two consecutive days
    (2026-06-17/18) producing zero blueprints.

    The fix makes ESPN news opt-in: ``fetch_news`` only runs when
    ``sources.yaml`` has ``fetch_espn_news: true`` at the top level.
    Default is off, preserving the post-fix behaviour."""

    def test_default_skips_espn_news(self):
        """Default config (no flag) MUST NOT call fetch_news."""
        from cw_strategies import fetch_sports_news as mod

        config = {"espn": {}, "rss_feeds": []}  # no fetch_espn_news flag

        with (
            patch.object(mod.ESPNFetcher, "fetch_scoreboard", return_value=[]),
            patch.object(mod.ESPNFetcher, "fetch_news") as mock_news,
            patch.object(mod.RSSFetcher, "fetch_all", return_value=[]),
        ):
            result = mod.fetch_all_sports_news(config)
        mock_news.assert_not_called()
        assert result == []

    def test_flag_true_enables_espn_news(self):
        """Operator opts in via ``fetch_espn_news: true``."""
        from cw_strategies import fetch_sports_news as mod

        config = {"fetch_espn_news": True, "espn": {}, "rss_feeds": []}

        fake_article = SportsStoryItem(title="Transfer rumour", source="espn_news")
        with (
            patch.object(mod.ESPNFetcher, "fetch_scoreboard", return_value=[]),
            patch.object(mod.ESPNFetcher, "fetch_news", return_value=[fake_article]),
            patch.object(mod.RSSFetcher, "fetch_all", return_value=[]),
        ):
            result = mod.fetch_all_sports_news(config)
        assert len(result) == 1
        assert result[0]["title"] == "Transfer rumour"

    def test_flag_false_explicit_skips_espn_news(self):
        """Explicit ``fetch_espn_news: false`` is identical to absent."""
        from cw_strategies import fetch_sports_news as mod

        config = {"fetch_espn_news": False, "espn": {}, "rss_feeds": []}

        with (
            patch.object(mod.ESPNFetcher, "fetch_scoreboard", return_value=[]),
            patch.object(mod.ESPNFetcher, "fetch_news") as mock_news,
            patch.object(mod.RSSFetcher, "fetch_all", return_value=[]),
        ):
            mod.fetch_all_sports_news(config)
        mock_news.assert_not_called()

    def test_scoreboard_still_runs_unconditionally(self):
        """ESPN scoreboard (game data, not text articles) MUST still
        run — those headlines feed the video-discovery search."""
        from cw_strategies import fetch_sports_news as mod

        config = {"espn": {}, "rss_feeds": []}
        fake_game = SportsStoryItem(
            title="Lakers 102, Celtics 98 (Final)", source="espn_scoreboard"
        )
        with (
            patch.object(mod.ESPNFetcher, "fetch_scoreboard", return_value=[fake_game]) as mock_sb,
            patch.object(mod.ESPNFetcher, "fetch_news", return_value=[]),
            patch.object(mod.RSSFetcher, "fetch_all", return_value=[]),
        ):
            result = mod.fetch_all_sports_news(config)
        mock_sb.assert_called_once()
        assert len(result) == 1
        assert result[0]["source"] == "espn_scoreboard"
