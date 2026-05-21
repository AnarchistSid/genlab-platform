"""Tests for GoogleTrendsIntel."""

from unittest.mock import MagicMock, patch

from genlab_core.intel.google_trends import (
    NICHE_SEED_KEYWORDS,
    TRENDS_CATEGORIES,
    GoogleTrendsIntel,
)


class TestGoogleTrendsIntel:
    def test_fallback_to_seed_keywords(self):
        """When all tiers fail (RSS + pytrends + cache), returns static seed keywords."""
        intel = GoogleTrendsIntel()
        with (
            patch.object(intel, "_get_rss_trending", side_effect=Exception("no network")),
            patch.object(intel, "_get_realtime_trending", side_effect=Exception("no network")),
            patch.object(intel, "_get_daily_trending", side_effect=Exception("no network")),
            patch("genlab_core.intel.google_trends._read_cache", return_value=None),
            patch("genlab_core.intel.google_trends._read_stale_cache", return_value=None),
        ):
            topics = intel.get_trending_topics("gaming", top_n=5)

        assert len(topics) > 0
        assert topics == NICHE_SEED_KEYWORDS["gaming"]

    def test_all_niches_have_seed_keywords(self):
        for niche in ["gaming", "sports", "movies", "anime", "ai_creators"]:
            assert niche in NICHE_SEED_KEYWORDS
            assert len(NICHE_SEED_KEYWORDS[niche]) >= 2

    def test_all_niches_have_trends_categories(self):
        for niche in ["gaming", "sports", "movies", "ai_creators"]:
            assert niche in TRENDS_CATEGORIES

    def test_realtime_trending_returns_list(self):
        intel = GoogleTrendsIntel()
        # Mock pytrends client
        mock_pt = MagicMock()
        import pandas as pd

        mock_pt.trending_searches.return_value = pd.DataFrame(
            {0: ["Valorant update", "NBA Finals", "New Movie Trailer", "AI breakthrough"]}
        )
        intel._pytrends = mock_pt

        topics = intel._get_realtime_trending("gaming")
        assert isinstance(topics, list)
        # "Valorant update" should match gaming seed keywords? Actually no,
        # but it should still be in the results
        assert len(topics) > 0

    def test_score_multiplier_default(self):
        intel = GoogleTrendsIntel()
        with patch.object(intel, "get_trending_topics", return_value=[]):
            score = intel.get_trending_score_multiplier("random topic", "gaming")
        assert score == 1.0

    def test_score_multiplier_trending(self):
        intel = GoogleTrendsIntel()
        with patch.object(
            intel,
            "get_trending_topics",
            return_value=["GTA 6", "Valorant", "Minecraft"],
        ):
            score = intel.get_trending_score_multiplier("GTA 6 release", "gaming")
        assert score == 3.0  # Top 5 match

    def test_score_multiplier_exception_returns_neutral(self):
        intel = GoogleTrendsIntel()
        with patch.object(intel, "get_trending_topics", side_effect=Exception("fail")):
            score = intel.get_trending_score_multiplier("anything", "gaming")
        assert score == 1.0
