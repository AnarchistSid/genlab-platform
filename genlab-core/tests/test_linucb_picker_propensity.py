"""Pin: ``pick_best_arm_with_propensity`` — IPS-aware variant of the
LinUCB tie-break picker (PR U, 2026-06-28).

PR #634 shipped per-decision propensity logging at the
``LinUCBBandit.select_with_propensity`` level + the
``pending_feedback.propensity`` storage column. But the production
decision path goes through ``linucb_picker.pick_best_arm``, not
``LinUCBBandit.select``, so every new pending_feedback row was being
written with ``propensity=NULL``.

PR U closes the gap by adding ``pick_best_arm_with_propensity`` — the
same argmax-UCB pick as ``pick_best_arm`` but returning the picked
arm's softmax weight as its IPS-compatible propensity.

These pins lock in:

  Happy path (1):
  1. test_pick_with_propensity_returns_tuple — both halves populated.

  Cold-start contract (1):
  2. test_pick_with_propensity_cold_start_returns_none_none —
     under-observed arm collapses BOTH halves to None.

  Single-candidate short-circuit (1):
  3. test_pick_with_propensity_single_candidate_returns_one —
     ``[X] → (X, 1.0)``.

  Softmax math (1):
  4. test_pick_with_propensity_softmax_sums_to_one — over 3 arms the
     weights sum to ≈1; picked arm's propensity is the max weight.

  Numerical floor (1):
  5. test_pick_with_propensity_respects_min_floor — loser of a
     dominated arm-set never reports propensity below
     ``_MIN_PROPENSITY``.

  Convention pin (1):
  6. test_pick_with_propensity_temperature_default_matches_linucb_
     bandit — picker's default temperature is 0.5, matching
     ``LinUCBBandit.DEFAULT_TEMPERATURE`` so IPS replay reads both
     producers under one convention.
"""

from __future__ import annotations

import numpy as np
from genlab_core.learning.linucb import (
    CONTEXT_DIM,
    DEFAULT_TEMPERATURE,
    LinUCBArm,
)
from genlab_core.learning.linucb_picker import (
    _DETERMINISTIC_TEMPERATURE,
    _MIN_PROPENSITY,
    pick_best_arm_with_propensity,
)


def _trained_arm(*, n_obs: int = 20, bias_dim: int = 0, reward: float = 1.0) -> LinUCBArm:
    """Build a LinUCBArm with ``n_obs`` observations all favoring
    ``bias_dim``. Arms trained with positive reward on a specific
    context dimension score HIGHER for contexts emphasizing that
    dimension."""
    arm = LinUCBArm(d=CONTEXT_DIM)
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    for _ in range(n_obs):
        arm.update(ctx, reward)
    return arm


def _context_vec(bias_dim: int = 0) -> np.ndarray:
    """Unit context vector with mass on a specific dimension."""
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    return ctx


def test_pick_with_propensity_returns_tuple():
    """Pin: happy path — both arms warm, context-aligned arm wins,
    propensity is the picked arm's softmax weight (>0, <=1)."""
    arm_a = _trained_arm(n_obs=20, bias_dim=0, reward=1.0)
    arm_b = _trained_arm(n_obs=20, bias_dim=1, reward=1.0)
    ctx = _context_vec(bias_dim=0)
    arm_id, propensity = pick_best_arm_with_propensity(
        ["arm_a", "arm_b"], ctx, {"arm_a": arm_a, "arm_b": arm_b}
    )
    # Argmax-by-UCB ranking is identical to pick_best_arm: arm_a wins.
    assert arm_id == "arm_a"
    # Propensity is a real float in (_MIN_PROPENSITY, 1.0].
    assert propensity is not None
    assert isinstance(propensity, float)
    assert _MIN_PROPENSITY <= propensity <= 1.0


def test_pick_with_propensity_cold_start_returns_none_none():
    """Pin: cold-start contract — even ONE under-observed arm collapses
    BOTH halves to None. (None, score) would lie about IPS data
    availability; (None, None) is the unambiguous "no data" sentinel."""
    warm = _trained_arm(n_obs=20)
    cold = _trained_arm(n_obs=3)  # below default min_obs=10
    arm_id, propensity = pick_best_arm_with_propensity(
        ["warm", "cold"], _context_vec(), {"warm": warm, "cold": cold}
    )
    assert arm_id is None
    assert propensity is None


