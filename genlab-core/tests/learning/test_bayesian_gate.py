"""Tests for genlab_core.learning.bayesian_gate — Engine 1.5.

Covers the 8 contracts in the spec: env-flag no-op, niche-size
threshold, Thompson stochasticity, MAP convergence on synthetic data
(both directions), ε-greedy floor, fail-OPEN on DB error, JSON
state persistence.

Most tests need numpy + scipy. Skip if absent so the suite remains
runnable in env-stripped CI runners.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from genlab_core.learning import bayesian_gate
from genlab_core.learning.bayesian_gate import (
    FEATURE_NAMES,
    N_FEATURES,
    NichePosterior,
    fit_niche_posterior,
    load_posterior,
    refit_niche,
    reset_decision_counts,
    sample_preference,
    save_posterior,
)

# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point the gate's state file at a tmp path so the production
    .tmp/bayesian_gate_state.json is never touched. Also clears the
    in-process ε-greedy decision counter so test ordering doesn't
    leak counts between tests."""
    state_path = tmp_path / "bayesian_gate_state.json"
    monkeypatch.setenv("BAYESIAN_GATE_STATE_PATH", str(state_path))
    reset_decision_counts()
    yield state_path
    reset_decision_counts()


@pytest.fixture
def gate_enabled(monkeypatch):
    """Flip the env flag on for the duration of one test."""
    monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", "1")
    yield


@pytest.fixture
def sample_blueprint():
    """A reasonable VISUAL_READY-ish blueprint dict for inference."""
    return {
        "id": "00000000-0000-4000-8000-000000000001",
        "niche_id": "gaming",
        "hook_text": "Wild moment in the finals",
        "extra": {
            "composite_score": 0.55,
            "virality_score": 0.08,
            "hook_classifier_score": 0.65,
        },
    }


@pytest.fixture
def sample_decision_dict():
    """The companion AutoApprovalDecision-shaped dict."""
    return {
        "passed_checks": ["has_video", "has_hook", "composite_score"],
        "failed_checks": [],
        "confidence": 0.72,
    }


def _make_synthetic_dataset(
    n: int, true_intercept: float, true_w: list[float], seed: int = 42
) -> tuple[np.ndarray, np.ndarray]:
    """Generate (X, y) from a known logistic-regression model.

    Returns an (n, N_FEATURES) design matrix (intercept column first)
    and an (n,) binary label vector sampled from σ(w·x).
    """
    rng = np.random.default_rng(seed)
    # Random feature draws roughly matching the live-data distribution:
    # all features in [0, 1].
    feats = rng.uniform(0.0, 1.0, size=(n, N_FEATURES - 1))
    X = np.hstack([np.ones((n, 1)), feats])  # intercept column
    w_true = np.concatenate([[true_intercept], np.asarray(true_w)])
    p_true = 1.0 / (1.0 + np.exp(-(X @ w_true)))
    y = (rng.uniform(size=n) < p_true).astype(np.float64)
    return X, y


def _persist_posterior_from_dataset(niche_id: str, X: np.ndarray, y: np.ndarray) -> NichePosterior:
    """Fit + save a posterior for a niche from synthetic data."""
    w_map, cov = fit_niche_posterior(X, y)
    posterior = NichePosterior(
        niche_id=niche_id,
        mean=w_map.tolist(),
        cov=cov.tolist(),
        n_rows=int(X.shape[0]),
    )
    assert save_posterior(posterior) is True
    return posterior


# ─────────────────────────────────────────────────────────────────
# 1. test_no_op_when_flag_unset
# ─────────────────────────────────────────────────────────────────


def test_no_op_when_flag_unset(monkeypatch, isolated_state, sample_blueprint, sample_decision_dict):
    """Default-OFF contract: flag unset → always (0.5, 0.5)."""
    monkeypatch.delenv("GENLAB_BAYESIAN_GATE_ENABLED", raising=False)

    # Even if a posterior is persisted, the env flag overrides.
    X, y = _make_synthetic_dataset(200, true_intercept=0.0, true_w=[3.0] + [0.0] * 5)
    _persist_posterior_from_dataset("gaming", X, y)

    sampled, mean = sample_preference(
        sample_blueprint, "gaming", decision_dict=sample_decision_dict
    )
    assert sampled == 0.5
    assert mean == 0.5

    # "0" also disables (explicit).
    monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", "0")
    sampled2, mean2 = sample_preference(
        sample_blueprint, "gaming", decision_dict=sample_decision_dict
    )
    assert sampled2 == 0.5
    assert mean2 == 0.5


