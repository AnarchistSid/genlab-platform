"""Pin the 2026-07-22 Facebook metric-list fix.

History: `platforms/metrics/facebook.py:_REELS_INSIGHTS_METRICS` included
`post_impressions_unique`. Meta v22 API rejects it on `/video_insights`
with HTTP 400 code 100 "The value must be a valid insights metric" — and
because Meta rejects the ENTIRE batch when any one metric is invalid, the
whole insights call was returning 400 → `fetch_facebook` returned None
→ `_fetch_platform_insights` treated as SKIPPED → publishing_analytics
rows stayed at SUCCESS forever for every FB row across all 5 niches.

Verified via per-metric probe against
`facebook:1726457395148308` (ai_creators, 2026-07-22 12:05 IST fire):

    fb_reels_total_plays              -> 200
    blue_reels_play_count             -> 200
    post_impressions_unique           -> 400 (#100) invalid metric
    post_video_likes_by_reaction_type -> 200
    post_video_social_actions         -> 200
    post_video_view_time              -> 200
    post_video_avg_time_watched       -> 200

Prod state before fix: 0/8 FB SUCCESS rows in 7-day window had
transitioned to INSIGHTS_48H. Same "learning loop denied signal"
class-of-bug as the Threads dispatch gap fixed in `f9f186c2` — but
different mechanism (Meta metric deprecation vs missing dispatch
elif branch).

These pins lock the metric list so a future edit re-adding
`post_impressions_unique` (or another Meta-rejected metric) fails at
test-time rather than in prod at 0% success for weeks.
"""

from __future__ import annotations

from genlab_core.platforms.metrics.facebook import _REELS_INSIGHTS_METRICS


class TestReelsInsightsMetricList:
    def test_does_not_include_post_impressions_unique(self) -> None:
        """`post_impressions_unique` was removed 2026-07-22. Re-adding it
        would poison the entire batch — Meta v22 rejects it and the whole
        insights call returns 400."""
        assert "post_impressions_unique" not in _REELS_INSIGHTS_METRICS, (
            "post_impressions_unique is Meta-invalid on /video_insights for Reels "
            "as of v22 API. Including it makes the ENTIRE metric batch fail with "
            "400 code 100. Verified via per-metric probe 2026-07-22."
        )

    def test_still_includes_fb_reels_total_plays(self) -> None:
        """The v23+ replacement for `post_video_views`. This is the primary
        views metric for Reels."""
        assert "fb_reels_total_plays" in _REELS_INSIGHTS_METRICS

    def test_still_includes_blue_reels_play_count(self) -> None:
        """Fallback initial-plays metric when total_plays is empty."""
        assert "blue_reels_play_count" in _REELS_INSIGHTS_METRICS

    def test_includes_all_six_verified_ok_metrics(self) -> None:
        """The 6 metrics that returned 200 on the 2026-07-22 probe must all
        remain — dropping any one silently degrades reward-signal
        completeness for FB across all 5 niches."""
        for metric in (
            "fb_reels_total_plays",
            "blue_reels_play_count",
            "post_video_likes_by_reaction_type",
            "post_video_social_actions",
            "post_video_view_time",
            "post_video_avg_time_watched",
        ):
            assert metric in _REELS_INSIGHTS_METRICS, (
                f"{metric!r} was verified-OK by 2026-07-22 probe against Meta v22 API. "
                f"Removing it drops real engagement signal for FB — regression."
            )

    def test_metric_list_is_comma_separated_string_not_list(self) -> None:
        """`_REELS_INSIGHTS_METRICS` is passed verbatim as the `metric=`
        query param — must be a comma-separated string, not a list."""
        assert isinstance(_REELS_INSIGHTS_METRICS, str)
        assert "," in _REELS_INSIGHTS_METRICS
