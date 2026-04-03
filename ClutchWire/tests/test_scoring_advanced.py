"""Tests for ClutchWire scoring — live event bonus, upset/record multipliers, 2h decay."""

from datetime import UTC, datetime, timedelta

import pytest
from cw_strategies.scoring import SportScoringStrategy


@pytest.fixture
def strategy():
    return SportScoringStrategy()


def _make_item(hours_ago=0, **kwargs):
    fetched = datetime.now(UTC) - timedelta(hours=hours_ago)
    base = {
        "title": "Test Story",
        "game_type": "regular_season",
        "upvotes": 500,
        "comment_count": 50,
        "fetched_at": fetched.isoformat(),
        "novelty_score": 0.5,
    }
    base.update(kwargs)
    return base


class TestLiveEventBonus:
    def test_live_event_scores_higher(self, strategy):
        live = strategy.score_item(_make_item(is_live=True))
        not_live = strategy.score_item(_make_item(is_live=False))
        assert live["final_score"] > not_live["final_score"]

    def test_live_bonus_is_multiplicative(self, strategy):
        live = strategy.score_item(_make_item(is_live=True))
        not_live = strategy.score_item(_make_item(is_live=False))
        ratio = live["final_score"] / not_live["final_score"]
        # Should be ~1.5x (the live_event_bonus from config)
        assert 1.4 <= ratio <= 1.6


class TestUpsetMultiplier:
    def test_upset_scores_higher(self, strategy):
        upset = strategy.score_item(_make_item(is_upset=True))
        normal = strategy.score_item(_make_item(is_upset=False))
        assert upset["final_score"] > normal["final_score"]

    def test_upset_multiplier_ratio(self, strategy):
        upset = strategy.score_item(_make_item(is_upset=True))
        normal = strategy.score_item(_make_item(is_upset=False))
        ratio = upset["final_score"] / normal["final_score"]
        assert 1.3 <= ratio <= 1.5


class TestRecordMultiplier:
    def test_record_scores_higher(self, strategy):
        record = strategy.score_item(_make_item(is_record=True))
        normal = strategy.score_item(_make_item(is_record=False))
        assert record["final_score"] > normal["final_score"]

    def test_record_multiplier_ratio(self, strategy):
        record = strategy.score_item(_make_item(is_record=True))
        normal = strategy.score_item(_make_item(is_record=False))
        ratio = record["final_score"] / normal["final_score"]
        assert 1.2 <= ratio <= 1.4


class TestTwoHourDecay:
    def test_score_decays_with_age(self, strategy):
        fresh = strategy.score_item(_make_item(hours_ago=0))
        old = strategy.score_item(_make_item(hours_ago=4))
        assert fresh["final_score"] > old["final_score"]

    def test_two_hour_half_life(self, strategy):
        """After 2 hours, recency score should be ~0.5."""
        item = _make_item(hours_ago=2)
        strategy._ensure_config()
        recency = strategy._score_recency(item)
        assert 0.4 <= recency <= 0.6, f"Expected ~0.5, got {recency}"

    def test_four_hour_quarter_life(self, strategy):
        """After 4 hours (2 half-lives), recency should be ~0.25."""
        item = _make_item(hours_ago=4)
        strategy._ensure_config()
        recency = strategy._score_recency(item)
        assert 0.2 <= recency <= 0.3, f"Expected ~0.25, got {recency}"


class TestCombinedMultipliers:
    def test_live_upset_stacks(self, strategy):
        """Live + upset should multiply together."""
        both = strategy.score_item(_make_item(is_live=True, is_upset=True))
        neither = strategy.score_item(_make_item(is_live=False, is_upset=False))
        # Should be ~1.5 * 1.4 = 2.1x
        ratio = both["final_score"] / neither["final_score"]
        assert ratio > 2.0

    def test_score_field_present(self, strategy):
        """Scored items should have both 'score' and 'final_score'."""
        item = strategy.score_item(_make_item())
        assert "score" in item
        assert "final_score" in item
        assert item["score"] == item["final_score"]
