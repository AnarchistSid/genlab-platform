"""Pin the retention derivation helper (PR 6).

Covers:
  1. Missing signals → None (not crash, not fake zero)
  2. Instagram avg_watch_time (ms) converted to seconds
  3. YouTube avg_view_duration (already seconds) preserved
  4. Facebook minutes_viewed / plays gives per-play seconds
  5. hold_3s / hold_15s monotonic in avg_watch_time
  6. Values clamped to [0, 1]
  7. sends_per_reach proxy math from total_interactions
  8. completion falls back to hold_15s when video_duration_s absent
  9. saved_rate + engagement_quality derivations
 10. Zero reach doesn't divide-by-zero
"""

from __future__ import annotations

import pytest

from genlab_core.learning.retention_derivations import derive_retention_metrics


class TestBasicIG:
    def test_avg_watch_3s_instagram_gives_hold_3s_1(self) -> None:
        d = derive_retention_metrics(
            {"reach": 100, "avg_watch_time": 3000}, "instagram"
        )
        assert d["hold_3s"] == 1.0

    def test_avg_watch_15s_instagram_gives_both_holds_1(self) -> None:
        d = derive_retention_metrics(
            {"reach": 100, "avg_watch_time": 15000}, "instagram"
        )
        assert d["hold_3s"] == 1.0
        assert d["hold_15s"] == 1.0

    def test_avg_watch_1_5s_instagram_hold_3s_0_5(self) -> None:
        d = derive_retention_metrics(
            {"reach": 100, "avg_watch_time": 1500}, "instagram"
        )
        assert d["hold_3s"] == pytest.approx(0.5)
        assert d["hold_15s"] == pytest.approx(0.1)

    def test_zero_watch_time_gives_zero(self) -> None:
        d = derive_retention_metrics(
            {"reach": 100, "avg_watch_time": 0}, "instagram"
        )
        assert d["hold_3s"] == 0.0
        assert d["hold_15s"] == 0.0


class TestPlatformUnits:
    def test_youtube_seconds_no_conversion(self) -> None:
        """YouTube already reports avg_view_duration in seconds."""
        d = derive_retention_metrics(
            {"reach": 100, "avg_view_duration": 3.0}, "youtube"
        )
        assert d["hold_3s"] == 1.0

    def test_facebook_minutes_to_seconds(self) -> None:
        """Facebook minutes_viewed / plays = avg minutes → * 60 sec."""
        d = derive_retention_metrics(
            {"reach": 100, "plays": 20, "minutes_viewed": 1.0},
            "facebook",
        )
        # 60 total seconds / 20 plays = 3s avg
        assert d["hold_3s"] == 1.0

    def test_facebook_zero_plays_gives_none(self) -> None:
        """No plays → can't average, return None gracefully."""
        d = derive_retention_metrics(
            {"reach": 100, "plays": 0, "minutes_viewed": 5}, "facebook"
        )
        assert d["hold_3s"] is None


class TestSendsPerReach:
    def test_sends_per_reach_residual_math(self) -> None:
        """sends_per_reach = (total_interactions - likes - comments - saves) / reach"""
        d = derive_retention_metrics(
            {
                "reach": 100,
                "likes": 3,
                "comments": 1,
                "saves": 1,
                "total_interactions": 10,
                # residual = 10 - 3 - 1 - 1 = 5 → 5/100 = 0.05
            },
            "instagram",
        )
        assert d["sends_per_reach"] == pytest.approx(0.05)

    def test_sends_per_reach_zero_when_no_residual(self) -> None:
        """total = likes + comments + saved → 0 residual."""
        d = derive_retention_metrics(
            {
                "reach": 100,
                "likes": 5,
                "comments": 2,
                "saves": 1,
                "total_interactions": 8,
            },
            "instagram",
        )
        assert d["sends_per_reach"] == 0.0

    def test_sends_per_reach_none_when_missing(self) -> None:
        """No total_interactions field → None (fallback for legacy IG rows)."""
        d = derive_retention_metrics(
            {"reach": 100, "likes": 5}, "instagram"
        )
        assert d["sends_per_reach"] is None

    def test_sends_per_reach_zero_reach_none(self) -> None:
        """Zero reach → can't divide, return None not crash."""
        d = derive_retention_metrics(
            {"reach": 0, "total_interactions": 5}, "instagram"
        )
        assert d["sends_per_reach"] is None

    def test_sends_per_reach_clamped_to_1(self) -> None:
        """Weird data where residual > reach clamps at 1.0."""
        d = derive_retention_metrics(
            {"reach": 100, "total_interactions": 200},
            "instagram",
        )
        # residual = 200 → 200/100 = 2.0 → clamp 1.0
        assert d["sends_per_reach"] == 1.0

    def test_negative_residual_treated_as_zero(self) -> None:
        """If likes+comments+saves > total_interactions (edge case),
        residual clamps to 0 not negative."""
        d = derive_retention_metrics(
            {
                "reach": 100,
                "likes": 10,
                "comments": 5,
                "saves": 3,
                "total_interactions": 15,  # < 10+5+3=18
            },
            "instagram",
        )
        assert d["sends_per_reach"] == 0.0


