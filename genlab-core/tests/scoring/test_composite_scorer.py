"""Tests for genlab_core.scoring.composite_scorer.

Covers:
  - VideoScore dataclass and to_dict()
  - CompositeScorer.score() formula correctness
  - CompositeScorer.score_and_rank() filtering and sorting
  - Per-niche threshold defaults
  - Edge cases: zero velocity, zero relevance, custom thresholds
  - Trend multiplier clamping
"""

import pytest
from genlab_core.scoring.composite_scorer import (
    CompositeScorer,
    VideoScore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _video(
    video_id: str = "abc123", title: str = "Test Video", view_velocity: float = 1000.0, **extra
) -> dict:
    """Build a minimal video dict."""
    d = {"video_id": video_id, "title": title, "view_velocity": view_velocity}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# VideoScore
# ---------------------------------------------------------------------------


class TestVideoScore:
    def test_to_dict_roundtrip(self):
        vs = VideoScore(
            video_id="v1",
            title="Hello",
            view_velocity=500.0,
            velocity_score=0.333,
            trend_multiplier=2.0,
            niche_relevance=1.0,
            composite=0.666,
            passed=True,
        )
        d = vs.to_dict()
        assert d["video_id"] == "v1"
        assert d["composite"] == 0.666
        assert d["passed"] is True
        assert isinstance(d["velocity_score"], float)

    def test_to_dict_rounds_values(self):
        vs = VideoScore(
            video_id="v2",
            title="X",
            view_velocity=123.456789,
            velocity_score=0.12345678,
            trend_multiplier=1.5555,
            niche_relevance=1.0,
            composite=0.123456789,
            passed=False,
        )
        d = vs.to_dict()
        assert d["view_velocity"] == 123.5
        assert d["velocity_score"] == 0.123
        assert d["trend_multiplier"] == 1.56
        assert d["composite"] == 0.1235


# ---------------------------------------------------------------------------
# CompositeScorer.score()
# ---------------------------------------------------------------------------


class TestScore:
    def test_basic_formula(self):
        """composite = velocity_score × trend_multiplier × niche_relevance."""
        scorer = CompositeScorer("gaming")  # threshold = 1500
        vs = scorer.score(_video(view_velocity=750.0), trend_multiplier=2.0)
        # velocity_score = 750/1500 = 0.5
        # composite = 0.5 × 2.0 × 1.0 = 1.0
        assert vs.velocity_score == pytest.approx(0.5)
        assert vs.composite == pytest.approx(1.0)

    def test_velocity_capped_at_1(self):
        """View velocity above threshold still caps velocity_score at 1.0."""
        scorer = CompositeScorer("ai_creators")  # threshold = 400
        vs = scorer.score(_video(view_velocity=800.0))
        assert vs.velocity_score == 1.0
        assert vs.composite == pytest.approx(1.0)

    def test_zero_velocity_gives_zero(self):
        scorer = CompositeScorer("gaming")
        vs = scorer.score(_video(view_velocity=0))
        assert vs.velocity_score == 0.0
        assert vs.composite == 0.0
        assert vs.passed is False

    def test_zero_relevance_gives_zero(self):
        scorer = CompositeScorer("gaming")
        vs = scorer.score(_video(view_velocity=3000.0), niche_relevance=0.0)
        assert vs.composite == 0.0
        assert vs.passed is False

    def test_trend_multiplier_amplifies(self):
        scorer = CompositeScorer("movies")  # threshold = 800
        vs_base = scorer.score(_video(view_velocity=400.0), trend_multiplier=1.0)
        vs_trend = scorer.score(_video(view_velocity=400.0), trend_multiplier=3.0)
        assert vs_trend.composite == pytest.approx(vs_base.composite * 3.0)

    def test_trend_multiplier_clamped_to_3(self):
        scorer = CompositeScorer("gaming")
        vs = scorer.score(_video(view_velocity=1500.0), trend_multiplier=5.0)
        assert vs.trend_multiplier == 3.0

    def test_negative_trend_multiplier_clamped_to_0(self):
        scorer = CompositeScorer("gaming")
        vs = scorer.score(_video(view_velocity=1500.0), trend_multiplier=-1.0)
        assert vs.trend_multiplier == 0.0
        assert vs.composite == 0.0

    def test_passed_flag_uses_min_composite(self):
        scorer = CompositeScorer("gaming", min_composite=0.5)
        vs_pass = scorer.score(_video(view_velocity=1500.0))  # 1.0 >= 0.5
        vs_fail = scorer.score(_video(view_velocity=300.0))  # 0.2 < 0.5
        assert vs_pass.passed is True
        assert vs_fail.passed is False


# ---------------------------------------------------------------------------
# Per-niche defaults
# ---------------------------------------------------------------------------


class TestNicheDefaults:
    @pytest.mark.parametrize(
        "niche,expected_vel,expected_min",
        [
            ("gaming", 1500.0, 0.35),
            ("sports", 2000.0, 0.35),
            ("movies", 800.0, 0.30),
            ("anime", 600.0, 0.30),
            ("ai_creators", 400.0, 0.25),
        ],
    )
    def test_default_thresholds(self, niche, expected_vel, expected_min):
        scorer = CompositeScorer(niche)
        assert scorer.velocity_threshold == expected_vel
        assert scorer.min_composite == expected_min

    def test_unknown_niche_gets_fallback(self):
        scorer = CompositeScorer("unknown_niche")
        assert scorer.velocity_threshold == 500.0  # fallback
        assert scorer.min_composite == 0.30  # fallback

    def test_custom_overrides(self):
        scorer = CompositeScorer("gaming", velocity_threshold=999.0, min_composite=0.1)
        assert scorer.velocity_threshold == 999.0
        assert scorer.min_composite == 0.1


# ---------------------------------------------------------------------------
# CompositeScorer.score_and_rank()
# ---------------------------------------------------------------------------


class TestScoreAndRank:
    def test_filters_below_threshold(self):
        scorer = CompositeScorer("gaming", min_composite=0.5)
        videos = [
            _video("v1", view_velocity=1500.0),  # 1.0 → pass
            _video("v2", view_velocity=300.0),  # 0.2 → fail
            _video("v3", view_velocity=900.0),  # 0.6 → pass
        ]
        results = scorer.score_and_rank(videos)
        ids = [r.video_id for r in results]
        assert "v1" in ids
        assert "v3" in ids
        assert "v2" not in ids

    def test_sorted_descending(self):
        scorer = CompositeScorer("gaming", min_composite=0.0)
        videos = [
            _video("low", view_velocity=100.0),
            _video("high", view_velocity=1500.0),
            _video("mid", view_velocity=750.0),
        ]
        results = scorer.score_and_rank(videos)
        composites = [r.composite for r in results]
        assert composites == sorted(composites, reverse=True)
        assert results[0].video_id == "high"

    def test_empty_input(self):
        scorer = CompositeScorer("gaming")
        results = scorer.score_and_rank([])
        assert results == []

    def test_all_filtered_returns_empty(self):
        scorer = CompositeScorer("gaming", min_composite=0.99)
        videos = [_video("v1", view_velocity=100.0)]  # 0.067 < 0.99
        results = scorer.score_and_rank(videos)
        assert results == []

    def test_trend_multipliers_map(self):
        scorer = CompositeScorer("ai_creators", min_composite=0.0)
        videos = [
            _video("v1", view_velocity=200.0),  # vel_score = 0.5
            _video("v2", view_velocity=200.0),  # vel_score = 0.5
        ]
        trend_map = {"v1": 3.0, "v2": 1.0}
        results = scorer.score_and_rank(videos, trend_multipliers=trend_map)
        # v1: 0.5 × 3.0 = 1.5, v2: 0.5 × 1.0 = 0.5
        assert results[0].video_id == "v1"
        assert results[0].composite == pytest.approx(1.5)
        assert results[1].composite == pytest.approx(0.5)

    def test_niche_relevance_map(self):
        scorer = CompositeScorer("gaming", min_composite=0.01)
        videos = [
            _video("relevant", view_velocity=1500.0),
            _video("irrelevant", view_velocity=1500.0),
        ]
        rel_map = {"relevant": 1.0, "irrelevant": 0.0}
        results = scorer.score_and_rank(videos, niche_relevances=rel_map)
        assert len(results) == 1
        assert results[0].video_id == "relevant"

    def test_best_video_wins(self):
        """Simulates a real scenario: 5 gaming videos, only the best survives."""
        scorer = CompositeScorer("gaming")  # threshold=1500, min=0.35
        videos = [
            _video("viral", view_velocity=5000.0),  # 1.0 → 1.0
            _video("decent", view_velocity=800.0),  # 0.533 → pass
            _video("mediocre", view_velocity=400.0),  # 0.267 → fail
            _video("weak", view_velocity=100.0),  # 0.067 → fail
            _video("dead", view_velocity=10.0),  # 0.007 → fail
        ]
        results = scorer.score_and_rank(videos)
        assert len(results) == 2  # viral + decent pass
        assert results[0].video_id == "viral"
        assert results[1].video_id == "decent"
