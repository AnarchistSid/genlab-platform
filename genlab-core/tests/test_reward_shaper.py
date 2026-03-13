"""Tests for genlab_core.learning.reward_shaper.

Tests verify monetisation-aware weight adjustments and reward computation.
No external dependencies — all channel metrics are injected directly.
"""
from __future__ import annotations

import pytest

from genlab_core.learning.reward_shaper import (
    BASE_WEIGHTS,
    MONETISATION_THRESHOLDS,
    RewardShaper,
    _normalise_metric,
    _normalise_weights,
)


# ---------------------------------------------------------------------------
# 1. Instagram dm_send_rate has higher weight than views (base weights)
# ---------------------------------------------------------------------------


class TestBaseWeights:
    def test_instagram_dm_send_rate_higher_than_views(self):
        ig_weights = BASE_WEIGHTS["instagram"]
        assert ig_weights["dm_send_rate"] > ig_weights["views"]


# ---------------------------------------------------------------------------
# 2. YouTube boost: subscriber_count = 850 (85% of 1000) -> subscriber_gained
#    weight multiplied by 2.0
# ---------------------------------------------------------------------------


class TestYouTubeSubscriberBoost:
    def test_boost_when_at_85_percent(self):
        shaper = RewardShaper()
        channel = {"subscriber_count": 850.0}
        weights = shaper.get_adjusted_weights("youtube", channel)

        base = BASE_WEIGHTS["youtube"]["subscriber_gained"]
        # After boost, subscriber_gained should be larger than its base
        # proportion. The boost is 2.0x on base, then re-normalised.
        base_normalised = _normalise_weights(dict(BASE_WEIGHTS["youtube"]))
        assert weights["subscriber_gained"] > base_normalised["subscriber_gained"]


# ---------------------------------------------------------------------------
# 3. Facebook boost: minutes_viewed_60d = 520K (87% of 600K) ->
#    completion_rate weight multiplied by 3.0
# ---------------------------------------------------------------------------


class TestFacebookCompletionBoost:
    def test_boost_when_at_87_percent(self):
        shaper = RewardShaper()
        channel = {"minutes_viewed_60d": 520_000.0}
        weights = shaper.get_adjusted_weights("facebook", channel)

        base_normalised = _normalise_weights(dict(BASE_WEIGHTS["facebook"]))
        # completion_rate should be boosted above its base proportion
        assert weights["completion_rate"] > base_normalised["completion_rate"]


# ---------------------------------------------------------------------------
# 4. compute_reward: all-zero metrics returns 0.0
# ---------------------------------------------------------------------------


class TestZeroMetrics:
    def test_all_zero_returns_zero(self):
        shaper = RewardShaper()
        reward = shaper.compute_reward("youtube", {})
        assert reward == 0.0

    def test_explicit_zeros_return_zero(self):
        shaper = RewardShaper()
        metrics = {"views": 0.0, "avg_view_duration": 0.0, "subscriber_gained": 0.0}
        reward = shaper.compute_reward("youtube", metrics)
        assert reward == 0.0


# ---------------------------------------------------------------------------
# 5. compute_reward: returns float in [0, 1] for any input (property test)
# ---------------------------------------------------------------------------


class TestRewardBounds:
    def test_large_metrics_clamped_to_one(self):
        shaper = RewardShaper()
        # Absurdly large values should still produce reward <= 1.0
        metrics = {
            "views": 1_000_000,
            "avg_view_duration": 300,
            "subscriber_gained": 500,
            "like_rate": 0.5,
            "comment_rate": 0.2,
        }
        reward = shaper.compute_reward("youtube", metrics)
        assert 0.0 <= reward <= 1.0

    def test_negative_skip_rate_still_bounded(self):
        shaper = RewardShaper()
        # skip_rate has negative weight — even extreme values should bound
        metrics = {"views": 5000, "saves": 200, "dm_send_rate": 0.1, "skip_rate": 0.9}
        reward = shaper.compute_reward("instagram", metrics)
        assert 0.0 <= reward <= 1.0

    @pytest.mark.parametrize("platform", ["youtube", "instagram", "tiktok", "facebook", "twitter"])
    def test_moderate_metrics_in_range(self, platform):
        shaper = RewardShaper()
        # Moderate "okay" values
        metrics = {"views": 3000, "shares": 20, "saves": 50, "impressions": 3000}
        reward = shaper.compute_reward(platform, metrics)
        assert 0.0 <= reward <= 1.0


# ---------------------------------------------------------------------------
# 6. No boost below 80% threshold: subscriber_count = 700 -> no weight change
# ---------------------------------------------------------------------------


class TestNoBoostBelowThreshold:
    def test_no_boost_at_70_percent(self):
        shaper = RewardShaper()
        channel = {"subscriber_count": 700.0}  # 70% of 1000 — below 80%
        weights = shaper.get_adjusted_weights("youtube", channel)

        base_normalised = _normalise_weights(dict(BASE_WEIGHTS["youtube"]))
        # Weights should be unchanged (within floating point)
        for key in base_normalised:
            assert abs(weights[key] - base_normalised[key]) < 1e-9


# ---------------------------------------------------------------------------
# 7. Twitter reply_chain_rate has highest base weight
# ---------------------------------------------------------------------------


class TestTwitterBaseWeights:
    def test_reply_chain_rate_is_highest(self):
        tw = BASE_WEIGHTS["twitter"]
        assert tw["reply_chain_rate"] == max(tw.values())


# ---------------------------------------------------------------------------
# 8. X/Twitter boost: impressions_3mo near 5M -> reply_chain_rate boosted
# ---------------------------------------------------------------------------


class TestTwitterBoost:
    def test_boost_when_near_impression_threshold(self):
        shaper = RewardShaper()
        channel = {"impressions_3mo": 4_200_000.0}  # 84% of 5M
        weights = shaper.get_adjusted_weights("twitter", channel)

        base_normalised = _normalise_weights(dict(BASE_WEIGHTS["twitter"]))
        assert weights["reply_chain_rate"] > base_normalised["reply_chain_rate"]


# ---------------------------------------------------------------------------
# 9. _normalise_metric: views at target returns 1.0
# ---------------------------------------------------------------------------


class TestNormaliseMetric:
    def test_at_target_returns_one(self):
        assert _normalise_metric("views", 10000, "youtube") == 1.0

    def test_above_target_clamped_to_one(self):
        assert _normalise_metric("views", 50000, "youtube") == 1.0

    def test_zero_returns_zero(self):
        assert _normalise_metric("views", 0, "youtube") == 0.0

    def test_half_target_returns_half(self):
        assert abs(_normalise_metric("views", 5000, "youtube") - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# 10. RewardShaper with channel_metrics_fn callback
# ---------------------------------------------------------------------------


class TestChannelMetricsFn:
    def test_callback_used_when_no_explicit_channel_metrics(self):
        called_with = {}

        def mock_fn(platform: str) -> dict:
            called_with["platform"] = platform
            return {"subscriber_count": 900.0}  # 90% — triggers boost

        shaper = RewardShaper(channel_metrics_fn=mock_fn)
        weights = shaper.get_adjusted_weights("youtube")
        assert called_with["platform"] == "youtube"
        # Should have boosted subscriber_gained
        base_normalised = _normalise_weights(dict(BASE_WEIGHTS["youtube"]))
        assert weights["subscriber_gained"] > base_normalised["subscriber_gained"]
