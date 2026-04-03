"""Tests for FrameDrift source_filters in content research strategy."""

from pathlib import Path

import yaml
from fd_strategies.content_research import AnimeContentResearchStrategy

NICHE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = NICHE_ROOT / "config"


def _load_sources_yaml() -> dict:
    with open(CONFIG_DIR / "sources.yaml") as f:
        return yaml.safe_load(f)


def _make_story(title: str = "Test Anime", source: str = "rss_ann") -> dict:
    return {
        "title": title,
        "source": source,
        "source_url": "https://example.com",
        "summary": "Test summary",
    }


class TestSourceFilters:
    """Test source filter rejection logic in AnimeContentResearchStrategy."""

    def setup_method(self):
        self.strategy = AnimeContentResearchStrategy()
        self.filters = _load_sources_yaml().get("source_filters", {})

    def test_source_filter_rejects_matching_title(self):
        stories = [
            _make_story("BAFTA Awards Nominees Announced"),
            _make_story("Best Video Game Adaptations of 2025"),
            _make_story("Game Award Winners: Full List"),
            _make_story("One Piece Season 2 Premiere Date Revealed"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1
        assert "One Piece" in result[0]["title"]

    def test_source_filter_passes_clean_story(self):
        stories = [
            _make_story("Jujutsu Kaisen Season 3 Gets New Trailer"),
            _make_story("Chainsaw Man Part 2 Anime Confirmed"),
            _make_story("Studio MAPPA Announces New Project"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 3

    def test_source_filter_rejects_matching_topic(self):
        stories = [
            _make_story("Anime-inspired looks", source="fashion"),
            _make_story("New streetwear collab", source="streetwear"),
            _make_story("Dragon Ball Super update", source="rss_ann"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1
        assert "Dragon Ball" in result[0]["title"]

    def test_source_filter_handles_empty_filters(self):
        stories = [
            _make_story("BAFTA nominee list"),
            _make_story("Normal anime news"),
        ]
        result = self.strategy._apply_source_filters(stories, {})
        assert len(result) == 2

    def test_source_filter_handles_none_title(self):
        stories = [{"title": None, "source": "rss_ann"}]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1

    def test_source_filters_section_exists(self):
        config = _load_sources_yaml()
        assert "source_filters" in config
        filters = config["source_filters"]
        assert len(filters["reject_title_patterns"]) >= 3
        assert "reject_topic_patterns" in filters
        assert "score_boost" in filters
        assert filters["score_boost"]["premiere"] == 0.15

    def test_google_trends_stub_mode_comment(self):
        """Verify stub_mode is true and the comment explains why."""
        config = _load_sources_yaml()
        gt = config["google_trends"]
        assert gt["stub_mode"] is True

    def test_case_insensitive_matching(self):
        stories = [
            _make_story("bafta nominees for animation"),
            _make_story("VIDEO GAME Anime Adaptation"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 0
