"""Tests for FrameDrift anime news fetcher."""

from datetime import UTC
from unittest.mock import MagicMock, patch

from fd_strategies.fetch_anime_news import (
    AnimeRSSFetcher,
    AnimeStoryItem,
    _detect_story_type,
    _is_creator_spotlight,
    _normalize_title,
    fetch_all_anime_news,
)


class TestNormalizeTitle:
    def test_strips_punctuation_and_lowercases(self):
        assert _normalize_title("MAPPA's NEW Chainsaw Man Trailer!") == "mappas new chainsaw man trailer"

    def test_collapses_whitespace(self):
        assert _normalize_title("  Too   much  space  ") == "too much space"


class TestDetectStoryType:
    def test_new_release_keyword_detected(self):
        result = _detect_story_type("Chainsaw Man Season 2 Premiere Date Confirmed")
        assert result["is_new_release"] is True

    def test_creator_spotlight_detected(self):
        result = _detect_story_type("Makoto Shinkai directed new film revealed")
        assert result["is_creator_spotlight"] is True

    def test_event_keyword_detected(self):
        result = _detect_story_type("Anime Expo 2026 panel reveals biggest lineup")
        assert result["is_event_coverage"] is True

    def test_collab_keyword_detected(self):
        result = _detect_story_type("MAPPA x ufotable crossover project announced")
        assert result["is_collab"] is True

    def test_generic_story_all_false(self):
        result = _detect_story_type("Anime industry report Q1 2026")
        assert result["is_new_release"] is False
        assert result["is_creator_spotlight"] is False
        assert result["is_event_coverage"] is False
        assert result["is_collab"] is False


class TestAnimeRSSFetcher:
    def test_rss_fetcher_returns_anime_story_items(self):
        config = {
            "tier_1": {
                "sources": [
                    {"name": "ANN", "type": "rss", "url": "https://example.com/feed"},
                ]
            }
        }
        with patch("fd_strategies.fetch_anime_news.feedparser.parse") as mock_parse:
            mock_parse.return_value = {
                "entries": [
                    {"title": "Jujutsu Kaisen Season 3 Premiere", "link": "https://example.com/1", "summary": "..."},
                ]
            }
            fetcher = AnimeRSSFetcher(config)
            items = fetcher.fetch_all()

        assert len(items) == 1
        assert items[0].is_new_release is True

    def test_cross_mention_grouping_counts_sources(self):
        config = {
            "tier_1": {
                "sources": [
                    {"name": "Source1", "type": "rss", "url": "https://a.com/feed"},
                    {"name": "Source2", "type": "rss", "url": "https://b.com/feed"},
                ]
            }
        }
        with patch("fd_strategies.fetch_anime_news.feedparser.parse") as mock_parse:
            mock_parse.return_value = {
                "entries": [
                    {"title": "Same Exact Anime Story Here", "link": "https://example.com/1", "summary": "..."},
                ]
            }
            fetcher = AnimeRSSFetcher(config)
            items = fetcher.fetch_all()

        # Both sources returned same title — should be grouped
        assert len(items) == 1
        assert items[0].source_mention_count == 2

    def test_rss_feed_failure_handled_gracefully(self):
        config = {
            "tier_1": {
                "sources": [
                    {"name": "Broken", "type": "rss", "url": "https://broken.com/feed"},
                ]
            }
        }
        with patch("fd_strategies.fetch_anime_news.feedparser.parse", side_effect=Exception("Network error")):
            fetcher = AnimeRSSFetcher(config)
            items = fetcher.fetch_all()
            assert items == []

    def test_skips_non_rss_sources(self):
        config = {
            "tier_1": {
                "sources": [
                    {"name": "API", "type": "api", "url": "https://api.example.com"},
                ]
            }
        }
        fetcher = AnimeRSSFetcher(config)
        items = fetcher.fetch_all()
        assert items == []


class TestCreatorSpotlightDetection:
    def test_generic_anime_title_not_classified_creator(self):
        assert _is_creator_spotlight("This is the biggest anime of the season") is False

    def test_known_creator_with_trigger_classified(self):
        assert _is_creator_spotlight("Makoto Shinkai directed new film trailer") is True

    def test_known_studio_with_trigger_classified(self):
        assert _is_creator_spotlight("MAPPA animation by studio revealed for new project") is True

    def test_creator_name_without_trigger_word_no_match(self):
        assert _is_creator_spotlight("Makoto Shinkai launches new merchandise line") is False


class TestCrossMentionGroupingV2:
    def test_cross_mention_groups_2_word_key_not_4_word(self):
        """2-3 word keys should group headlines that vary in phrasing."""
        fetcher = AnimeRSSFetcher({})
        key1 = fetcher._make_group_key("chainsaw man season three premiere announced")
        key2 = fetcher._make_group_key("chainsaw man season three different words")
        assert key1 == key2  # Both use first 3 significant words

    def test_tier1_fresh_single_source_is_emerging(self):
        """Tier-1 fresh single-source items should be classified EMERGING."""
        from datetime import datetime
        AnimeStoryItem(
            title="Fresh Tier 1 Story",
            source="rss_ann",
            trend_name="fresh tier story",
            published_at=datetime.now(UTC).isoformat(),
        )
        config = {"tier_1": {"sources": [{"name": "ANN", "type": "rss", "url": "https://example.com/feed"}]}}
        with patch("fd_strategies.fetch_anime_news.feedparser.parse") as mock_parse:
            mock_parse.return_value = {
                "entries": [{"title": "Fresh Tier 1 Story", "link": "https://example.com/1"}]
            }
            fetcher = AnimeRSSFetcher(config)
            items = fetcher.fetch_all()

        # Fresh Tier-1 single source should be EMERGING
        assert any(i.trend_cycle_stage == "emerging" for i in items)


class TestFetchAll:
    def test_returns_list_of_dicts(self):
        with patch("fd_strategies.fetch_anime_news.AnimeRSSFetcher") as mock_cls:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch_all.return_value = [
                AnimeStoryItem(title="Test", source="rss_ann"),
            ]
            mock_cls.return_value = mock_fetcher

            results = fetch_all_anime_news(config={})
            assert len(results) == 1
            assert results[0]["source"] == "rss_ann"
