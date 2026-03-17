"""Tests for genlab_core.learning.reward_shaper — monetisation-aware rewards."""

import pytest
from genlab_core.learning.reward_shaper import (
    BASE_WEIGHTS,
    RewardShaper,
    _normalise_metric,
    _normalise_weights,
)


class TestBaseReward:
    def test_base_reward_computed_from_metrics(self):
        """YouTube base reward with realistic metrics."""
        shaper = RewardShaper()
        reward = shaper.compute_reward(
            platform="youtube",
            metrics={
                "views": 5000,
                "avg_view_duration": 30.0,
                "subscriber_gained": 5,
                "like_rate": 0.02,
                "comment_rate": 0.005,
            },
        )
        assert 0.0 < reward < 1.0

    def test_zero_views_returns_zero_reward(self):
        shaper = RewardShaper()
        reward = shaper.compute_reward(
            platform="youtube",
            metrics={"views": 0},
        )
        assert reward == 0.0

    def test_reward_capped_at_one(self):
        """Even extreme metrics don't exceed 1.0."""
        shaper = RewardShaper()
        reward = shaper.compute_reward(
            platform="youtube",
            metrics={
                "views": 999999,
                "avg_view_duration": 999,
                "subscriber_gained": 999,
                "like_rate": 0.99,
                "comment_rate": 0.99,
            },
        )
        assert reward <= 1.0

    def test_empty_metrics_returns_zero(self):
        shaper = RewardShaper()
        reward = shaper.compute_reward(platform="youtube", metrics={})
        assert reward == 0.0


class TestThresholdBoost:
    def test_boost_activates_above_trigger(self):
        """When within 20% of YouTube subscriber threshold, boost metric weight increases."""
        # Channel at 900/1000 subscribers = 90%, within 20% proximity
        shaper = RewardShaper()
        boosted = shaper.get_adjusted_weights(
            platform="youtube",
            channel_metrics={"subscriber_count": 900, "watch_hours_total": 100},
        )

        base = _normalise_weights(dict(BASE_WEIGHTS["youtube"]))
        # subscriber_gained weight should be boosted
        assert boosted["subscriber_gained"] > base["subscriber_gained"]

    def test_boost_zero_below_trigger(self):
        """When far from threshold (50%), no boost applied."""
        shaper = RewardShaper()
        no_boost = shaper.get_adjusted_weights(
            platform="youtube",
            channel_metrics={"subscriber_count": 100, "watch_hours_total": 100},
        )

        base = _normalise_weights(dict(BASE_WEIGHTS["youtube"]))
        # Weights should be identical (just normalised)
        for key in base:
            assert abs(no_boost[key] - base[key]) < 0.001

    def test_boost_zero_for_no_threshold_platform(self):
        """Instagram has no fixed threshold — weights stay at base."""
        shaper = RewardShaper()
        weights = shaper.get_adjusted_weights(
            platform="instagram",
            channel_metrics={},  # no thresholds to check
        )
        base = _normalise_weights(dict(BASE_WEIGHTS["instagram"]))
        for key in base:
            assert abs(weights[key] - base[key]) < 0.001

    def test_threshold_proximity_uses_channel_metrics_fn(self):
        """When channel_metrics_fn is provided, it's called to get metrics."""
        called = {}

        def mock_channel_fn(platform: str) -> dict:
            called["platform"] = platform
            return {"subscriber_count": 950, "watch_hours_total": 3500}

        shaper = RewardShaper(channel_metrics_fn=mock_channel_fn)
        weights = shaper.get_adjusted_weights(platform="youtube")

        assert called["platform"] == "youtube"
        base = _normalise_weights(dict(BASE_WEIGHTS["youtube"]))
        # Both thresholds within 20% — both boost metrics should be boosted
        assert weights["subscriber_gained"] > base["subscriber_gained"]
        assert weights["avg_view_duration"] > base["avg_view_duration"]


class TestNormalisation:
    def test_weights_sum_to_one(self):
        result = _normalise_weights({"a": 0.3, "b": 0.7})
        assert abs(sum(result.values()) - 1.0) < 0.001

    def test_metric_normalisation_caps_at_one(self):
        # views target for youtube = 10000, so 20000 views -> capped at 1.0
        assert _normalise_metric("views", 20000, "youtube") == 1.0

    def test_metric_normalisation_proportional(self):
        # 5000 views out of 10000 target = 0.5
        assert _normalise_metric("views", 5000, "youtube") == pytest.approx(0.5)


