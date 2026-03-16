"""Tests to verify ai_news → ai_creators niche ID migration.

All lookup dicts must have ai_creators as the canonical key.
ai_news must remain as a backward-compat alias key that resolves
to the same value.
"""
from genlab_core.media.video_sourcer import NICHE_SUBREDDITS
from genlab_core.media.trending_video_fetcher import (
    YOUTUBE_CATEGORIES,
    NICHE_SEARCH_KEYWORDS,
    MIN_VIEW_VELOCITY,
)
from genlab_core.media.download_top_videos import _NICHE_KEYWORDS
from genlab_core.intel.google_trends import TRENDS_CATEGORIES, NICHE_SEED_KEYWORDS
from genlab_core.scoring.composite_scorer import (
    DEFAULT_VELOCITY_THRESHOLDS,
    DEFAULT_MIN_COMPOSITE,
)


class TestAiCreatorsCanonical:
    """ai_creators is the canonical key in all lookup dicts."""

    def test_video_sourcer_subreddits(self):
        assert "ai_creators" in NICHE_SUBREDDITS
        assert "ai_news" in NICHE_SUBREDDITS  # backward compat alias
        assert NICHE_SUBREDDITS["ai_creators"] == NICHE_SUBREDDITS["ai_news"]

    def test_youtube_categories(self):
        assert "ai_creators" in YOUTUBE_CATEGORIES
        assert "ai_news" in YOUTUBE_CATEGORIES  # backward compat alias
        assert YOUTUBE_CATEGORIES["ai_creators"] == YOUTUBE_CATEGORIES["ai_news"]

    def test_niche_search_keywords(self):
        assert "ai_creators" in NICHE_SEARCH_KEYWORDS
        assert "ai_news" in NICHE_SEARCH_KEYWORDS  # backward compat alias
        assert NICHE_SEARCH_KEYWORDS["ai_creators"] == NICHE_SEARCH_KEYWORDS["ai_news"]

    def test_min_view_velocity(self):
        assert "ai_creators" in MIN_VIEW_VELOCITY
        assert "ai_news" in MIN_VIEW_VELOCITY  # backward compat alias
        assert MIN_VIEW_VELOCITY["ai_creators"] == MIN_VIEW_VELOCITY["ai_news"]

    def test_download_niche_keywords(self):
        assert "ai_creators" in _NICHE_KEYWORDS
        assert "ai_news" in _NICHE_KEYWORDS  # backward compat alias
        assert _NICHE_KEYWORDS["ai_creators"] == _NICHE_KEYWORDS["ai_news"]

    def test_trends_categories(self):
        assert "ai_creators" in TRENDS_CATEGORIES
        assert "ai_news" in TRENDS_CATEGORIES  # backward compat alias
        assert TRENDS_CATEGORIES["ai_creators"] == TRENDS_CATEGORIES["ai_news"]

    def test_trends_seed_keywords(self):
        assert "ai_creators" in NICHE_SEED_KEYWORDS
        assert "ai_news" in NICHE_SEED_KEYWORDS  # backward compat alias
        assert NICHE_SEED_KEYWORDS["ai_creators"] == NICHE_SEED_KEYWORDS["ai_news"]

    def test_velocity_thresholds(self):
        assert "ai_creators" in DEFAULT_VELOCITY_THRESHOLDS
        assert "ai_news" in DEFAULT_VELOCITY_THRESHOLDS  # backward compat alias
        assert DEFAULT_VELOCITY_THRESHOLDS["ai_creators"] == DEFAULT_VELOCITY_THRESHOLDS["ai_news"]

    def test_min_composite(self):
        assert "ai_creators" in DEFAULT_MIN_COMPOSITE
        assert "ai_news" in DEFAULT_MIN_COMPOSITE  # backward compat alias
        assert DEFAULT_MIN_COMPOSITE["ai_creators"] == DEFAULT_MIN_COMPOSITE["ai_news"]


class TestCommentProcessorPersonaAlias:
    """Comment processor persona loading resolves ai_news ↔ ai_creators."""

    def test_load_persona_ai_creators_resolves_via_alias(self):
        """ai_creators resolves to ai_news.yaml persona via alias fallback."""
        from genlab_core.engagement.comment_processor import _load_persona
        # The alias mechanism tries ai_creators.yaml first, then ai_news.yaml.
        # ai_news.yaml exists in personas/, so this should succeed.
        persona = _load_persona("ai_creators")
        assert persona is not None

    def test_load_persona_ai_news_loads_directly(self):
        """ai_news.yaml exists directly in personas/."""
        from genlab_core.engagement.comment_processor import _load_persona
        persona = _load_persona("ai_news")
        assert persona is not None
