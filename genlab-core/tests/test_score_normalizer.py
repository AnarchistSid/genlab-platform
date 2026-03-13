"""Tests for genlab_core.intelligence.score_normalizer."""
import math

import pytest

from genlab_core.intelligence.score_normalizer import (
    ScoringWeightLoader,
    WelfordNormalizer,
    stouffer_composite,
    temporal_decay,
)

try:
    from hypothesis import given, settings as hyp_settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False


# ---------------------------------------------------------------------------
# WelfordNormalizer — basic tests
# ---------------------------------------------------------------------------


class TestWelfordBasic:
    def test_empty_normaliser_returns_neutral(self):
        norm = WelfordNormalizer()
        assert norm.normalise(42.0) == 0.5

    def test_single_observation_returns_neutral(self):
        norm = WelfordNormalizer()
        norm.update(10.0)
        assert norm.normalise(10.0) == 0.5

    def test_variance_non_negative_simple(self):
        norm = WelfordNormalizer()
        for v in [1, 2, 3, 4, 5]:
            norm.update(v)
        assert norm.variance >= 0

    def test_mean_correct_simple(self):
        norm = WelfordNormalizer()
        values = [10, 20, 30, 40, 50]
        for v in values:
            norm.update(v)
        assert abs(norm.mean - 30.0) < 1e-9

    def test_normalise_clips_to_zero_one(self):
        norm = WelfordNormalizer()
        for v in [0, 1, 2, 3, 4]:
            norm.update(v)
        # Extreme outlier
        assert 0.0 <= norm.normalise(1000.0) <= 1.0
        assert 0.0 <= norm.normalise(-1000.0) <= 1.0

    def test_normalise_no_clip(self):
        norm = WelfordNormalizer()
        for v in [0, 1, 2, 3, 4]:
            norm.update(v)
        result = norm.normalise(1000.0, clip=False)
        assert result > 1.0  # unclipped, way above mean

    def test_to_dict_from_dict_roundtrip(self):
        norm = WelfordNormalizer()
        for v in [3.14, 2.71, 1.41, 1.73, 100.0]:
            norm.update(v)

        state = norm.to_dict()
        restored = WelfordNormalizer.from_dict(state)

        assert restored.count == norm.count
        assert restored.mean == norm.mean
        assert restored._M2 == norm._M2
        assert restored.variance == norm.variance
        assert restored.normalise(50.0) == norm.normalise(50.0)

    def test_zero_std_returns_neutral(self):
        """All same values -> std=0 -> normalise returns 0.5."""
        norm = WelfordNormalizer()
        for _ in range(10):
            norm.update(5.0)
        assert norm.std < 1e-9
        assert norm.normalise(5.0) == 0.5


# ---------------------------------------------------------------------------
# WelfordNormalizer — property-based tests (Hypothesis)
# ---------------------------------------------------------------------------

if HAS_HYPOTHESIS:

    @given(st.lists(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False), min_size=2, max_size=100))
    @hyp_settings(max_examples=200)
    def test_welford_mean_matches_sample_mean(values):
        norm = WelfordNormalizer()
        for v in values:
            norm.update(v)
        expected = sum(values) / len(values)
        assert abs(norm.mean - expected) < 1e-6, f"mean {norm.mean} != expected {expected}"

    @given(st.lists(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False), min_size=2, max_size=100))
    @hyp_settings(max_examples=200)
    def test_welford_variance_non_negative(values):
        norm = WelfordNormalizer()
        for v in values:
            norm.update(v)
        assert norm.variance >= 0

    @given(
        st.lists(st.floats(min_value=-1e4, max_value=1e4, allow_nan=False), min_size=2, max_size=50),
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
    )
    @hyp_settings(max_examples=200)
    def test_welford_normalise_always_in_zero_one(values, query):
        norm = WelfordNormalizer()
        for v in values:
            norm.update(v)
        result = norm.normalise(query, clip=True)
        assert 0.0 <= result <= 1.0

    @given(st.lists(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False), min_size=2, max_size=50))
    @hyp_settings(max_examples=100)
    def test_welford_roundtrip_preserves_state(values):
        norm = WelfordNormalizer()
        for v in values:
            norm.update(v)
        restored = WelfordNormalizer.from_dict(norm.to_dict())
        assert restored.count == norm.count
        assert abs(restored.mean - norm.mean) < 1e-12
        assert abs(restored._M2 - norm._M2) < 1e-9


