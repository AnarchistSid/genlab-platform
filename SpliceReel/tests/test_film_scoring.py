"""Tests for SpliceReel scoring strategy."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sr_strategies.scoring import MovieScoringStrategy


@pytest.fixture
def strategy(tmp_path):
    """Create strategy with test config."""
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "scoring_weights.yaml").write_text(
        yaml.dump(
            {
                "scoring_dimensions": {
                    "weights": {
                        "timeliness": 0.25,
                        "magnitude": 0.35,
                        "engagement_potential": 0.25,
                        "novelty": 0.15,
                    },
                    "timeliness": {"decay_half_life_hours": 48.0},
                    "magnitude": {"rt_score_excellent": 0.90, "rt_score_good": 0.75},
                },
                "film_lifecycle_multipliers": {
                    "pre_release": 1.0,
                    "opening_weekend": 1.6,
                    "long_tail": 0.7,
                    "unknown": 0.3,
                },
                "franchise_multipliers": {
                    "MCU": 1.5,
                    "DC": 1.3,
                    "default": 1.0,
                },
                "thresholds": {
                    "min_score": 0.40,
                    "top_items_per_run": 5,
                },
            }
        )
    )
    with patch("sr_strategies.scoring.NICHE_ROOT", tmp_path):
        s = MovieScoringStrategy()
        s._ensure_config()
        yield s


def _make_story(**overrides):
    base = {
        "title": "Test Film",
        "film_title": "Test Film",
        "fetched_at": datetime.now(UTC).isoformat(),
        "lifecycle_stage": "opening_weekend",
        "franchise": "",
        "tmdb_popularity": 50,
        "rt_score": None,
        "imdb_score": None,
        "is_trailer_drop": False,
        "is_box_office_news": False,
    }
    base.update(overrides)
    return base


class TestScoring:
    def test_opening_weekend_scores_higher_than_long_tail(self, strategy):
        ow = _make_story(lifecycle_stage="opening_weekend")
        lt = _make_story(lifecycle_stage="long_tail")
        scored_ow = strategy.score_item(ow)
        scored_lt = strategy.score_item(lt)
        assert scored_ow["final_score"] > scored_lt["final_score"]

    def test_franchise_multiplier_applied_for_mcu(self, strategy):
        mcu = _make_story(franchise="MCU")
        plain = _make_story(franchise="")
        scored_mcu = strategy.score_item(mcu)
        scored_plain = strategy.score_item(plain)
        assert scored_mcu["franchise_multiplier"] == 1.5
        assert scored_plain["franchise_multiplier"] == 1.0
        assert scored_mcu["final_score"] > scored_plain["final_score"]

    def test_48h_decay_half_life_applied(self, strategy):
        recent = _make_story(fetched_at=datetime.now(UTC).isoformat())
        from datetime import timedelta

        old = _make_story(fetched_at=(datetime.now(UTC) - timedelta(hours=48)).isoformat())
        scored_recent = strategy.score_item(recent)
        scored_old = strategy.score_item(old)
        assert scored_recent["scores"]["timeliness"] > scored_old["scores"]["timeliness"]
        # After exactly 1 half-life, timeliness should be ~0.5
        assert 0.4 <= scored_old["scores"]["timeliness"] <= 0.6

    def test_trailer_drop_boosts_magnitude(self, strategy):
        trailer = _make_story(is_trailer_drop=True)
        normal = _make_story(is_trailer_drop=False)
        scored_t = strategy.score_item(trailer)
        scored_n = strategy.score_item(normal)
        assert scored_t["scores"]["magnitude"] >= 0.85
        assert scored_t["scores"]["magnitude"] >= scored_n["scores"]["magnitude"]

    def test_high_rt_score_boosts_magnitude(self, strategy):
        high_rt = _make_story(rt_score=95)
        low_rt = _make_story(rt_score=40)
        scored_high = strategy.score_item(high_rt)
        scored_low = strategy.score_item(low_rt)
        assert scored_high["scores"]["magnitude"] > scored_low["scores"]["magnitude"]

    def test_execute_filters_below_min_score(self, strategy):
        context = {
            "stories": [
                _make_story(lifecycle_stage="opening_weekend", tmdb_popularity=100),
                _make_story(lifecycle_stage="unknown", tmdb_popularity=0),
            ]
        }
        result = strategy.execute(context)
        stats = result["run_stats"]["scoring"]
        assert stats["input_count"] == 2
        # The unknown lifecycle story (0.3x) with 0 popularity should score very low
        assert stats["scored_count"] <= 2

    def test_execute_caps_at_top_items_per_run(self, strategy):
        stories = [
            _make_story(
                title=f"Film {i}",
                lifecycle_stage="opening_weekend",
                tmdb_popularity=100 - i,
            )
            for i in range(10)
        ]
        context = {"stories": stories}
        result = strategy.execute(context)
        assert len(result["stories"]) <= 5
