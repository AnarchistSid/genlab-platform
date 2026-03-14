"""Example-based tests for score_normalizer (complement Sprint 21 Hypothesis tests).

These tests use concrete, hand-computed values to serve as documentation and
regression anchors for the scoring math infrastructure.
"""

import pytest

from genlab_core.intelligence.score_normalizer import (
    WelfordNormalizer,
    temporal_decay,
    stouffer_composite,
)


# ---------------------------------------------------------------------------
# WelfordNormalizer — concrete examples
# ---------------------------------------------------------------------------


class TestWelfordExamples:
    def test_mean_single_value(self):
        w = WelfordNormalizer()
        w.update(5.0)
        assert w.mean == pytest.approx(5.0)
        assert w.count == 1

    def test_zscore_known_distribution(self):
        w = WelfordNormalizer()
        for v in [10, 20, 30, 40, 50]:
            w.update(v)
        # Mean = 30, population variance = 200, std = sqrt(200) ~ 14.14
        assert w.mean == pytest.approx(30.0)
        assert w.variance == pytest.approx(200.0)

        # normalise(30) -> z=0 -> rescaled to 0.5
        assert w.normalise(30.0) == pytest.approx(0.5, abs=0.01)
        # normalise(50) should be > 0.5 (above the mean)
        assert w.normalise(50.0) > 0.5

    def test_mean_equals_average(self):
        w = WelfordNormalizer()
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        for v in values:
            w.update(v)
        assert w.mean == pytest.approx(sum(values) / len(values))

    def test_large_spread_normalise_clips(self):
        """Values with large spread: outlier normalise clips to 0 or 1."""
        w = WelfordNormalizer()
        for v in [0.0, 100.0]:
            w.update(v)
        assert w.normalise(1000.0) == 1.0
        assert w.normalise(-1000.0) == 0.0


# ---------------------------------------------------------------------------
# temporal_decay — concrete examples
# ---------------------------------------------------------------------------


class TestTemporalDecayExamples:
    def test_half_life_at_boundary(self):
        """age == half_life -> decay = 0.5."""
        result = temporal_decay(24.0, 24.0)
        assert result == pytest.approx(0.5, abs=1e-9)

    def test_age_zero_returns_one(self):
        assert temporal_decay(0.0, 24.0) == pytest.approx(1.0)

    def test_two_half_lives_returns_quarter(self):
        result = temporal_decay(48.0, 24.0)
        assert result == pytest.approx(0.25, abs=1e-9)

    def test_very_old_content_near_zero(self):
        """Content 10 half-lives old is nearly worthless."""
        result = temporal_decay(240.0, 24.0)
        assert result < 0.001

    def test_custom_half_life_gaming(self):
        """Gaming clips with 48-hour half-life: 48h old -> 0.5."""
        result = temporal_decay(48.0, 48.0)
        assert result == pytest.approx(0.5, abs=1e-9)


# ---------------------------------------------------------------------------
# stouffer_composite — concrete examples
# ---------------------------------------------------------------------------


class TestStoufferCompositeExamples:
    def test_single_signal_passthrough(self):
        """Single signal with weight 1.0 should pass through directly."""
        result = stouffer_composite(
            scores={"virality": 0.8},
            weights={"virality": 1.0},
        )
        # w*z / sqrt(w^2) = 1.0*0.8 / 1.0 = 0.8
        assert result == pytest.approx(0.8, abs=1e-9)

    def test_missing_signal_gets_neutral_fill(self):
        """Signal in weights but not scores is filled with 0.5."""
        result = stouffer_composite(
            scores={"virality": 0.9},
            weights={"virality": 0.5, "recency": 0.5},
        )
        # (0.5*0.9 + 0.5*0.5) / sqrt(0.25+0.25) = 0.7 / 0.707 ~ 0.99
        assert 0.0 <= result <= 1.0
        assert result > 0.5  # High virality should pull composite up

    def test_empty_weights_returns_neutral(self):
        assert stouffer_composite(scores={"a": 0.9}, weights={}) == 0.5

    def test_higher_scores_give_higher_composite(self):
        low = stouffer_composite(
            scores={"a": 0.2, "b": 0.3},
            weights={"a": 0.5, "b": 0.5},
        )
        high = stouffer_composite(
            scores={"a": 0.8, "b": 0.9},
            weights={"a": 0.5, "b": 0.5},
        )
        assert high > low

    def test_all_zero_scores(self):
        """All zero scores should produce a low composite."""
        result = stouffer_composite(
            scores={"a": 0.0, "b": 0.0},
            weights={"a": 0.5, "b": 0.5},
        )
        assert result == pytest.approx(0.0)
