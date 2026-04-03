"""Tests for SpliceReel film news fetcher."""

from unittest.mock import MagicMock, patch

from sr_strategies.fetch_film_news import (
    EntertainmentRSSFetcher,
    FilmStoryItem,
    TMDBFetcher,
    detect_franchise,
    fetch_all_film_news,
)


class TestDetectFranchise:
    def test_mcu_detected_from_avengers(self):
        assert detect_franchise("Avengers: Secret Wars") == "MCU"

    def test_star_wars_detected_from_jedi(self):
        assert detect_franchise("Return of the Jedi") == "Star_Wars"

    def test_dc_detected_from_batman(self):
        assert detect_franchise("The Batman Part II") == "DC"

    def test_unknown_title_returns_empty(self):
        assert detect_franchise("Oppenheimer") == ""

    def test_case_insensitive(self):
        assert detect_franchise("SPIDER-MAN: Beyond") == "MCU"


class TestFilmStoryItem:
    def test_to_dict_roundtrip(self):
        item = FilmStoryItem(
            title="Test Film",
            source="tmdb_trending",
            film_title="Test Film",
            franchise="MCU",
            tmdb_popularity=42.5,
        )
        d = item.to_dict()
        assert d["title"] == "Test Film"
        assert d["franchise"] == "MCU"
        assert d["tmdb_popularity"] == 42.5
        assert d["is_trailer_drop"] is False


class TestTMDBFetcher:
    @patch("sr_strategies.fetch_film_news.httpx.Client")
    def test_fetch_trending_returns_film_story_items(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "id": 123,
                    "title": "Thunderbolts",
                    "release_date": "2025-05-02",
                    "overview": "A team assembles",
                    "popularity": 85.3,
                    "vote_average": 7.2,
                },
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        fetcher = TMDBFetcher(api_key="test_key")
        items = fetcher.fetch_trending()

        assert len(items) == 1
        assert items[0].film_title == "Thunderbolts"
        assert items[0].source == "tmdb_trending"
        assert items[0].tmdb_popularity == 85.3

    @patch("sr_strategies.fetch_film_news.httpx.Client")
    def test_tmdb_fetcher_gracefully_handles_401(self, mock_client_cls):
        import httpx
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401)
        )
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        fetcher = TMDBFetcher(api_key="bad_key")
        items = fetcher.fetch_trending()
        assert items == []

    @patch("sr_strategies.fetch_film_news.httpx.Client")
    def test_missing_title_skipped(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"id": 1, "title": "", "release_date": ""},
                {"id": 2, "title": "Real Film", "release_date": "2025-06-01", "popularity": 10},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=MagicMock(get=MagicMock(return_value=mock_resp)))
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        fetcher = TMDBFetcher(api_key="test_key")
        items = fetcher.fetch_trending()
        assert len(items) == 1
        assert items[0].film_title == "Real Film"


class TestEntertainmentRSSFetcher:
    def test_rss_detects_trailer_story(self):
        config = {
            "tier_2": {
                "sources": [
                    {"name": "Deadline", "type": "rss", "url": "https://example.com/rss"},
                ]
            }
        }
        with patch("sr_strategies.fetch_film_news.feedparser.parse") as mock_parse:
            mock_parse.return_value = {
                "entries": [
                    {"title": "New Spider-Man Trailer Drops Today", "link": "https://example.com/1", "summary": "..."},
                ]
            }
            fetcher = EntertainmentRSSFetcher(config)
            items = fetcher.fetch_all()

        assert len(items) == 1
        assert items[0].is_trailer_drop is True
        assert items[0].franchise == "MCU"

    def test_rss_detects_award_story(self):
        config = {
            "tier_2": {
                "sources": [
                    {"name": "THR", "type": "rss", "url": "https://example.com/rss"},
                ]
            }
        }
        with patch("sr_strategies.fetch_film_news.feedparser.parse") as mock_parse:
            mock_parse.return_value = {
                "entries": [
                    {"title": "Oscar Nominations Announced for 2026", "link": "https://example.com/2", "summary": "The Academy..."},
                ]
            }
            fetcher = EntertainmentRSSFetcher(config)
            items = fetcher.fetch_all()

        assert len(items) == 1
        assert items[0].is_award_news is True

    def test_rss_skips_non_rss_sources(self):
        config = {
            "tier_2": {
                "sources": [
                    {"name": "API", "type": "api", "url": "https://example.com/api"},
                ]
            }
        }
        fetcher = EntertainmentRSSFetcher(config)
        items = fetcher.fetch_all()
        assert items == []


class TestFetchAllFilmNews:
    @patch("sr_strategies.fetch_film_news.EntertainmentRSSFetcher")
    @patch.dict("os.environ", {"TMDB_API_KEY": ""})
    def test_runs_without_tmdb_key(self, mock_rss_cls):
        mock_rss = MagicMock()
        mock_rss.fetch_all.return_value = [
            FilmStoryItem(title="Test", source="rss_deadline", film_title="Test"),
        ]
        mock_rss_cls.return_value = mock_rss

        results = fetch_all_film_news(config={})
        assert len(results) == 1
        assert results[0]["source"] == "rss_deadline"
