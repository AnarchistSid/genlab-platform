"""Property-based tests for WelfordNormalizer and temporal_decay."""
from hypothesis import given, assume
from hypothesis import strategies as st

from genlab_core.intelligence.score_normalizer import WelfordNormalizer, temporal_decay

safe_float = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


@given(st.lists(safe_float, min_size=2, max_size=500))
def test_mean_within_input_range(values):
    """Mean must always be within [min(values), max(values)]."""
    norm = WelfordNormalizer()
    for v in values:
        norm.update(v)
    assert min(values) - 1e-9 <= norm.mean <= max(values) + 1e-9


@given(st.lists(safe_float, min_size=2, max_size=500))
def test_variance_nonnegative(values):
    """Variance must never go negative."""
    norm = WelfordNormalizer()
    for v in values:
        norm.update(v)
    assert norm.variance >= -1e-12  # Small tolerance for float arithmetic


@given(st.lists(st.just(42.0), min_size=5, max_size=100))
def test_zero_variance_constant_input(values):
    """Variance should be ~0 for constant input."""
    norm = WelfordNormalizer()
    for v in values:
        norm.update(v)
    assert norm.variance < 1e-9


@given(safe_float)
def test_normalise_returns_0_5_with_insufficient_data(value):
    """normalise() returns 0.5 when <2 observations."""
    norm = WelfordNormalizer()
    norm.update(value)
    assert norm.normalise(value) == 0.5


@given(st.lists(safe_float, min_size=10, max_size=200), safe_float)
def test_normalise_clipped_to_0_1(values, query):
    """normalise() with clip=True must return value in [0, 1]."""
    norm = WelfordNormalizer()
    for v in values:
        norm.update(v)
    result = norm.normalise(query, clip=True)
    assert 0.0 <= result <= 1.0


@given(st.lists(safe_float, min_size=5, max_size=200))
def test_serialization_roundtrip(values):
    """to_dict() / from_dict() must preserve state exactly."""
    norm = WelfordNormalizer()
    for v in values:
        norm.update(v)

    restored = WelfordNormalizer.from_dict(norm.to_dict())
    assert restored.count == norm.count
    assert abs(restored.mean - norm.mean) < 1e-12
    assert abs(restored.variance - norm.variance) < 1e-12


@given(st.lists(safe_float, min_size=30, max_size=300))
def test_mean_equals_average(values):
    """Running mean should equal arithmetic mean."""
    norm = WelfordNormalizer()
    for v in values:
        norm.update(v)
    expected = sum(values) / len(values)
    assert abs(norm.mean - expected) < 1e-6


# --- temporal_decay property tests ---


@given(
    st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False),
)
def test_temporal_decay_bounded(age_hours, half_life):
    """Decay multiplier must be in [0, 1].

    NOTE: The production docstring claims (0, 1] but extreme age/half_life
    ratios (e.g., age=1075, half_life=1.0) cause float64 underflow to 0.0.
    This is a known edge case — 2^(-1075) is below the float64 subnormal
    threshold. We test [0, 1] here to reflect actual behavior.
    """
    result = temporal_decay(age_hours, half_life)
    assert 0.0 <= result <= 1.0 + 1e-9


@given(st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False))
def test_temporal_decay_zero_age_is_one(half_life):
    """At age=0, decay should be 1.0."""
    result = temporal_decay(0.0, half_life)
    assert abs(result - 1.0) < 1e-9


@given(
    st.floats(min_value=0.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.1, max_value=1000.0, allow_nan=False, allow_infinity=False),
)
def test_temporal_decay_monotonic(age_a, age_b, half_life):
    """Older content should have equal or lower decay multiplier."""
    assume(age_a <= age_b)
    assert temporal_decay(age_a, half_life) >= temporal_decay(age_b, half_life) - 1e-9