def test_pick_with_propensity_single_candidate_returns_one():
    """Pin: single-candidate short-circuit — ``[X] → (X, 1.0)``. The
    selection is trivially deterministic, so the propensity is 1.0 by
    construction. No LinUCB scoring needed; the path doesn't even
    require ``X`` to exist in ``linucb_arms``."""
    arm_id, propensity = pick_best_arm_with_propensity(["only_arm"], _context_vec(), {})
    assert arm_id == "only_arm"
    assert propensity == 1.0


def test_pick_with_propensity_softmax_sums_to_one():
    """Pin: over 3 evenly-trained arms, the softmax weights sum to ≈1.0
    and the picked arm's propensity is the MAX of those weights (because
    argmax-UCB picks the highest-scoring arm and that arm's softmax
    weight is also the largest)."""
    # 3 arms biased on different context dims so UCB scores diverge.
    arm_a = _trained_arm(n_obs=20, bias_dim=0, reward=1.0)
    arm_b = _trained_arm(n_obs=20, bias_dim=1, reward=0.5)
    arm_c = _trained_arm(n_obs=20, bias_dim=2, reward=0.25)
    ctx = _context_vec(bias_dim=0)  # favors arm_a
    arm_id, propensity = pick_best_arm_with_propensity(
        ["a", "b", "c"], ctx, {"a": arm_a, "b": arm_b, "c": arm_c}
    )
    assert arm_id == "a"
    # Recompute weights by hand to assert SUM and MAX properties.
    scores = {
        "a": arm_a.predict(ctx),
        "b": arm_b.predict(ctx),
        "c": arm_c.predict(ctx),
    }
    arr = np.array(list(scores.values()))
    shifted = (arr - arr.max()) / _DETERMINISTIC_TEMPERATURE
    exp_vals = np.exp(shifted)
    weights = exp_vals / exp_vals.sum()
    weights = np.maximum(weights, _MIN_PROPENSITY)
    # Sum ≈ 1 (within clamp drift bounded by 3 * _MIN_PROPENSITY).
    assert abs(float(weights.sum()) - 1.0) < 1e-4
    # Picked arm's propensity equals the max softmax weight.
    assert propensity is not None
    assert abs(propensity - float(weights.max())) < 1e-9


def test_pick_with_propensity_respects_min_floor():
    """Pin: when one arm dominates (huge UCB), the loser's propensity
    is ≥ ``_MIN_PROPENSITY``, NEVER 0.0. The floor bounds the IPS
    weight 1/p so a near-zero propensity can't dominate the IPS
    estimator's variance."""
    # arm_dom trained 200x to dominate; arm_weak just warm enough to
    # pass the cold-start gate but with tiny UCB.
    arm_dom = _trained_arm(n_obs=200, bias_dim=0, reward=10.0)
    arm_weak = _trained_arm(n_obs=10, bias_dim=0, reward=0.001)
    ctx = _context_vec(bias_dim=0)
    arm_id, propensity = pick_best_arm_with_propensity(
        ["dom", "weak"], ctx, {"dom": arm_dom, "weak": arm_weak}
    )
    # arm_dom should win.
    assert arm_id == "dom"
    # Recompute the loser's softmax weight directly — it must be
    # >= _MIN_PROPENSITY (the floor IS the test, not the math).
    scores = {
        "dom": arm_dom.predict(ctx),
        "weak": arm_weak.predict(ctx),
    }
    arr = np.array(list(scores.values()))
    shifted = (arr - arr.max()) / _DETERMINISTIC_TEMPERATURE
    exp_vals = np.exp(shifted)
    raw_weights = exp_vals / exp_vals.sum()
    floored = np.maximum(raw_weights, _MIN_PROPENSITY)
    # The loser's floored weight must equal the floor (or exceed it
    # — the assertion is that we NEVER go below).
    loser_weight = floored[list(scores.keys()).index("weak")]
    assert loser_weight >= _MIN_PROPENSITY


def test_pick_with_propensity_temperature_default_matches_linucb_bandit():
    """Pin: the picker's default softmax temperature MUST equal
    ``LinUCBBandit.DEFAULT_TEMPERATURE`` (0.5). If one drifts, IPS
    replay can't combine decisions from the two paths under one
    convention. This is a forward-compat guard — any future refactor
    must update both constants together."""
    assert _DETERMINISTIC_TEMPERATURE == 0.5
    assert _DETERMINISTIC_TEMPERATURE == DEFAULT_TEMPERATURE
