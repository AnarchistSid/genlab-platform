"""Tests for FrameDrift TrendCycleStage detection."""

from datetime import UTC, datetime, timedelta

from fd_strategies.trend_cycle import (
    TrendCycleStage,
    detect_trend_cycle,
    get_trend_cycle_multiplier,
)


class TestDetectTrendCycle:
    def test_single_source_fresh_story_is_emerging(self):
        first_seen = datetime.now(UTC) - timedelta(hours=6)
        assert detect_trend_cycle(first_seen, source_mention_count=1) == TrendCycleStage.EMERGING

    def test_two_sources_24h_story_is_emerging(self):
        first_seen = datetime.now(UTC) - timedelta(hours=20)
        assert detect_trend_cycle(first_seen, source_mention_count=2) == TrendCycleStage.EMERGING

    def test_two_sources_48h_story_is_peak(self):
        first_seen = datetime.now(UTC) - timedelta(hours=48)
        assert detect_trend_cycle(first_seen, source_mention_count=2) == TrendCycleStage.PEAK

    def test_four_sources_48h_story_is_peak(self):
        first_seen = datetime.now(UTC) - timedelta(hours=48)
        assert detect_trend_cycle(first_seen, source_mention_count=4) == TrendCycleStage.PEAK

    def test_four_sources_96h_story_is_declining(self):
        first_seen = datetime.now(UTC) - timedelta(hours=96)
        assert detect_trend_cycle(first_seen, source_mention_count=4) == TrendCycleStage.DECLINING

    def test_story_older_than_96h_two_sources_is_declining(self):
        first_seen = datetime.now(UTC) - timedelta(hours=100)
        assert detect_trend_cycle(first_seen, source_mention_count=2) == TrendCycleStage.DECLINING

    def test_none_first_seen_returns_unknown(self):
        assert detect_trend_cycle(None, source_mention_count=1) == TrendCycleStage.UNKNOWN

    def test_single_source_old_story_returns_unknown(self):
        first_seen = datetime.now(UTC) - timedelta(hours=24)
        assert detect_trend_cycle(first_seen, source_mention_count=1) == TrendCycleStage.UNKNOWN


class TestTrendCycleMultiplier:
    def test_emerging_is_highest(self):
        config = {"trend_cycle_multipliers": {"emerging": 1.4, "peak": 1.0, "declining": 0.5}}
        em = get_trend_cycle_multiplier(TrendCycleStage.EMERGING, config)
        pk = get_trend_cycle_multiplier(TrendCycleStage.PEAK, config)
        dc = get_trend_cycle_multiplier(TrendCycleStage.DECLINING, config)
        assert em > pk > dc

    def test_declining_is_lowest(self):
        config = {"trend_cycle_multipliers": {"emerging": 1.4, "peak": 1.0, "declining": 0.5, "unknown": 0.7}}
        dc = get_trend_cycle_multiplier(TrendCycleStage.DECLINING, config)
        un = get_trend_cycle_multiplier(TrendCycleStage.UNKNOWN, config)
        assert dc < un

    def test_multiplier_loaded_from_config(self):
        config = {"trend_cycle_multipliers": {"emerging": 2.0}}
        result = get_trend_cycle_multiplier(TrendCycleStage.EMERGING, config)
        assert result == 2.0

    def test_defaults_when_config_missing(self):
        result = get_trend_cycle_multiplier(TrendCycleStage.EMERGING, {})
        assert result == 1.4
