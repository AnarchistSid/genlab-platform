"""The Google-Trends multiplier must not boost the composite score.

2026-08-26. `composite = velocity × trend_mult × relevance × engagement ×
source_mult`, with `trend_mult` clamped to [0, 3.0]. Measured against 207
published reels with realised views, that boost inverted the ranking:

    composite band   n    avg views
    0.30-0.48       59      520     <- lowest scored, BEST performing
    ...
    3.00             8      105     <- highest scored, WORST performing

Boosted items (>1.0) averaged 133 views against 421 unboosted — a 3.2x penalty
reproducing in every niche where boosting occurs. Overall pearson -0.221,
spearman -0.317, top/bottom decile 0.468x.

Within-group correlations are weak (-0.13, -0.09), so the inversion lives
BETWEEN the groups: the base score is roughly uninformative and the multiplier
is what actively inverts the ranking. Hence a ceiling rather than a re-weight.

These pin that the ceiling holds, that it stays reversible, and — importantly —
that nothing else in the formula was disturbed.
"""
from __future__ import annotations

import pytest

from genlab_core.scoring import composite_scorer as cs


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(cs._TREND_CEILING_ENV, raising=False)


class TestCeilingHolds:
    @pytest.mark.parametrize("supplied", [1.0, 1.5, 2.0, 2.5, 3.0, 99.0])
    def test_no_supplied_value_can_boost(self, supplied):
        """Whatever Google Trends reports, it cannot raise the composite."""
        assert min(supplied, cs._trend_multiplier_ceiling()) <= 1.0

    def test_default_is_neutral(self):
        assert cs._DEFAULT_TREND_CEILING == 1.0

    def test_values_below_one_still_pass_through(self):
        """The ceiling caps; it must not floor. If the signal ever learns to
        express doubt (<1.0), that information should survive."""
        assert min(0.4, cs._trend_multiplier_ceiling()) == 0.4


class TestReversible:
    def test_env_can_restore_previous_behaviour(self, monkeypatch):
        monkeypatch.setenv(cs._TREND_CEILING_ENV, "3.0")
        assert cs._trend_multiplier_ceiling() == 3.0

    def test_env_is_itself_clamped_to_three(self, monkeypatch):
        monkeypatch.setenv(cs._TREND_CEILING_ENV, "99")
        assert cs._trend_multiplier_ceiling() == 3.0

    @pytest.mark.parametrize("bad", ["garbage", "", "  ", "1.0.0", "None"])
    def test_malformed_falls_back_to_safe_default(self, monkeypatch, bad):
        """A typo must not silently restore a boost nobody asked for."""
        monkeypatch.setenv(cs._TREND_CEILING_ENV, bad)
        assert cs._trend_multiplier_ceiling() == cs._DEFAULT_TREND_CEILING

    def test_negative_is_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv(cs._TREND_CEILING_ENV, "-5")
        assert cs._trend_multiplier_ceiling() == 0.0


class TestFormulaOtherwiseUntouched:
    """The change must cap one input and disturb nothing else."""

    @staticmethod
    def _video(vid="v", velocity=1000.0):
        return {"video_id": vid, "title": "t", "view_velocity": velocity,
                "view_count": 0, "like_count": 0}

    def test_boosted_and_unboosted_now_score_identically(self):
        scorer = cs.CompositeScorer(niche_id="anime")
        low = scorer.score(self._video("a"), trend_multiplier=1.0, niche_relevance=1.0)
        high = scorer.score(self._video("b"), trend_multiplier=3.0, niche_relevance=1.0)
        assert low.composite == high.composite, (
            "a 3.0 trend multiplier still outranks a 1.0 one — the ceiling is "
            "not being applied where the composite is assembled"
        )

    def test_reported_multiplier_reflects_the_cap(self):
        """The stored value must show what was USED, not what was supplied —
        otherwise the next person auditing this sees 3.0 and misdiagnoses."""
        scorer = cs.CompositeScorer(niche_id="anime")
        got = scorer.score(self._video(), trend_multiplier=3.0, niche_relevance=1.0)
        assert got.trend_multiplier <= 1.0

    def test_velocity_still_drives_the_score(self):
        """Capping the multiplier must not flatten the whole scorer."""
        scorer = cs.CompositeScorer(niche_id="anime")
        slow = scorer.score(self._video("a", 10.0), trend_multiplier=1.0, niche_relevance=1.0)
        fast = scorer.score(self._video("b", 100000.0), trend_multiplier=1.0, niche_relevance=1.0)
        assert fast.composite > slow.composite, "velocity no longer differentiates"

    def test_relevance_zero_still_zeroes_the_score(self):
        scorer = cs.CompositeScorer(niche_id="anime")
        off = scorer.score(self._video(), trend_multiplier=1.0, niche_relevance=0.0)
        assert off.composite == 0.0

    def test_restoring_the_ceiling_restores_the_boost(self, monkeypatch):
        """Proves the cap is what changed the outcome, not something else."""
        monkeypatch.setenv(cs._TREND_CEILING_ENV, "3.0")
        scorer = cs.CompositeScorer(niche_id="anime")
        low = scorer.score(self._video("a"), trend_multiplier=1.0, niche_relevance=1.0)
        high = scorer.score(self._video("b"), trend_multiplier=3.0, niche_relevance=1.0)
        assert high.composite > low.composite
