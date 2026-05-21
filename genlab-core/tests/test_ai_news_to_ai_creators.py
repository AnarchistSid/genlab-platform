"""Tests to verify ai_news backward-compat aliases have been fully removed.

ai_creators is the only canonical key. ai_news must NOT appear in any lookup dict.
"""

from genlab_core.intel.google_trends import NICHE_SEED_KEYWORDS, TRENDS_CATEGORIES
from genlab_core.media.download_top_videos import _NICHE_KEYWORDS
from genlab_core.media.trending_video_fetcher import (
    MIN_VIEW_VELOCITY,
    NICHE_SEARCH_KEYWORDS,
    YOUTUBE_CATEGORIES,
)
from genlab_core.media.video_sourcer import NICHE_SUBREDDITS
from genlab_core.scoring.composite_scorer import (
    DEFAULT_MIN_COMPOSITE,
    DEFAULT_VELOCITY_THRESHOLDS,
)


class TestAiCreatorsCanonical:
    """ai_creators is the only key — ai_news aliases are removed."""

    def test_video_sourcer_subreddits(self):
        assert "ai_creators" in NICHE_SUBREDDITS
        assert "ai_news" not in NICHE_SUBREDDITS

    def test_youtube_categories(self):
        assert "ai_creators" in YOUTUBE_CATEGORIES
        assert "ai_news" not in YOUTUBE_CATEGORIES

    def test_niche_search_keywords(self):
        assert "ai_creators" in NICHE_SEARCH_KEYWORDS
        assert "ai_news" not in NICHE_SEARCH_KEYWORDS

    def test_min_view_velocity(self):
        assert "ai_creators" in MIN_VIEW_VELOCITY
        assert "ai_news" not in MIN_VIEW_VELOCITY

    def test_download_niche_keywords(self):
        assert "ai_creators" in _NICHE_KEYWORDS
        assert "ai_news" not in _NICHE_KEYWORDS

    def test_trends_categories(self):
        assert "ai_creators" in TRENDS_CATEGORIES
        assert "ai_news" not in TRENDS_CATEGORIES

    def test_trends_seed_keywords(self):
        assert "ai_creators" in NICHE_SEED_KEYWORDS
        assert "ai_news" not in NICHE_SEED_KEYWORDS

    def test_velocity_thresholds(self):
        assert "ai_creators" in DEFAULT_VELOCITY_THRESHOLDS
        assert "ai_news" not in DEFAULT_VELOCITY_THRESHOLDS

    def test_min_composite(self):
        assert "ai_creators" in DEFAULT_MIN_COMPOSITE
        assert "ai_news" not in DEFAULT_MIN_COMPOSITE
