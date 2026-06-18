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
    _is_fixture_preview,
    score_visual_potential,
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


# ---------------------------------------------------------------------------
# Engagement-aware ranking + absolute view-count floor (selection quality)
# ---------------------------------------------------------------------------


class TestEngagement:
    def test_no_engagement_data_is_neutral(self):
        """Backward-compat: with no view_count/like_count, engagement is 1.0 and
        the composite matches the old velocity×trend×relevance formula."""
        scorer = CompositeScorer("gaming")
        vs = scorer.score(_video(view_velocity=1500.0), trend_multiplier=1.0)
        assert vs.engagement_score == 1.0
        assert vs.composite == pytest.approx(1.0)

    def test_high_engagement_outranks_low_at_equal_velocity(self):
        """A clip people actually liked beats a raw view-spike with few likes."""
        scorer = CompositeScorer("gaming", min_composite=0.0)
        engaged = _video(
            "engaged", view_velocity=1500.0, view_count=100_000, like_count=4_000
        )  # 4%
        flat = _video("flat", view_velocity=1500.0, view_count=100_000, like_count=300)  # 0.3%
        results = scorer.score_and_rank([flat, engaged])
        assert results[0].video_id == "engaged"
        assert results[0].composite > results[1].composite

    def test_zero_likes_halves_but_does_not_kill(self):
        """Engagement floor (0.5): a zero-like clip keeps half its composite."""
        scorer = CompositeScorer("gaming")
        vs = scorer.score(_video(view_velocity=1500.0, view_count=100_000, like_count=0))
        assert vs.engagement_score == pytest.approx(0.5)
        assert vs.composite == pytest.approx(0.5)  # 1.0 × 0.5
        assert vs.passed is True  # 0.5 ≥ 0.35

    def test_engagement_saturates_at_target_ratio(self):
        scorer = CompositeScorer("gaming")
        # 3% like ratio = target → full engagement; 6% adds nothing more.
        at_target = scorer.score(_video(view_velocity=1500.0, view_count=100_000, like_count=3_000))
        above = scorer.score(_video(view_velocity=1500.0, view_count=100_000, like_count=6_000))
        assert at_target.engagement_score == pytest.approx(1.0)
        assert above.engagement_score == pytest.approx(1.0)


class TestViewCountFloor:
    def test_known_low_view_count_rejected(self):
        """A recency-gamed clip (high velocity, trivial reach) is floored out."""
        scorer = CompositeScorer("gaming")  # min_view_count = 5000
        vs = scorer.score(_video(view_velocity=5000.0, view_count=200, like_count=10))
        assert vs.composite > scorer.min_composite  # would pass on score alone
        assert vs.passed is False  # but floored on absolute reach

    def test_view_count_above_floor_passes(self):
        scorer = CompositeScorer("gaming")
        vs = scorer.score(_video(view_velocity=1500.0, view_count=50_000, like_count=1_500))
        assert vs.passed is True

    def test_zero_view_count_is_unknown_not_floored(self):
        """view_count=0/absent = unknown → not floored, falls back to velocity."""
        scorer = CompositeScorer("gaming")
        vs = scorer.score(_video(view_velocity=1500.0, view_count=0))
        assert vs.passed is True

    def test_floor_is_per_niche(self):
        assert CompositeScorer("gaming").min_view_count == 5000
        assert CompositeScorer("anime").min_view_count == 2000
        assert CompositeScorer("gaming", min_view_count=99).min_view_count == 99


# ── 2026-06-18: Sports fixture-preview detection ──────────────


