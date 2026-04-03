"""Tests for SpliceReel source_filters in content research strategy."""

from pathlib import Path

import yaml
from sr_strategies.content_research import MovieContentResearchStrategy

NICHE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = NICHE_ROOT / "config"


def _load_sources_yaml() -> dict:
    with open(CONFIG_DIR / "sources.yaml") as f:
        return yaml.safe_load(f)


def _make_story(title: str = "Test Movie", source: str = "tmdb_trending") -> dict:
    return {
        "title": title,
        "source": source,
        "source_url": "https://example.com",
        "summary": "Test summary",
    }


class TestSourceFilters:
    """Test source filter rejection logic in MovieContentResearchStrategy."""

    def setup_method(self):
        self.strategy = MovieContentResearchStrategy()
        self.filters = _load_sources_yaml().get("source_filters", {})

    def test_source_filter_rejects_matching_title(self):
        stories = [
            _make_story("Opinion: Why Marvel is Dying"),
            _make_story("Best of 2025: Top 10 Films"),
            _make_story("Ranking Every MCU Movie"),
            _make_story("Explained: The Multiverse Saga"),
            _make_story("New Blade Runner 2099 Trailer Drops"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1
        assert "Blade Runner" in result[0]["title"]

    def test_source_filter_passes_clean_story(self):
        stories = [
            _make_story("Dune Part 3 Confirmed by Denis Villeneuve"),
            _make_story("Avatar 3 Breaks Opening Weekend Record"),
            _make_story("Tom Holland Signs On for Spider-Man 4"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 3

    def test_source_filter_rejects_matching_topic(self):
        """SpliceReel has no reject_topic_patterns, so topic filter is a no-op."""
        stories = [_make_story("Test", source="some_topic")]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1

    def test_source_filter_handles_empty_filters(self):
        stories = [
            _make_story("Opinion piece about cinema"),
            _make_story("Normal movie news"),
        ]
        result = self.strategy._apply_source_filters(stories, {})
        assert len(result) == 2

    def test_source_filter_handles_none_title(self):
        stories = [{"title": None, "source": "tmdb_trending"}]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1

    def test_source_filters_section_exists(self):
        config = _load_sources_yaml()
        assert "source_filters" in config
        filters = config["source_filters"]
        assert len(filters["reject_title_patterns"]) >= 4
        assert "score_boost" in filters
        assert filters["score_boost"]["trailer"] == 0.15

    def test_worst_of_pattern_works(self):
        stories = [_make_story("Worst of 2025: Films That Flopped")]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 0

    def test_case_insensitive_matching(self):
        stories = [
            _make_story("OPINION: Studios Need to Change"),
            _make_story("An Essay on Cinema's Future"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 0
