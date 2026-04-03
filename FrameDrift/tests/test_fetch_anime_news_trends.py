"""Tests for FrameDrift Google Trends integration in fetch_anime_news."""

from unittest.mock import MagicMock, patch

import pandas as pd
from fd_strategies.fetch_anime_news import (
    AnimeStoryItem,
    _fetch_google_trends,
    _get_google_trends,
    _google_trends_stub,
    fetch_all_anime_news,
)

# ---------------------------------------------------------------------------
# _fetch_google_trends — core function tests
# ---------------------------------------------------------------------------


class TestFetchGoogleTrends:
    """Tests for the real pytrends-backed _fetch_google_trends."""

    @patch("pytrends.request.TrendReq")
    def test_returns_scores_for_each_title(self, mock_trendreq_cls):
        mock_pytrends = MagicMock()
        mock_trendreq_cls.return_value = mock_pytrends

        # Simulate interest_over_time returning a DataFrame
        df = pd.DataFrame({
            "Nike Air Max": [30, 60, 45],
            "Yeezy Drop": [10, 20, 15],
            "isPartial": [False, False, False],
        })
        mock_pytrends.interest_over_time.return_value = df

        config = {"google_trends": {"enabled": True, "timeframe": "now 7-d"}}
        titles = ["Nike Air Max", "Yeezy Drop"]

        result = _fetch_google_trends(config, titles)

        assert len(result) == 2
        assert result[0] == {"title": "Nike Air Max", "trend_score": 60}
        assert result[1] == {"title": "Yeezy Drop", "trend_score": 20}

    @patch("pytrends.request.TrendReq")
    def test_batch_processing_over_5_titles(self, mock_trendreq_cls):
        """Google Trends allows max 5 keywords per request."""
        mock_pytrends = MagicMock()
        mock_trendreq_cls.return_value = mock_pytrends

        titles = [f"Title {i}" for i in range(8)]

        # First batch (5 titles): returns scores
        df_batch1 = pd.DataFrame({
            f"Title {i}": [i * 10] for i in range(5)
        })
        # Second batch (3 titles): returns scores
        df_batch2 = pd.DataFrame({
            f"Title {i}": [i * 10] for i in range(5, 8)
        })

        mock_pytrends.interest_over_time.side_effect = [df_batch1, df_batch2]

        config = {"google_trends": {"enabled": True}}
        result = _fetch_google_trends(config, titles)

        assert len(result) == 8
        # Verify build_payload was called twice (batches of 5 + 3)
        assert mock_pytrends.build_payload.call_count == 2

    @patch("pytrends.request.TrendReq")
    def test_error_returns_zero_scores(self, mock_trendreq_cls):
        """When pytrends fails, all titles in that batch get score=0."""
        mock_pytrends = MagicMock()
        mock_trendreq_cls.return_value = mock_pytrends
        mock_pytrends.build_payload.side_effect = Exception("429 Too Many Requests")

        config = {"google_trends": {"enabled": True}}
        titles = ["Nike Drop", "Adidas Collab"]

        result = _fetch_google_trends(config, titles)

        assert len(result) == 2
        assert result[0] == {"title": "Nike Drop", "trend_score": 0}
        assert result[1] == {"title": "Adidas Collab", "trend_score": 0}

    @patch("pytrends.request.TrendReq")
    def test_empty_dataframe_returns_zero_scores(self, mock_trendreq_cls):
        """When interest_over_time returns empty DataFrame, scores are 0."""
        mock_pytrends = MagicMock()
        mock_trendreq_cls.return_value = mock_pytrends
        mock_pytrends.interest_over_time.return_value = pd.DataFrame()

        config = {"google_trends": {"enabled": True}}
        titles = ["Unknown Brand"]

        result = _fetch_google_trends(config, titles)

        assert len(result) == 1
        assert result[0] == {"title": "Unknown Brand", "trend_score": 0}

    def test_disabled_returns_empty(self):
        """When google_trends is disabled, return empty list."""
        config = {"google_trends": {"enabled": False}}
        result = _fetch_google_trends(config, ["Something"])
        assert result == []

    @patch("pytrends.request.TrendReq")
    def test_long_titles_truncated_to_100_chars(self, mock_trendreq_cls):
        mock_pytrends = MagicMock()
        mock_trendreq_cls.return_value = mock_pytrends
        mock_pytrends.interest_over_time.return_value = pd.DataFrame()

        config = {"google_trends": {"enabled": True}}
        long_title = "A" * 200

        _fetch_google_trends(config, [long_title])

        # Verify the keyword passed to build_payload is truncated
        call_args = mock_pytrends.build_payload.call_args
        keywords = call_args[0][0]
        assert len(keywords[0]) == 100

    @patch("pytrends.request.TrendReq")
    def test_partial_batch_failure(self, mock_trendreq_cls):
        """First batch succeeds, second batch fails -- partial results."""
        mock_pytrends = MagicMock()
        mock_trendreq_cls.return_value = mock_pytrends

        df_ok = pd.DataFrame({"Title 0": [50], "Title 1": [30]})
        mock_pytrends.interest_over_time.side_effect = [
            df_ok,
            Exception("Rate limited"),
        ]

        config = {"google_trends": {"enabled": True}}
        titles = ["Title 0", "Title 1", "Title 2", "Title 3", "Title 4",
                   "Title 5", "Title 6"]

        result = _fetch_google_trends(config, titles)

        assert len(result) == 7
        # First batch succeeded
        assert result[0]["trend_score"] == 50
        assert result[1]["trend_score"] == 30
        # Second batch failed -> all zeros
        assert result[5]["trend_score"] == 0
        assert result[6]["trend_score"] == 0


