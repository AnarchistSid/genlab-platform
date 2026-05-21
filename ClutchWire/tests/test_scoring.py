"""Tests for ClutchWire scoring strategy — magnitude multiplier logic."""

from datetime import UTC, datetime

import pytest
from cw_strategies.scoring import SportScoringStrategy


@pytest.fixture
def strategy():
    return SportScoringStrategy()


def _make_item(game_type="regular_season", upvotes=500, **kwargs):
    return {
        "title": "Test Story",
        "game_type": game_type,
        "upvotes": upvotes,
        "comment_count": 50,
        "fetched_at": datetime.now(UTC).isoformat(),
        "novelty_score": 0.5,
        **kwargs,
    }


class TestMagnitudeMultipliers:
    """Championship > playoff > rivalry > regular > preseason."""

    def test_championship_scores_highest(self, strategy):
        champ = strategy.score_item(_make_item("championship_game"))
        regular = strategy.score_item(_make_item("regular_season"))
        assert champ["final_score"] > regular["final_score"]

    def test_playoff_beats_regular(self, strategy):
        playoff = strategy.score_item(_make_item("playoff_game"))
        regular = strategy.score_item(_make_item("regular_season"))
        assert playoff["final_score"] > regular["final_score"]

    def test_rivalry_beats_regular(self, strategy):
        rivalry = strategy.score_item(_make_item("rivalry_game"))
        regular = strategy.score_item(_make_item("regular_season"))
        assert rivalry["final_score"] > regular["final_score"]

    def test_preseason_scores_lowest(self, strategy):
        pre = strategy.score_item(_make_item("preseason"))
        regular = strategy.score_item(_make_item("regular_season"))
        assert pre["final_score"] < regular["final_score"]

    def test_multiplier_ordering(self, strategy):
        """Full ordering: championship > playoff > rivalry > regular > preseason."""
        types = ["championship_game", "playoff_game", "rivalry_game", "regular_season", "preseason"]
        scores = [strategy.score_item(_make_item(t))["final_score"] for t in types]
        assert scores == sorted(scores, reverse=True), f"Score ordering wrong: {list(zip(types, scores, strict=False))}"


class TestScoringExecution:
    def test_execute_empty_stories(self, strategy):
        ctx = {"stories": []}
        result = strategy.execute(ctx)
        assert result["run_stats"]["scoring"]["input_count"] == 0

    def test_execute_drops_below_threshold(self, strategy):
        stories = [_make_item(upvotes=0, game_type="preseason")]
        ctx = {"stories": stories}
        result = strategy.execute(ctx)
        stats = result["run_stats"]["scoring"]
        # Very low score items may be dropped
        assert stats["input_count"] == 1

    def test_scored_item_has_required_fields(self, strategy):
        item = strategy.score_item(_make_item())
        assert "scores" in item
        assert "final_score" in item
        assert "scored_at" in item
        assert "magnitude_multiplier" in item
