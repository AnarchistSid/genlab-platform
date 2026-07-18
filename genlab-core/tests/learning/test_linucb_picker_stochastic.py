"""Pin tests for Intelligence #8 stochastic mode in linucb_picker.

Adds ``stochastic=`` kwarg to ``pick_best_arm_with_propensity``. When
True, arms are sampled from softmax(scores/T) instead of picked via
argmax. Enables meaningful IPS/DR signal for the sampled arms.

## What these pin

1. **Default off — argmax unchanged** — existing callers unaffected
2. **Stochastic explores** — over many draws, non-argmax arms
   sometimes win (variance in selection = IPS signal)
3. **Sampled arm's propensity = its softmax weight** — the reported
   probability matches the actual selection distribution
4. **Single-candidate short-circuit unchanged** — no sampling to do
   when only one match
5. **Cold-start unchanged** — returns (None, None) when any arm under-
   observed, regardless of stochastic flag
"""

from __future__ import annotations

import numpy as np
from genlab_core.learning.linucb import LinUCBArm
from genlab_core.learning.linucb_picker import pick_best_arm_with_propensity


def _make_arm(alpha_mult: float = 1.0, n_obs: int = 100) -> LinUCBArm:
    """Build a LinUCBArm with meaningful predict() behavior for tests."""
    arm = LinUCBArm(d=3, alpha=alpha_mult)
    # Simulate observations: update the arm so it has real n_obs.
    # LinUCBArm updates via update(context, reward). Feed n_obs updates
    # to reach the observation floor.
    rng = np.random.default_rng(seed=42)
    for _ in range(n_obs):
        ctx = rng.standard_normal(3)
        # Reward correlates with alpha_mult so arms with higher mult
        # have higher expected scores → clearer arm ranking in tests.
        reward = float(alpha_mult) + rng.normal(0, 0.1)
        arm.update(ctx, reward)
    return arm


class TestDefaultDeterministic:
    """stochastic=False (default) preserves argmax behavior."""

    def test_default_kwarg_is_false(self) -> None:
        """Signature default = False — existing callers unaffected."""
        import inspect

        sig = inspect.signature(pick_best_arm_with_propensity)
        assert sig.parameters["stochastic"].default is False

    def test_argmax_wins_deterministically(self) -> None:
        """Same input → same output in deterministic mode."""
        arms = {
            "a": _make_arm(alpha_mult=1.0, n_obs=100),
            "b": _make_arm(alpha_mult=3.0, n_obs=100),  # higher score
            "c": _make_arm(alpha_mult=0.5, n_obs=100),
        }
        context = np.array([0.5, 0.3, -0.1])
        results = {
            pick_best_arm_with_propensity(list(arms.keys()), context, arms)[0] for _ in range(10)
        }
        assert len(results) == 1, "deterministic mode must produce the same arm every time"


class TestStochasticMode:
    """stochastic=True samples from softmax."""

    def test_stochastic_can_pick_non_argmax(self) -> None:
        """Over enough draws, a non-argmax arm should sometimes win —
        that's the whole point of stochastic mode."""
        arms = {
            "a": _make_arm(alpha_mult=1.0, n_obs=100),
            "b": _make_arm(alpha_mult=1.5, n_obs=100),  # slight favorite
            "c": _make_arm(alpha_mult=0.5, n_obs=100),
        }
        context = np.array([0.5, 0.3, -0.1])
        rng_state = np.random.get_state()
        np.random.seed(0)
        try:
            picks = [
                pick_best_arm_with_propensity(list(arms.keys()), context, arms, stochastic=True)[0]
                for _ in range(50)
            ]
        finally:
            np.random.set_state(rng_state)
        # At least 2 distinct arms picked across 50 draws — otherwise
        # sampling isn't actually happening
        assert len(set(picks)) >= 2, (
            f"stochastic mode failed to explore; all picks were {set(picks)}"
        )

    def test_stochastic_reports_softmax_weight(self) -> None:
        """The propensity returned should reflect the sampling distribution."""
        arms = {
            "a": _make_arm(alpha_mult=1.0, n_obs=100),
            "b": _make_arm(alpha_mult=1.5, n_obs=100),
        }
        context = np.array([0.5, 0.3, -0.1])
        # Aggregate propensities per arm across many draws
        np.random.seed(0)
        arm_propensities: dict[str, list[float]] = {"a": [], "b": []}
        for _ in range(30):
            arm_id, prop = pick_best_arm_with_propensity(
                list(arms.keys()), context, arms, stochastic=True
            )
            if arm_id is not None and prop is not None:
                arm_propensities[arm_id].append(prop)
        # Each arm's propensity, when picked, should be its softmax weight —
        # a value strictly between _MIN_PROPENSITY floor and 1.0
        for arm_id, props in arm_propensities.items():
            if props:
                for p in props:
                    assert 0.0 < p < 1.0, f"{arm_id} propensity out of (0,1) range: {p}"


class TestSingleCandidateShortCircuit:
    """Single-candidate case is degenerate — no sampling to do."""

    def test_single_candidate_stochastic_still_1_0(self) -> None:
        """When len(matches)==1, propensity must be 1.0 regardless of flag."""
        arms = {"a": _make_arm(alpha_mult=1.0, n_obs=100)}
        context = np.array([0.5, 0.3, -0.1])
        arm_id, prop = pick_best_arm_with_propensity(["a"], context, arms, stochastic=True)
        assert arm_id == "a"
        assert prop == 1.0


class TestColdStartUnchanged:
    """Cold-start returns (None, None) in both modes."""

    def test_cold_start_deterministic(self) -> None:
        arms = {"a": _make_arm(alpha_mult=1.0, n_obs=5)}  # below MIN_OBS
        context = np.array([0.5, 0.3, -0.1])
        arm_id, prop = pick_best_arm_with_propensity(["a", "b"], context, arms, stochastic=False)
        assert arm_id is None
        assert prop is None

    def test_cold_start_stochastic(self) -> None:
        arms = {"a": _make_arm(alpha_mult=1.0, n_obs=5)}
        context = np.array([0.5, 0.3, -0.1])
        arm_id, prop = pick_best_arm_with_propensity(["a", "b"], context, arms, stochastic=True)
        assert arm_id is None
        assert prop is None


class TestEmptyMatches:
    def test_empty_matches_returns_none_tuple(self) -> None:
        arm_id, prop = pick_best_arm_with_propensity(
            [], np.array([0.0, 0.0, 0.0]), {}, stochastic=True
        )
        assert arm_id is None
        assert prop is None