class TestFixturePreviewDetection:
    """Sports fixture previews ("Atletico Madrid - Athletic Bilbao")
    are RSS-source text articles for games that HAVEN'T happened yet.
    They have no video clip and they died at DownloadTopVideos for
    two consecutive days producing zero sports blueprints. Pin the
    fixture-detection so the visual gate (min_visual_potential=0.3)
    zero-rejects them before they displace real video stories in the
    top-N cut.
    """

    @staticmethod
    def _s(title: str) -> dict:
        return {"title": title, "description": ""}

    def test_dash_separated_teams_is_fixture(self):
        """The 2026-06-18 prod payload — exact strings that killed
        ClutchWire today."""
        assert _is_fixture_preview("Atletico Madrid - Athletic Bilbao", "")
        assert _is_fixture_preview("Koln - Bayer Leverkusen", "")

    def test_vs_separated_teams_is_fixture(self):
        assert _is_fixture_preview("Lakers vs Celtics", "")
        assert _is_fixture_preview("Lakers v Celtics", "")

    def test_unicode_dash_handled(self):
        """Some RSS feeds use en-dash (–) instead of hyphen (-)."""
        assert _is_fixture_preview("Real Madrid – Barcelona", "")

    def test_played_game_with_scores_not_fixture(self):
        """Games with score-shaped numerals are NOT previews — let
        them through to normal scoring."""
        assert not _is_fixture_preview("Lakers 102, Celtics 98", "")
        assert not _is_fixture_preview("Real Madrid 2-1 Barcelona", "")
        assert not _is_fixture_preview("Bayern 3:0 Dortmund", "")

    def test_final_marker_not_fixture(self):
        """``Final`` anywhere in the text means game ended."""
        assert not _is_fixture_preview(
            "Lakers vs Celtics: Final",
            "lakers vs celtics: final",
        )

    def test_live_marker_not_fixture(self):
        assert not _is_fixture_preview(
            "Lakers vs Celtics — LIVE updates",
            "lakers vs celtics — live updates",
        )

    def test_highlights_marker_not_fixture(self):
        """Highlights = the game happened, this IS what we want."""
        assert not _is_fixture_preview(
            "Lakers vs Celtics highlights",
            "lakers vs celtics highlights",
        )

    def test_non_matchup_title_not_fixture(self):
        """Round-ups, single-team news, etc. don't match the
        Team-vs-Team shape."""
        assert not _is_fixture_preview("Premier League round-up", "")
        assert not _is_fixture_preview(
            "Atletico Madrid extend win streak to 8",
            "atletico madrid extend win streak to 8",
        )
        assert not _is_fixture_preview("LeBron's top plays 2026", "")

    def test_long_title_rejected(self):
        """80-char cap keeps regex bounded — long titles can't be
        fixture previews."""
        long_title = "Atletico Madrid - Athletic Bilbao " * 5  # ~170 chars
        assert not _is_fixture_preview(long_title, "")

    def test_visual_potential_zero_for_sports_fixture(self):
        """End-to-end: a sports fixture preview gets visual_potential
        0.0, NOT the default 0.4, so the 0.3 min-gate rejects it."""
        story = self._s("Atletico Madrid - Athletic Bilbao")
        assert score_visual_potential(story, "sports") == 0.0

    def test_visual_potential_unchanged_for_other_niches(self):
        """The fixture check is sports-only. A `Team - Team`
        title in anime or movies (e.g. anime crossover episode
        titles) shouldn't get the sports treatment."""
        # Unlikely shape but pin the guard
        story = self._s("Naruto - Sasuke")
        assert score_visual_potential(story, "anime") == 0.4  # default

    def test_visual_potential_unchanged_for_sports_with_video_signal(self):
        """A sports story with strong visual signal (e.g. 'highlights')
        still gets 0.7+, not 0.0 — fixture check fires only when no
        other signal explains the visual-quality score."""
        story = self._s("Lakers vs Celtics highlights — Final")
        # Has both 'highlights' (strong signal) and 'Final' (result marker)
        # — should NOT trigger fixture check, should score 0.7+
        assert score_visual_potential(story, "sports") >= 0.7