# ─────────────────────────────────────────────────────────────────
# 2. test_returns_neutral_when_niche_has_insufficient_data
# ─────────────────────────────────────────────────────────────────


def test_returns_neutral_when_niche_has_insufficient_data(
    gate_enabled, isolated_state, sample_blueprint, sample_decision_dict
):
    """Niche with 30 rows (<50 threshold) → (0.5, 0.5)."""
    X, y = _make_synthetic_dataset(30, true_intercept=2.0, true_w=[1.0] * 6)
    w_map, cov = fit_niche_posterior(X, y)
    posterior = NichePosterior(
        niche_id="anime",
        mean=w_map.tolist(),
        cov=cov.tolist(),
        n_rows=30,  # Deliberately below _MIN_ROWS_PER_NICHE
    )
    save_posterior(posterior)

    # Disable ε-greedy in this test by exhausting the counter — we want
    # to verify the niche-size short-circuit, not the random floor.
    for _ in range(bayesian_gate._EPSILON_DECISION_FLOOR + 1):
        bayesian_gate._bump_decision_count("anime")

    sampled, mean = sample_preference(sample_blueprint, "anime", decision_dict=sample_decision_dict)
    assert sampled == 0.5
    assert mean == 0.5


def test_returns_neutral_when_no_posterior_exists(
    gate_enabled, isolated_state, sample_blueprint, sample_decision_dict
):
    """Niche with NO persisted posterior → (0.5, 0.5)."""
    for _ in range(bayesian_gate._EPSILON_DECISION_FLOOR + 1):
        bayesian_gate._bump_decision_count("unknown_niche")
    sampled, mean = sample_preference(
        sample_blueprint, "unknown_niche", decision_dict=sample_decision_dict
    )
    assert sampled == 0.5
    assert mean == 0.5


# ─────────────────────────────────────────────────────────────────
# 3. test_thompson_sample_varies_per_call
# ─────────────────────────────────────────────────────────────────


def test_thompson_sample_varies_per_call(
    gate_enabled, isolated_state, sample_blueprint, sample_decision_dict
):
    """Consecutive calls with same input return different samples
    (the posterior is non-degenerate, Thompson resamples each time)."""
    X, y = _make_synthetic_dataset(200, true_intercept=0.0, true_w=[2.0, 1.0, 0.5, 0.0, 0.0, 0.0])
    _persist_posterior_from_dataset("gaming", X, y)

    # Burn the ε-greedy floor decisions so they don't contaminate the
    # variance check (those are uniform random, not Thompson samples).
    for _ in range(bayesian_gate._EPSILON_DECISION_FLOOR + 1):
        bayesian_gate._bump_decision_count("gaming")

    samples = set()
    means = set()
    for _ in range(20):
        s, m = sample_preference(sample_blueprint, "gaming", decision_dict=sample_decision_dict)
        samples.add(round(s, 6))
        means.add(round(m, 6))

    # Thompson samples vary; the posterior mean is deterministic for
    # a fixed posterior + fixed input.
    assert len(samples) > 1, "Thompson sample should vary across calls"
    assert len(means) == 1, "posterior-mean is deterministic given fixed input"


# ─────────────────────────────────────────────────────────────────
# 4. test_posterior_mean_increases_with_more_approve_data
# ─────────────────────────────────────────────────────────────────


def test_posterior_mean_increases_with_more_approve_data(
    gate_enabled, isolated_state, sample_decision_dict
):
    """Fit on synthetic data where label=1 ≈ always. Posterior mean
    on a typical-feature blueprint should be ≥ 0.7."""
    n = 200
    rng = np.random.default_rng(123)
    feats = rng.uniform(0.0, 1.0, size=(n, N_FEATURES - 1))
    X = np.hstack([np.ones((n, 1)), feats])
    # 95% approves: heavy positive bias regardless of features.
    y = (rng.uniform(size=n) < 0.95).astype(np.float64)
    _persist_posterior_from_dataset("gaming", X, y)

    # Burn the ε-greedy floor before sampling.
    for _ in range(bayesian_gate._EPSILON_DECISION_FLOOR + 1):
        bayesian_gate._bump_decision_count("gaming")

    # Typical-feature blueprint
    blueprint = {
        "niche_id": "gaming",
        "extra": {
            "composite_score": 0.5,
            "virality_score": 0.1,
            "hook_classifier_score": 0.5,
        },
    }
    _, mean = sample_preference(blueprint, "gaming", decision_dict=sample_decision_dict)
    assert mean >= 0.7, f"expected mean ≥ 0.7 for approve-heavy data, got {mean:.3f}"