# ---------------------------------------------------------------------------
# _get_google_trends — routing tests
# ---------------------------------------------------------------------------


class TestGetGoogleTrendsRouting:
    """Tests for stub_mode config flag routing."""

    @patch("fd_strategies.fetch_anime_news._google_trends_stub")
    def test_stub_mode_true_uses_stub(self, mock_stub):
        mock_stub.return_value = []
        config = {"google_trends": {"enabled": True, "stub_mode": True}}
        _get_google_trends(config, ["Test Title"])
        mock_stub.assert_called_once()

    @patch("fd_strategies.fetch_anime_news._fetch_google_trends")
    def test_stub_mode_false_uses_real(self, mock_real):
        mock_real.return_value = [{"title": "Test", "trend_score": 42}]
        config = {"google_trends": {"enabled": True, "stub_mode": False}}
        result = _get_google_trends(config, ["Test"])
        mock_real.assert_called_once()
        assert result[0]["trend_score"] == 42

    @patch("fd_strategies.fetch_anime_news._google_trends_stub")
    def test_stub_mode_absent_defaults_to_stub(self, mock_stub):
        """When stub_mode is not set, default to stub (safe)."""
        mock_stub.return_value = []
        config = {"google_trends": {"enabled": True}}
        _get_google_trends(config, ["Test"])
        mock_stub.assert_called_once()

    def test_disabled_returns_empty(self):
        config = {"google_trends": {"enabled": False}}
        result = _get_google_trends(config, ["Test"])
        assert result == []

    def test_missing_config_returns_empty(self):
        result = _get_google_trends({}, ["Test"])
        assert result == []


# ---------------------------------------------------------------------------
# _google_trends_stub — stub behavior tests
# ---------------------------------------------------------------------------


class TestGoogleTrendsStub:
    def test_stub_returns_empty_list(self):
        config = {"google_trends": {"enabled": True, "stub_mode": True}}
        result = _google_trends_stub(config, ["Nike Air Max"])
        assert result == []

    def test_stub_disabled_returns_empty(self):
        config = {"google_trends": {"enabled": False}}
        result = _google_trends_stub(config, ["Test"])
        assert result == []


# ---------------------------------------------------------------------------
# Integration — fetch_all_anime_news with trends
# ---------------------------------------------------------------------------


class TestFetchAllWithTrends:
    @patch("fd_strategies.fetch_anime_news._get_google_trends")
    @patch("fd_strategies.fetch_anime_news.AnimeRSSFetcher")
    def test_trend_scores_merged_into_items(self, mock_fetcher_cls, mock_trends):
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_all.return_value = [
            AnimeStoryItem(title="Nike Air Max 90 Drop", source="rss_hypebeast"),
        ]
        mock_fetcher_cls.return_value = mock_fetcher

        mock_trends.return_value = [
            {"title": "Nike Air Max 90 Drop", "trend_score": 75},
        ]

        results = fetch_all_anime_news(config={
            "google_trends": {"enabled": True, "stub_mode": False},
        })

        assert len(results) == 1
        assert results[0]["extra"]["google_trend_score"] == 75

    @patch("fd_strategies.fetch_anime_news._get_google_trends")
    @patch("fd_strategies.fetch_anime_news.AnimeRSSFetcher")
    def test_zero_score_not_added_to_extra(self, mock_fetcher_cls, mock_trends):
        mock_fetcher = MagicMock()
        mock_fetcher.fetch_all.return_value = [
            AnimeStoryItem(title="Unknown Story", source="rss_vogue"),
        ]
        mock_fetcher_cls.return_value = mock_fetcher

        mock_trends.return_value = [
            {"title": "Unknown Story", "trend_score": 0},
        ]

        results = fetch_all_anime_news(config={})

        assert "google_trend_score" not in results[0]["extra"]
