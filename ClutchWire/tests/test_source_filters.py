"""Tests for ClutchWire source_filters in content research strategy."""

from pathlib import Path

import yaml
from cw_strategies.content_research import SportContentResearchStrategy

NICHE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = NICHE_ROOT / "config"


def _load_sources_yaml() -> dict:
    with open(CONFIG_DIR / "sources.yaml") as f:
        return yaml.safe_load(f)


def _make_story(title: str = "Test Story", source: str = "espn_news") -> dict:
    return {
        "title": title,
        "source": source,
        "source_url": "https://example.com",
        "summary": "Test summary",
    }


class TestSourceFilters:
    """Test source filter rejection logic in SportContentResearchStrategy."""

    def setup_method(self):
        self.strategy = SportContentResearchStrategy()
        self.filters = _load_sources_yaml().get("source_filters", {})

    def test_source_filter_rejects_matching_title(self):
        stories = [
            _make_story("NBA Fantasy Rankings Week 12"),
            _make_story("NFL Betting Tips for Sunday"),
            _make_story("NBA Power Rankings: Top 30"),
            _make_story("Best Players Available in Free Agency"),
            _make_story("NFL DFS Picks for Week 5"),
            _make_story("Real story about a game"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1
        assert result[0]["title"] == "Real story about a game"

    def test_source_filter_passes_clean_story(self):
        stories = [
            _make_story("LeBron James drops 40 in Lakers win"),
            _make_story("Manchester City clinches Premier League title"),
            _make_story("Patrick Mahomes throws game-winning TD"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 3

    def test_source_filter_rejects_matching_topic(self):
        """ClutchWire has no reject_topic_patterns, so topic filter is a no-op."""
        stories = [_make_story("Test", source="some_topic")]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1

    def test_source_filter_handles_empty_filters(self):
        stories = [
            _make_story("Fantasy Football Rankings"),
            _make_story("Normal story"),
        ]
        result = self.strategy._apply_source_filters(stories, {})
        assert len(result) == 2

    def test_source_filter_handles_none_title(self):
        stories = [{"title": None, "source": "espn_news"}]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 1

    def test_espn_scoreboard_removed(self):
        """Verify ClutchWire sources.yaml has no scoreboard section."""
        config = _load_sources_yaml()
        espn = config.get("espn_api", {})
        assert "scoreboard" not in espn, (
            "espn_api.scoreboard should be removed (0% conversion rate)"
        )
        assert "news" in espn, "espn_api.news should still exist"

    def test_source_filters_section_exists(self):
        config = _load_sources_yaml()
        assert "source_filters" in config
        filters = config["source_filters"]
        assert len(filters["reject_title_patterns"]) >= 5
        assert filters["min_priority_score_for_render"] == 0.5

    def test_case_insensitive_matching(self):
        stories = [
            _make_story("FANTASY Football Draft Guide"),
            _make_story("Podcast: Weekly NFL Recap"),
        ]
        result = self.strategy._apply_source_filters(stories, self.filters)
        assert len(result) == 0
