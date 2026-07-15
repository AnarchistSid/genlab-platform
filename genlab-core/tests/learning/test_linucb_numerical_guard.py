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
    """Inf in context → -inf score (never raises, argmax skips broken arm).

    2026-07-14 design review: pin flipped from 0.5 → -inf. Docstring
    had claimed "Thompson fallback upstream picks this arm's score"
    but tracing `LinUCBBandit.select_with_propensity:585` shows pure
    argmax with NO Thompson fallback. In mature bandit state a
    broken arm returning 0.5 could beat a healthy arm returning
    negative exploitation (bad-content prediction). -inf guarantees
    a broken arm never wins argmax.
    """
    arm = LinUCBArm(d=6)
    x_inf = np.array([np.inf, 0.0, 0.0, 0.0, 0.0, 0.0])
    score = arm.predict(x_inf)
    # -inf is not "finite" but IS a defined float — argmax over
    # {healthy: 0.3, broken: -inf} correctly picks healthy.
    assert score == float("-inf")


def test_predict_handles_nan_in_context_gracefully() -> None:
    arm = LinUCBArm(d=6)
    x_nan = np.array([np.nan, 0.0, 0.0, 0.0, 0.0, 0.0])
    score = arm.predict(x_nan)
    # NaN input → non-finite score → -inf fallback (see F1 rationale above).
    assert score == float("-inf")


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
    # -inf is not-raise + argmax-safe (see other tests for rationale).
    assert score == float("-inf")


def test_predict_guards_against_singular_matrix() -> None:
    """Explicitly singular matrix — LinAlgError returns -inf, argmax skips.

    2026-07-14 design review: pin flipped from 0.5 → -inf. See
    ``test_predict_handles_inf_in_context_gracefully`` docstring for
    the full rationale — LinUCBBandit.select does pure argmax with
    no Thompson fallback, so a broken arm scoring 0.5 could beat a
    healthy arm scoring lower. -inf guarantees argmax skip.
    """
    arm = LinUCBArm(d=6)
    # All-zeros matrix is perfectly singular
    arm.A = np.zeros((6, 6))
    score = arm.predict(np.array([1.0, 0, 0, 0, 0, 0]))
    assert score == float("-inf")
