"""2026-08-12: pin the per-source reach multiplier in composite_scorer.

Motivating investigation: composite_score has Pearson r=-0.44 (log
r=-0.75) against anime/facebook reach over 30d. Root cause: the
formula measures SOURCE-platform virality (YouTube view velocity)
but doesn't predict DESTINATION-platform reach.

Observed anime data (n=5-7 per cell, 30d):
  anilist × facebook: avg 695 views (top of any cell across niches)
  youtube_trending × facebook: avg 4 views (bottom quartile)

Fix: _SOURCE_REACH_MULTIPLIER applies a per-(niche, source) nudge to
composite. Currently populated only for anime where signal is
unambiguous; all other cells default to 1.0 (no effect).
"""

from __future__ import annotations

from genlab_core.scoring.composite_scorer import (
    _SOURCE_REACH_MULTIPLIER,
    CompositeScorer,
    _source_reach_multiplier,
)


class TestSourceReachMultiplierLookup:
    def test_unpopulated_cell_returns_1(self):
        """Every unpopulated cell must return 1.0 (no effect).
        Defensive against uncalibrated niches getting bogus multipliers."""
        assert _source_reach_multiplier("gaming", "twitch_trending") == 1.0
        assert _source_reach_multiplier("sports", "reddit:boxing") == 1.0
        assert _source_reach_multiplier("movies", "reddit:horror") == 1.0
        assert _source_reach_multiplier("nonexistent_niche", "any_source") == 1.0
        assert _source_reach_multiplier("anime", "unknown_source") == 1.0

    def test_anime_anilist_boosted(self):
        """anilist × anime × any platform observed 695 avg views
        vs baseline ~280 on facebook. Boost captured as 1.5x."""
        assert _source_reach_multiplier("anime", "anilist") == 1.5

    def test_anime_youtube_trending_dampened(self):
        """youtube_trending × anime observed 4-80 avg views vs
        baseline 5-280. Non-zero (threads works OK) but the biggest
        source of anime content flooding low-value posts. 0.6x
        nudge, not a kill."""
        assert _source_reach_multiplier("anime", "youtube_trending") == 0.6

    def test_all_multipliers_within_safe_bounds(self):
        """Conservative bounds: no boost > 2.0, no penalty < 0.4.
        Small-sample estimates can be noisy; multiplier should
        NUDGE not OVERWRITE the scoring model."""
        for niche, sources in _SOURCE_REACH_MULTIPLIER.items():
            for source, mult in sources.items():
                assert 0.4 <= mult <= 2.0, (
                    f"{niche}.{source} multiplier {mult} outside safe "
                    "bounds [0.4, 2.0]. Small samples make wider bounds "
                    "risky — recalibrate against ≥30-post data before "
                    "widening."
                )


class TestCompositeScorerAppliesMultiplier:
    def test_anime_anilist_composite_boosted(self):
        """Same video, source=anilist vs source=other — anilist path
        must produce a composite 1.5x higher (anime.anilist multiplier)."""
        scorer = CompositeScorer("anime")
        base_video = {
            "video_id": "test",
            "title": "Test Anime Episode 42",
            "view_velocity": 1000.0,  # equal for both
        }

        s_anilist = scorer.score({**base_video, "source": "anilist"})
        s_other = scorer.score({**base_video, "source": "other_random_source"})

        # Ratio should be exactly 1.5 (no other differences)
        ratio = s_anilist.composite / max(s_other.composite, 1e-9)
        assert 1.4 <= ratio <= 1.6, (
            f"anime.anilist should boost composite by 1.5x, got ratio={ratio}"
        )

    def test_anime_youtube_trending_composite_dampened(self):
        """youtube_trending anime scores at 0.6x of baseline."""
        scorer = CompositeScorer("anime")
        base_video = {
            "video_id": "test",
            "title": "Test",
            "view_velocity": 1000.0,
        }

        s_yt = scorer.score({**base_video, "source": "youtube_trending"})
        s_other = scorer.score({**base_video, "source": "other_random_source"})

        ratio = s_yt.composite / max(s_other.composite, 1e-9)
        assert 0.55 <= ratio <= 0.65, (
            f"anime.youtube_trending should be 0.6x, got ratio={ratio}"
        )

    def test_gaming_unaffected_by_calibration(self):
        """Only anime has multipliers populated. gaming source values
        should have no effect on composite."""
        scorer = CompositeScorer("gaming")
        base_video = {
            "video_id": "test",
            "title": "Test",
            "view_velocity": 1000.0,
        }
        s1 = scorer.score({**base_video, "source": "twitch_trending"})
        s2 = scorer.score({**base_video, "source": "youtube_trending"})
        s3 = scorer.score({**base_video, "source": "unknown"})

        assert s1.composite == s2.composite == s3.composite

    def test_missing_source_falls_back_to_1_multiplier(self):
        """Video without a source field must not crash — falls back
        to 1.0 multiplier (no effect)."""
        scorer = CompositeScorer("anime")
        video = {
            "video_id": "test",
            "title": "Test",
            "view_velocity": 1000.0,
            # no 'source' field
        }
        # Must not raise
        result = scorer.score(video)
        # Multiplier defaults to 1.0 -> composite unchanged from formula
        assert result.composite > 0