# ─────────────────────────────────────────────────────────────────
# 5. test_posterior_mean_decreases_with_more_reject_data
# ─────────────────────────────────────────────────────────────────


def test_posterior_mean_decreases_with_more_reject_data(
    gate_enabled, isolated_state, sample_decision_dict
):
    """Fit on synthetic data where label=0 ≈ always. Posterior mean
    on a typical-feature blueprint should be ≤ 0.3."""
    n = 200
    rng = np.random.default_rng(456)
    feats = rng.uniform(0.0, 1.0, size=(n, N_FEATURES - 1))
    X = np.hstack([np.ones((n, 1)), feats])
    # 95% rejects.
    y = (rng.uniform(size=n) < 0.05).astype(np.float64)
    _persist_posterior_from_dataset("anime", X, y)

    for _ in range(bayesian_gate._EPSILON_DECISION_FLOOR + 1):
        bayesian_gate._bump_decision_count("anime")

    blueprint = {
        "niche_id": "anime",
        "extra": {
            "composite_score": 0.5,
            "virality_score": 0.1,
            "hook_classifier_score": 0.5,
        },
    }
    _, mean = sample_preference(blueprint, "anime", decision_dict=sample_decision_dict)
    assert mean <= 0.3, f"expected mean ≤ 0.3 for reject-heavy data, got {mean:.3f}"


# ─────────────────────────────────────────────────────────────────
# 6. test_epsilon_greedy_floor_active_in_cold_start
# ─────────────────────────────────────────────────────────────────


def test_epsilon_greedy_floor_active_in_cold_start(
    gate_enabled, isolated_state, sample_decision_dict, monkeypatch
):
    """First 100 decisions for a fresh niche should occasionally
    short-circuit to uniform random — show non-trivial variance
    even with a degenerate (very tight) posterior."""
    # Construct a posterior that would deterministically give ≈0.99
    # if Thompson dominated (no variance) — but ε-greedy should still
    # inject uniform draws.
    n = 500
    rng = np.random.default_rng(789)
    feats = rng.uniform(0.0, 1.0, size=(n, N_FEATURES - 1))
    X = np.hstack([np.ones((n, 1)), feats])
    y = np.ones(n, dtype=np.float64)  # all approves → extreme posterior
    _persist_posterior_from_dataset("sports", X, y)

    blueprint = {
        "niche_id": "sports",
        "extra": {
            "composite_score": 0.5,
            "virality_score": 0.05,
            "hook_classifier_score": 0.5,
        },
    }

    # Force the ε-greedy coin to flip "explore" on every call so we
    # can deterministically observe the uniform-random path firing.
    # (Tests the *mechanism* of the ε-greedy floor, not its prob.)
    with patch("genlab_core.learning.bayesian_gate.random.random") as mock_rand:
        # First call: coin = 0.05 < 0.10 → ε-greedy fires, then returns
        # a uniform draw. mock_rand is called TWICE per ε-greedy path
        # (once for coin, once for uniform draw).
        mock_rand.side_effect = [0.05, 0.13, 0.05, 0.87, 0.05, 0.42]
        s1, m1 = sample_preference(blueprint, "sports", decision_dict=sample_decision_dict)
        s2, m2 = sample_preference(blueprint, "sports", decision_dict=sample_decision_dict)
        s3, m3 = sample_preference(blueprint, "sports", decision_dict=sample_decision_dict)

    # Mean is reported as 0.5 (prior) under ε-greedy by spec.
    assert m1 == 0.5
    assert m2 == 0.5
    assert m3 == 0.5
    # Samples are the uniform draws we injected.
    assert s1 == 0.13
    assert s2 == 0.87
    assert s3 == 0.42


