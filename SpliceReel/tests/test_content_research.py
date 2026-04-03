"""Tests for SpliceReel content research — get_sources() returns 3-tier structure."""

import pytest
from sr_strategies.content_research import MovieContentResearchStrategy


@pytest.fixture
def strategy():
    return MovieContentResearchStrategy()


class TestGetSources:
    def test_returns_three_tiers(self, strategy):
        sources = strategy.get_sources()
        assert "tier_1" in sources
        assert "tier_2" in sources
        assert "tier_3" in sources

    def test_tier_1_has_sources(self, strategy):
        sources = strategy.get_sources()
        tier_1_sources = sources["tier_1"].get("sources", [])
        assert len(tier_1_sources) >= 1

    def test_tier_1_has_tmdb(self, strategy):
        sources = strategy.get_sources()
        tier_1_names = [s["name"] for s in sources["tier_1"]["sources"]]
        assert any("TMDB" in n for n in tier_1_names)

    def test_tier_2_has_rss_sources(self, strategy):
        sources = strategy.get_sources()
        tier_2_types = [s.get("type") for s in sources["tier_2"]["sources"]]
        assert "rss" in tier_2_types

    def test_tier_3_has_sources(self, strategy):
        sources = strategy.get_sources()
        tier_3_sources = sources["tier_3"].get("sources", [])
        assert len(tier_3_sources) >= 1

    def test_all_tiers_have_refresh_interval(self, strategy):
        sources = strategy.get_sources()
        for tier_name in ["tier_1", "tier_2", "tier_3"]:
            tier = sources[tier_name]
            assert "refresh_interval_minutes" in tier


class TestExecute:
    def test_execute_returns_context(self, strategy):
        ctx = {"stories": []}
        result = strategy.execute(ctx)
        assert "run_stats" in result
        assert "fetch" in result["run_stats"]
        assert "tmdb_count" in result["run_stats"]["fetch"]