class TestMonetisationRewardShaper:
    def test_threshold_boost_zero_when_trigger_equals_one(self):
        """trigger=1.0 must return 0.0, not raise ZeroDivisionError."""
        from genlab_core.learning.reward_shaper import MonetisationRewardShaper

        shaper = MonetisationRewardShaper(
            config={"reward": {"threshold_proximity_trigger": 1.0, "threshold_proximity_bonus": 0.15}},
            niche_id="gaming",
            monetisation_config={"platforms": {"youtube": {"threshold_watch_hours": 4000}}},
        )
        result = shaper._compute_threshold_boost({"watch_time_hours": 4001.0}, "youtube")
        assert result == 0.0

    def test_threshold_boost_zero_when_below_trigger(self):
        from genlab_core.learning.reward_shaper import MonetisationRewardShaper

        shaper = MonetisationRewardShaper(
            config={}, niche_id="gaming",
            monetisation_config={"platforms": {"youtube": {"threshold_watch_hours": 4000}}},
        )
        raw = shaper._compute_threshold_boost({"watch_time_hours": 100.0}, "youtube")
        assert raw == 0.0

    def test_threshold_boost_positive_near_threshold(self):
        from genlab_core.learning.reward_shaper import MonetisationRewardShaper

        shaper = MonetisationRewardShaper(
            config={}, niche_id="gaming",
            monetisation_config={"platforms": {"youtube": {"threshold_watch_hours": 4000}}},
        )
        # 3800/4000 = 95% — above default trigger of 80%
        raw = shaper._compute_threshold_boost({"watch_time_hours": 3800.0}, "youtube")
        assert raw > 0.0

    def test_compute_returns_positive_for_valid_metrics(self):
        from genlab_core.learning.reward_shaper import MonetisationRewardShaper

        shaper = MonetisationRewardShaper(
            config={}, niche_id="gaming",
            monetisation_config={"platforms": {"youtube": {"threshold_watch_hours": 4000}}},
        )
        reward = shaper.compute(
            metrics={"completion_rate": 0.5, "engagement_rate": 0.05,
                     "views": 1000, "shares": 10, "watch_time_hours": 3800},
            platform="youtube", window="48h",
        )
        assert reward > 0.0

    def test_share_scale_read_from_config(self):
        """share_rate_scale from bandit_blend config changes reward computation."""
        from genlab_core.learning.reward_shaper import MonetisationRewardShaper

        metrics = {
            "completion_rate": 0.5, "engagement_rate": 0.0,
            "views": 1000, "shares": 50,
        }

        # Default scale (10) — shares/views = 0.05, scaled = 0.5
        shaper_default = MonetisationRewardShaper(
            config={}, niche_id="gaming", monetisation_config={},
        )
        reward_default = shaper_default.compute(metrics, "youtube", "48h")

        # Custom scale (40) — shares/views = 0.05, scaled = min(1.0, 2.0) = 1.0
        shaper_custom = MonetisationRewardShaper(
            config={"reward": {"bandit_blend": {"share_rate_scale": 40}}},
            niche_id="gaming", monetisation_config={},
        )
        reward_custom = shaper_custom.compute(metrics, "youtube", "48h")

        # Higher scale → shares contribute more → higher reward
        assert reward_custom > reward_default

    def test_share_scale_fallback_on_missing_config(self):
        """Falls back to scale=10 when bandit_blend config is missing."""
        from genlab_core.learning.reward_shaper import MonetisationRewardShaper

        metrics = {
            "completion_rate": 0.0, "engagement_rate": 0.0,
            "views": 1000, "shares": 100,  # shares/views = 0.1, * 10 = 1.0
        }

        shaper = MonetisationRewardShaper(
            config={}, niche_id="test", monetisation_config={},
        )
        reward = shaper.compute(metrics, "youtube", "48h")
        # With default weights: shares weight=0.25, shares_norm=min(1.0, 0.1*10)=1.0
        # base = 0.25 * 1.0 = 0.25
        assert reward > 0.0