# ─────────────────────────────────────────────────────────────────
# 7. test_fail_open_on_db_error
# ─────────────────────────────────────────────────────────────────


def test_fail_open_on_db_error(
    gate_enabled, isolated_state, sample_blueprint, sample_decision_dict
):
    """A DB exception raised inside the load path returns (0.5, 0.5).
    Mirrors the calibration_logger fail-OPEN contract."""

    def _raise_psycopg(*args, **kwargs):
        raise RuntimeError("simulated psycopg connection error")

    # Burn the ε-greedy floor so we're definitely in the posterior path.
    for _ in range(bayesian_gate._EPSILON_DECISION_FLOOR + 1):
        bayesian_gate._bump_decision_count("gaming")

    with patch(
        "genlab_core.learning.bayesian_gate.load_posterior",
        side_effect=_raise_psycopg,
    ):
        sampled, mean = sample_preference(
            sample_blueprint, "gaming", decision_dict=sample_decision_dict
        )
    assert sampled == 0.5
    assert mean == 0.5


def test_fail_open_on_refit_db_error(isolated_state, monkeypatch):
    """refit_niche fail-OPEN when DB unreachable — returns None, never
    raises. The nightly worker calls refit_niche per niche; a single
    failure must not crash the whole batch."""
    # No DATABASE_URL set → _fetch_training_rows returns (None, None, 0)
    # → refit_niche skips with INFO log, returns None.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = refit_niche("gaming")
    assert result is None


# ─────────────────────────────────────────────────────────────────
# 8. test_state_file_persisted_and_loaded
# ─────────────────────────────────────────────────────────────────


def test_state_file_persisted_and_loaded(isolated_state):
    """Round-trip: save_posterior → load_posterior returns identical mean+cov."""
    X, y = _make_synthetic_dataset(100, true_intercept=0.5, true_w=[1.5, 0.8, 0.3, 0.0, 0.0, 0.0])
    w_map, cov = fit_niche_posterior(X, y)
    posterior_in = NichePosterior(
        niche_id="movies",
        mean=w_map.tolist(),
        cov=cov.tolist(),
        n_rows=100,
    )
    assert save_posterior(posterior_in) is True
    assert isolated_state.exists()

    # Disk format is human-readable JSON
    raw = json.loads(isolated_state.read_text())
    assert "movies" in raw
    assert "mean" in raw["movies"]
    assert "cov" in raw["movies"]
    assert raw["movies"]["n_rows"] == 100
    assert len(raw["movies"]["mean"]) == N_FEATURES
    assert len(raw["movies"]["cov"]) == N_FEATURES

    # Round-trip via load_posterior in a "fresh instance" sense (no
    # in-memory cache; load_posterior re-reads the disk file each call).
    loaded = load_posterior("movies")
    assert loaded is not None
    assert loaded.niche_id == "movies"
    assert loaded.n_rows == 100
    assert loaded.feature_names == list(FEATURE_NAMES)

    np.testing.assert_allclose(np.asarray(loaded.mean), np.asarray(posterior_in.mean), rtol=1e-10)
    np.testing.assert_allclose(np.asarray(loaded.cov), np.asarray(posterior_in.cov), rtol=1e-10)


def test_state_file_supports_multiple_niches(isolated_state):
    """Saving one niche doesn't overwrite another — merge semantics."""
    X1, y1 = _make_synthetic_dataset(80, 0.0, [2.0] + [0.0] * 5, seed=1)
    X2, y2 = _make_synthetic_dataset(80, 0.0, [-2.0] + [0.0] * 5, seed=2)
    w1, c1 = fit_niche_posterior(X1, y1)
    w2, c2 = fit_niche_posterior(X2, y2)
    save_posterior(NichePosterior(niche_id="gaming", mean=w1.tolist(), cov=c1.tolist(), n_rows=80))
    save_posterior(NichePosterior(niche_id="anime", mean=w2.tolist(), cov=c2.tolist(), n_rows=80))

    gaming = load_posterior("gaming")
    anime = load_posterior("anime")
    assert gaming is not None and anime is not None
    assert gaming.n_rows == 80
    assert anime.n_rows == 80
    # Means differ — the two niches stayed separate.
    assert gaming.mean != anime.mean