class TestVideoBearingSkipsFixtureCheck:
    """ScoreBat highlights videos use the matchup as the title
    ("Angers - PSG" IS the video title, not a preview article).
    FetchScoreBat / FetchTrendingVideos populate video_id /
    download_url / video_url. When ANY of those is set, the fixture-
    check must be SKIPPED — the title is a video title, not a
    preview-article title.

    Regression: PR #320's first deploy dropped 12 YouTube trending
    videos + 10 ScoreBat highlights in a single sports run because
    their matchup titles matched _is_fixture_preview without this
    guard."""

    @staticmethod
    def _s(title: str, **extra) -> dict:
        return {"title": title, "description": "", **extra}

    def test_scorebat_highlight_with_video_url_passes(self):
        """ScoreBat returns `video_url` — fixture check must skip."""
        story = self._s("Angers - PSG", video_url="https://www.scorebat.com/...")
        assert score_visual_potential(story, "sports") == 0.4  # default

    def test_youtube_trending_with_video_id_passes(self):
        """FetchTrendingVideos returns `video_id` — same skip."""
        story = self._s("Manchester City - Southampton", video_id="abc123")
        assert score_visual_potential(story, "sports") == 0.4

    def test_youtube_trending_with_download_url_passes(self):
        """Both `video_id` and `download_url` populated — same skip."""
        story = self._s(
            "Real Madrid – Barcelona",
            video_id="xyz789",
            download_url="https://www.youtube.com/watch?v=xyz789",
        )
        assert score_visual_potential(story, "sports") == 0.4

    def test_text_only_fixture_still_rejected(self):
        """The original PR #320 contract is preserved: text-only
        fixture previews (no video field) still zero-reject."""
        story = self._s("Atletico Madrid - Athletic Bilbao")
        assert score_visual_potential(story, "sports") == 0.0

    def test_empty_video_field_still_rejects(self):
        """Empty-string video fields don't bypass the check —
        only populated values do."""
        story = self._s("Lakers vs Celtics", video_id="", video_url="")
        assert score_visual_potential(story, "sports") == 0.0


class TestSourceFieldVideoDetection:
    """ScoreBat stories don't have a video_url/video_id in the story
    dict (the embed lives elsewhere; VideoGate matches it later).
    Detect them by `source` / `video_source` field instead.

    Regression: PR #323's first-deploy still rejected all 10 ScoreBat
    matchup-titled stories because they lacked video_id."""

    @staticmethod
    def _s(title: str, **extra) -> dict:
        return {"title": title, "description": "", **extra}

    def test_scorebat_source_marker_passes(self):
        """source='scorebat' → video-bearing even without video_id."""
        story = self._s("Angers - PSG", source="scorebat")
        assert score_visual_potential(story, "sports") == 0.4

    def test_video_source_field_also_works(self):
        """video_source field is an alternate marker."""
        story = self._s("Lakers vs Celtics", video_source="scorebat")
        assert score_visual_potential(story, "sports") == 0.4

    def test_youtube_channel_rss_source_passes(self):
        story = self._s("NBA - GSW vs LAL", source="youtube_channel_rss")
        assert score_visual_potential(story, "sports") == 0.4

    def test_content_pool_source_passes(self):
        story = self._s("Real Madrid - Barcelona", source="content_pool")
        assert score_visual_potential(story, "sports") == 0.4

    def test_unknown_source_still_rejects_fixture(self):
        """Unknown source name + no video_id → fixture check still
        applies. Pins that we don't over-generously bypass for any
        random `source` value."""
        story = self._s("Atletico Madrid - Athletic Bilbao", source="rss_espn")
        assert score_visual_potential(story, "sports") == 0.0

    def test_source_case_insensitive(self):
        """Match should be case-insensitive — some fetchers capitalize."""
        story = self._s("Angers - PSG", source="ScoreBat")
        assert score_visual_potential(story, "sports") == 0.4

    def test_empty_source_falls_through_to_fixture_check(self):
        story = self._s("Real Madrid - Barcelona", source="")
        assert score_visual_potential(story, "sports") == 0.0