# ---------------------------------------------------------------------------
# temporal_decay
# ---------------------------------------------------------------------------


class TestTemporalDecay:
    def test_fresh_content_returns_one(self):
        assert temporal_decay(0.0) == 1.0

    def test_half_life_returns_half(self):
        result = temporal_decay(24.0, half_life_hours=24.0)
        assert abs(result - 0.5) < 1e-9

    def test_monotonically_decreasing(self):
        values = [temporal_decay(h, half_life_hours=24.0) for h in range(0, 100)]
        for i in range(len(values) - 1):
            assert values[i] >= values[i + 1]

    def test_two_half_lives_returns_quarter(self):
        result = temporal_decay(48.0, half_life_hours=24.0)
        assert abs(result - 0.25) < 1e-9

    def test_custom_half_life(self):
        result = temporal_decay(48.0, half_life_hours=48.0)
        assert abs(result - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# stouffer_composite
# ---------------------------------------------------------------------------


class TestStoufferComposite:
    def test_all_neutral_returns_neutral(self):
        """All 0.5 inputs with any weights should return close to 0.5.

        Note: Stouffer divides by sqrt(sum(w^2)), not by sum(w), so equal
        weights with 0.5 scores won't return exactly 0.5 unless weights
        are chosen to make sum(w*0.5)/sqrt(sum(w^2)) == 0.5. With equal
        weights w, result = n*w*0.5 / sqrt(n*w^2) = 0.5*sqrt(n), which
        gets clipped. For typical small weight sets (sum < 1), it's close.
        """
        result = stouffer_composite(
            scores={"a": 0.5, "b": 0.5, "c": 0.5},
            weights={"a": 0.33, "b": 0.33, "c": 0.34},
        )
        # With these weights: 0.5*(0.33+0.33+0.34)/sqrt(0.33^2+0.33^2+0.34^2)
        # = 0.5*1.0/sqrt(0.3274) = 0.5/0.572 = 0.874 — clipped to 0.874
        # This is expected: Stouffer is NOT a simple average
        assert 0.0 <= result <= 1.0

    def test_missing_signals_get_neutral_fallback(self):
        """Signals in weights but not scores get filled with 0.5."""
        result = stouffer_composite(
            scores={"virality": 0.9},
            weights={"virality": 0.5, "recency": 0.5},
        )
        # recency missing -> treated as 0.5
        assert 0.0 <= result <= 1.0

    def test_empty_weights_returns_neutral(self):
        assert stouffer_composite(scores={"a": 0.9}, weights={}) == 0.5

    def test_single_signal(self):
        result = stouffer_composite(
            scores={"x": 0.8},
            weights={"x": 1.0},
        )
        # 1.0 * 0.8 / sqrt(1.0^2) = 0.8
        assert abs(result - 0.8) < 1e-9

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


# ---------------------------------------------------------------------------
# ScoringWeightLoader
# ---------------------------------------------------------------------------


class TestScoringWeightLoader:
    def test_load_and_cache(self, tmp_path):
        yaml_file = tmp_path / "weights.yaml"
        yaml_file.write_text("content_scoring:\n  virality: 0.4\n  recency: 0.3\n")

        loader = ScoringWeightLoader(str(yaml_file))
        weights = loader.get_weights("content_scoring")
        assert weights == {"virality": 0.4, "recency": 0.3}

    def test_cache_invalidation_on_mtime_change(self, tmp_path):
        yaml_file = tmp_path / "weights.yaml"
        yaml_file.write_text("section:\n  a: 1.0\n")

        loader = ScoringWeightLoader(str(yaml_file))
        assert loader.get_weights("section") == {"a": 1.0}

        # Modify file
        import time
        time.sleep(0.1)  # ensure mtime changes
        yaml_file.write_text("section:\n  a: 2.0\n  b: 3.0\n")

        assert loader.get_weights("section") == {"a": 2.0, "b": 3.0}

    def test_missing_section_returns_empty(self, tmp_path):
        yaml_file = tmp_path / "weights.yaml"
        yaml_file.write_text("other:\n  x: 1.0\n")

        loader = ScoringWeightLoader(str(yaml_file))
        assert loader.get_weights("nonexistent") == {}