class TestCompletionFallback:
    def test_completion_from_video_duration(self) -> None:
        """avg 15s watch of 30s video = 0.5 completion."""
        d = derive_retention_metrics(
            {"reach": 100, "avg_watch_time": 15000},
            "instagram",
            video_duration_s=30.0,
        )
        assert d["completion"] == pytest.approx(0.5)

    def test_completion_fallback_to_hold_15s(self) -> None:
        """Without video_duration_s, completion falls back to hold_15s."""
        d = derive_retention_metrics(
            {"reach": 100, "avg_watch_time": 15000}, "instagram"
        )
        assert d["completion"] == d["hold_15s"]

    def test_completion_none_when_no_watch_data(self) -> None:
        d = derive_retention_metrics(
            {"reach": 100}, "instagram"
        )
        assert d["completion"] is None


class TestSavedRate:
    def test_saved_rate_basic(self) -> None:
        d = derive_retention_metrics(
            {"reach": 100, "saves": 5}, "instagram"
        )
        assert d["saved_rate"] == pytest.approx(0.05)

    def test_saved_rate_none_when_missing_saves(self) -> None:
        d = derive_retention_metrics({"reach": 100}, "instagram")
        assert d["saved_rate"] is None


class TestEngagementQuality:
    def test_comments_weighted_2x(self) -> None:
        """(likes + 2*comments) / reach"""
        d = derive_retention_metrics(
            {"reach": 100, "likes": 6, "comments": 2}, "instagram"
        )
        # (6 + 2*2) / 100 = 10/100 = 0.10
        assert d["engagement_quality"] == pytest.approx(0.10)

    def test_engagement_quality_missing_engagement_none(self) -> None:
        d = derive_retention_metrics({"reach": 100}, "instagram")
        assert d["engagement_quality"] is None


class TestClampBehavior:
    def test_hold_clamped_at_1(self) -> None:
        """Watch time way above threshold → clamp at 1.0, not >1.0."""
        d = derive_retention_metrics(
            {"reach": 100, "avg_watch_time": 999999}, "instagram"
        )
        assert d["hold_3s"] == 1.0
        assert d["hold_15s"] == 1.0

    def test_saved_rate_clamped_at_1(self) -> None:
        """Weird case: saves > reach clamps at 1.0."""
        d = derive_retention_metrics(
            {"reach": 10, "saves": 20}, "instagram"
        )
        assert d["saved_rate"] == 1.0


class TestEmptyMetrics:
    def test_empty_dict_returns_all_none(self) -> None:
        d = derive_retention_metrics({}, "instagram")
        for key in [
            "hold_3s",
            "hold_15s",
            "hold_avg_3s_15s",
            "completion",
            "sends_per_reach",
            "saved_rate",
            "engagement_quality",
        ]:
            assert d[key] is None, f"expected None for {key}, got {d[key]}"

    def test_unknown_platform_returns_all_none(self) -> None:
        """Unknown platform can't extract watch time → hold_* None."""
        d = derive_retention_metrics(
            {"reach": 100, "likes": 5}, "unknown_platform"
        )
        assert d["hold_3s"] is None
        assert d["hold_15s"] is None
        # But engagement-side derivations still work.
        assert d["engagement_quality"] == pytest.approx(0.05)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
