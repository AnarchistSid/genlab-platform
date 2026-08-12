"""2026-08-12: pin the view-through-rate (VTR) derived signal for
Instagram + Facebook reward.

VTR = views / reach. Measures "of the people the algorithm showed the
post to, how many watched?" Orthogonal to raw reach — a post with 100
views on 1000 reach (10% VTR) and 100 views on 100 reach (100% VTR)
score differently now (previously identical, because raw views was the
only reward signal for algorithm-side behavior).

Motivating investigation (memo:
composite-score-calibration-2026-08-12): reward magnitude too low to
differentiate arms meaningfully. Adding VTR expected to 5-10× signal
information density on Meta platforms.
"""

from __future__ import annotations


class TestInstagramVTRDerivation:
    def test_vtr_computed_when_views_and_reach_present(self):
        """The fetcher should populate `vtr` in metrics when both
        signals are present. Directly test the derived-signal logic
        (not the API call itself)."""
        from unittest.mock import MagicMock, patch

        from genlab_core.learning.metrics.instagram import _fetch_instagram

        # Mock the Meta API to return views + reach
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"name": "views", "values": [{"value": 100}]},
                {"name": "reach", "values": [{"value": 1000}]},
                {"name": "saved", "values": [{"value": 5}]},
                {"name": "likes", "values": [{"value": 3}]},
                {"name": "comments", "values": [{"value": 1}]},
                {"name": "shares", "values": [{"value": 2}]},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with (
            patch(
                "genlab_core.publishing.niche_credentials.resolve_meta_credentials",
                return_value={"ig_access_token": "test_token"},
            ),
            patch(
                "genlab_core.learning.metrics.instagram._META_SESSION.get",
                return_value=mock_resp,
            ),
        ):
            metrics = _fetch_instagram("ig_test_post", niche_id="anime")

        assert "vtr" in metrics, f"vtr missing from metrics: {metrics}"
        # 100 views / 1000 reach = 0.1
        assert metrics["vtr"] == 0.1

    def test_vtr_clamped_at_1_when_views_gt_reach(self):
        """Rare case: views > reach (multiple plays per unique user).
        VTR clamps at 1.0 rather than exceeding it."""
        from unittest.mock import MagicMock, patch

        from genlab_core.learning.metrics.instagram import _fetch_instagram

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"name": "views", "values": [{"value": 500}]},
                {"name": "reach", "values": [{"value": 300}]},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with (
            patch(
                "genlab_core.publishing.niche_credentials.resolve_meta_credentials",
                return_value={"ig_access_token": "test_token"},
            ),
            patch(
                "genlab_core.learning.metrics.instagram._META_SESSION.get",
                return_value=mock_resp,
            ),
        ):
            metrics = _fetch_instagram("ig_test", niche_id="anime")

        assert metrics["vtr"] == 1.0

    def test_vtr_omitted_when_reach_zero(self):
        """Zero reach means no data — vtr should NOT be populated
        (reward-shaper will handle absent metric via redistribution)."""
        from unittest.mock import MagicMock, patch

        from genlab_core.learning.metrics.instagram import _fetch_instagram

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"name": "views", "values": [{"value": 5}]},
                {"name": "reach", "values": [{"value": 0}]},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with (
            patch(
                "genlab_core.publishing.niche_credentials.resolve_meta_credentials",
                return_value={"ig_access_token": "test_token"},
            ),
            patch(
                "genlab_core.learning.metrics.instagram._META_SESSION.get",
                return_value=mock_resp,
            ),
        ):
            metrics = _fetch_instagram("ig_test", niche_id="anime")

        assert "vtr" not in metrics


class TestBaseWeightsIncludeVTR:
    def test_instagram_has_vtr_weight(self):
        """Regression pin: instagram BASE_WEIGHTS must include vtr.
        Without the weight, fetcher populates the metric but reward
        computation ignores it."""
        from genlab_core.learning.reward_shaper import BASE_WEIGHTS

        assert "vtr" in BASE_WEIGHTS["instagram"]
        # Sanity: not zero, not > 1.0
        assert 0.0 < BASE_WEIGHTS["instagram"]["vtr"] <= 1.0

    def test_facebook_has_vtr_weight(self):
        from genlab_core.learning.reward_shaper import BASE_WEIGHTS

        assert "vtr" in BASE_WEIGHTS["facebook"]
        assert 0.0 < BASE_WEIGHTS["facebook"]["vtr"] <= 1.0

    def test_vtr_weight_conservative(self):
        """Both should be <= 0.15 — VTR is a new signal with unknown
        variance; small weight nudges without dominating existing
        signals until we have data to justify a larger allocation."""
        from genlab_core.learning.reward_shaper import BASE_WEIGHTS

        assert BASE_WEIGHTS["instagram"]["vtr"] <= 0.15
        assert BASE_WEIGHTS["facebook"]["vtr"] <= 0.15


class TestRewardShaperConsumesVTR:
    def test_vtr_in_metrics_contributes_to_reward(self):
        """End-to-end: reward_shaper.compute_reward should incorporate
        vtr into the reward calc when the metric is present."""
        from genlab_core.learning.reward_shaper import RewardShaper

        shaper = RewardShaper(niche_id="anime")

        # Post with only reach + views: reward comes from vtr + views
        base_metrics = {"views": 50, "reach": 500}
        r_base = shaper.compute_reward("instagram", base_metrics)

        # Post WITH vtr populated (higher signal): should score higher
        # (or at least differently) than base
        with_vtr = {**base_metrics, "vtr": 0.5}
        r_with_vtr = shaper.compute_reward("instagram", with_vtr)

        # If vtr weight fires, adding it to metrics changes reward
        # (either up or down depending on redistribution — the point
        # is the two must NOT be identical).
        assert r_base != r_with_vtr, (
            "vtr must contribute to reward. If these are equal, either "
            "the weight isn't in BASE_WEIGHTS or the fetcher isn't "
            "populating the metric."
        )
