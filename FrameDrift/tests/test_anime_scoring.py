"""Tests for FrameDrift scoring strategy."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fd_strategies.scoring import AnimeScoringStrategy


@pytest.fixture
def strategy(tmp_path):
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "scoring_weights.yaml").write_text(
        yaml.dump(
            {
                "scoring_dimensions": {
                    "weights": {
                        "timeliness": 0.30,
                        "magnitude": 0.35,
                        "engagement_potential": 0.25,
                        "novelty": 0.10,
                    },
                    "timeliness": {"decay_half_life_hours": 24.0},
                    "magnitude": {"celebrity_multiplier": 1.9, "collaboration_multiplier": 1.5},
                },
                "trend_cycle_multipliers": {
                    "emerging": 1.4,
                    "peak": 1.0,
                    "declining": 0.5,
                    "unknown": 0.7,
                },
                "thresholds": {
                    "min_score": 0.42,
                    "top_items_per_run": 4,
                },
            }
        )
    )
    with patch("fd_strategies.scoring.NICHE_ROOT", tmp_path):
        s = AnimeScoringStrategy()
        s._ensure_config()
        yield s


def _make_story(**overrides):
    base = {
        "title": "Test Anime Story",
        "fetched_at": datetime.now(UTC).isoformat(),
        "trend_cycle_stage": "peak",
        "source_mention_count": 2,
        "is_new_release": False,
        "is_creator_spotlight": False,
        "is_collab": False,
        "is_event_coverage": False,
    }
    base.update(overrides)
    return base


class TestAnimeScoring:
    def test_emerging_trend_scores_higher_than_declining(self, strategy):
        em = _make_story(trend_cycle_stage="emerging")
        dc = _make_story(trend_cycle_stage="declining")
        scored_em = strategy.score_item(em)
        scored_dc = strategy.score_item(dc)
        assert scored_em["final_score"] > scored_dc["final_score"]

    def test_creator_spotlight_gets_magnitude_boost(self, strategy):
        creator = _make_story(is_creator_spotlight=True)
        normal = _make_story(is_creator_spotlight=False)
        scored_c = strategy.score_item(creator)
        scored_n = strategy.score_item(normal)
        assert scored_c["scores"]["magnitude"] > scored_n["scores"]["magnitude"]

    def test_24h_decay_applied(self, strategy):
        recent = _make_story(fetched_at=datetime.now(UTC).isoformat())
        old = _make_story(fetched_at=(datetime.now(UTC) - timedelta(hours=24)).isoformat())
        scored_r = strategy.score_item(recent)
        scored_o = strategy.score_item(old)
        assert scored_r["scores"]["timeliness"] > scored_o["scores"]["timeliness"]
        # After 1 half-life, timeliness ~0.5
        assert 0.4 <= scored_o["scores"]["timeliness"] <= 0.6

    def test_trend_cycle_multiplier_from_config(self, strategy):
        em = _make_story(trend_cycle_stage="emerging")
        scored = strategy.score_item(em)
        assert scored["trend_cycle_multiplier"] == 1.4

    def test_high_mention_count_boosts_engagement(self, strategy):
        high = _make_story(source_mention_count=4)
        low = _make_story(source_mention_count=1)
        scored_h = strategy.score_item(high)
        scored_l = strategy.score_item(low)
        assert (
            scored_h["scores"]["engagement_potential"] > scored_l["scores"]["engagement_potential"]
        )
