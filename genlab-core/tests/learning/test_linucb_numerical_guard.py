"""Regression test for LinUCB numerical edge-case guards."""
from __future__ import annotations

import numpy as np

from genlab_core.learning.linucb import LinUCBArm


def test_predict_normal_case_returns_finite_score() -> None:
    arm = LinUCBArm(d=6)
    x = np.array([1.0, 0.5, 0.3, 0.2, 0.1, 0.0])
    score = arm.predict(x)
    assert np.isfinite(score)


def test_predict_handles_inf_in_context_gracefully() -> None:
    arm = LinUCBArm(d=6)
    x_inf = np.array([np.inf, 0.0, 0.0, 0.0, 0.0, 0.0])
    score = arm.predict(x_inf)
    assert np.isfinite(score)
    assert score == 0.5


def test_predict_handles_nan_in_context_gracefully() -> None:
    arm = LinUCBArm(d=6)
    x_nan = np.array([np.nan, 0.0, 0.0, 0.0, 0.0, 0.0])
    score = arm.predict(x_nan)
    assert np.isfinite(score)


def test_predict_after_many_updates_never_produces_nan() -> None:
    """Heavy update loops shouldn't corrupt downstream scores."""
    arm = LinUCBArm(d=6)
    rng = np.random.default_rng(42)
    for _ in range(1000):
        x = rng.random(6)
        reward = float(rng.random())
        arm.update(x, reward)
    score = arm.predict(rng.random(6))
    assert np.isfinite(score)


def test_predict_never_raises_on_degenerate_matrix() -> None:
    """Must NOT raise — downstream code depends on this."""
    arm = LinUCBArm(d=6)
    arm.A = np.full((6, 6), np.inf)
    score = arm.predict(np.array([1.0, 0, 0, 0, 0, 0]))
    assert np.isfinite(score)


def test_predict_guards_against_singular_matrix() -> None:
    """Explicitly singular matrix — LinAlgError must become neutral score."""
    arm = LinUCBArm(d=6)
    # All-zeros matrix is perfectly singular
    arm.A = np.zeros((6, 6))
    score = arm.predict(np.array([1.0, 0, 0, 0, 0, 0]))
    assert score == 0.5
