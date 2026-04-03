"""Tests for FilmLifecycleStage detection."""

from datetime import UTC, datetime, timedelta

from sr_strategies.film_lifecycle import (
    FilmLifecycleStage,
    detect_lifecycle_stage,
    get_lifecycle_score_multiplier,
)


class TestDetectLifecycleStage:
    def test_film_releasing_in_15_days_is_pre_release(self):
        release = datetime.now(UTC) + timedelta(days=15)
        assert detect_lifecycle_stage(release) == FilmLifecycleStage.PRE_RELEASE

    def test_film_released_24h_ago_is_opening_weekend(self):
        release = datetime.now(UTC) - timedelta(hours=24)
        assert detect_lifecycle_stage(release) == FilmLifecycleStage.OPENING_WEEKEND

    def test_film_released_3_days_ago_is_opening_weekend(self):
        release = datetime.now(UTC) - timedelta(hours=70)
        assert detect_lifecycle_stage(release) == FilmLifecycleStage.OPENING_WEEKEND

    def test_film_released_4_days_ago_is_long_tail(self):
        release = datetime.now(UTC) - timedelta(days=4)
        assert detect_lifecycle_stage(release) == FilmLifecycleStage.LONG_TAIL

    def test_film_released_22_days_ago_is_unknown(self):
        release = datetime.now(UTC) - timedelta(days=22)
        assert detect_lifecycle_stage(release) == FilmLifecycleStage.UNKNOWN

    def test_unknown_release_date_returns_unknown(self):
        assert detect_lifecycle_stage(None) == FilmLifecycleStage.UNKNOWN

    def test_film_releasing_in_60_days_is_unknown(self):
        release = datetime.now(UTC) + timedelta(days=60)
        assert detect_lifecycle_stage(release) == FilmLifecycleStage.UNKNOWN

    def test_custom_config_opening_hours(self):
        release = datetime.now(UTC) - timedelta(hours=100)
        config = {"opening_weekend_hours": 120}
        assert detect_lifecycle_stage(release, config=config) == FilmLifecycleStage.OPENING_WEEKEND


class TestLifecycleScoreMultiplier:
    def test_opening_weekend_is_highest(self):
        config = {
            "film_lifecycle_multipliers": {
                "pre_release": 1.0,
                "opening_weekend": 1.6,
                "long_tail": 0.7,
            }
        }
        ow = get_lifecycle_score_multiplier(FilmLifecycleStage.OPENING_WEEKEND, config)
        pr = get_lifecycle_score_multiplier(FilmLifecycleStage.PRE_RELEASE, config)
        lt = get_lifecycle_score_multiplier(FilmLifecycleStage.LONG_TAIL, config)
        assert ow > pr > lt

    def test_multiplier_loaded_from_config(self):
        config = {"film_lifecycle_multipliers": {"opening_weekend": 2.0}}
        result = get_lifecycle_score_multiplier(FilmLifecycleStage.OPENING_WEEKEND, config)
        assert result == 2.0

    def test_defaults_when_config_missing(self):
        result = get_lifecycle_score_multiplier(FilmLifecycleStage.OPENING_WEEKEND, {})
        assert result == 1.6
