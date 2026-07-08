"""Pin: LinUCB state validation on load (L12, 2026-07-08 audit).

Corrupt persisted state (NaN/Inf entries, singular / near-singular A)
would silently produce garbage predictions or raise inside every
predict call. ``LinUCBArm.from_dict`` now validates on restore and
hard-resets to a fresh arm on any corruption class.

Pins:
  1. Healthy state round-trips byte-identically
  2. NaN in A_matrix → reset to identity + zero + n_obs=0
  3. Inf in A_matrix → reset
  4. NaN in b_vector → reset
  5. Inf in b_vector → reset
  6. Non-square A → reset (defense-in-depth)
  7. Singular A (all zeros) → reset
  8. Near-singular A (condition number above limit) → reset
  9. Alpha is preserved across reset (per-arm tuning parameter)
 10. Reset arm can be updated + predicted afterwards (functional)
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm


class TestRoundTrip:
    def test_healthy_state_round_trips(self):
        """Sanity: a healthy arm serialises and restores byte-identical.
        Locks in that L12 doesn't destroy legitimate state."""
        arm = LinUCBArm(d=CONTEXT_DIM, alpha=1.5)
        # Simulate a few updates so A + b diverge from init.
        for _ in range(5):
            x = np.random.RandomState(42).randn(CONTEXT_DIM)
            arm.update(x, reward=0.7)

        restored = LinUCBArm.from_dict(arm.to_dict())

        assert restored.A.shape == arm.A.shape
        assert np.allclose(restored.A, arm.A)
        assert np.allclose(restored.b, arm.b)
        assert restored.n_obs == arm.n_obs
        assert restored.alpha == pytest.approx(1.5)


class TestNaNInfGuards:
    """Any NaN/Inf must trigger a full reset — a single corrupt entry
    propagates through matrix inversion."""

    def test_nan_in_A_triggers_reset(self, caplog):
        payload = {
            "A_matrix": np.eye(CONTEXT_DIM).tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 1.0,
            "n_obs": 100,
        }
        # Poke one NaN.
        payload["A_matrix"][3][7] = float("nan")

        with caplog.at_level(logging.WARNING):
            restored = LinUCBArm.from_dict(payload)

        # Reset: identity A, zero b, n_obs=0 (cold-start behavior).
        assert np.allclose(restored.A, np.eye(CONTEXT_DIM))
        assert np.allclose(restored.b, np.zeros(CONTEXT_DIM))
        assert restored.n_obs == 0
        assert "NaN/Inf" in caplog.text or "resetting" in caplog.text.lower()

    def test_inf_in_A_triggers_reset(self):
        payload = {
            "A_matrix": np.eye(CONTEXT_DIM).tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 1.0,
            "n_obs": 100,
        }
        payload["A_matrix"][0][0] = float("inf")

        restored = LinUCBArm.from_dict(payload)
        assert np.allclose(restored.A, np.eye(CONTEXT_DIM))
        assert restored.n_obs == 0

    def test_nan_in_b_triggers_reset(self, caplog):
        payload = {
            "A_matrix": np.eye(CONTEXT_DIM).tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 1.0,
            "n_obs": 100,
        }
        payload["b_vector"][5] = float("nan")

        with caplog.at_level(logging.WARNING):
            restored = LinUCBArm.from_dict(payload)

        assert np.allclose(restored.b, np.zeros(CONTEXT_DIM))
        assert restored.n_obs == 0

    def test_inf_in_b_triggers_reset(self):
        payload = {
            "A_matrix": np.eye(CONTEXT_DIM).tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 1.0,
            "n_obs": 100,
        }
        payload["b_vector"][0] = float("-inf")
        restored = LinUCBArm.from_dict(payload)
        assert np.isfinite(restored.b).all()


class TestSingularityGuards:
    """Singular or near-singular A means inversion is unreliable —
    predict() might succeed silently but return garbage. Reset."""

    def test_all_zero_A_triggers_reset(self, caplog):
        """A matrix of all zeros has infinite condition number and
        no inverse. Must reset."""
        payload = {
            "A_matrix": np.zeros((CONTEXT_DIM, CONTEXT_DIM)).tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 1.0,
            "n_obs": 50,
        }
        with caplog.at_level(logging.WARNING):
            restored = LinUCBArm.from_dict(payload)

        # After reset, A should be a proper identity (invertible).
        assert np.allclose(restored.A, np.eye(CONTEXT_DIM))
        assert restored.n_obs == 0
        assert (
            "singular" in caplog.text.lower()
            or "resetting" in caplog.text.lower()
        )

    def test_near_singular_A_triggers_reset(self):
        """Condition number above _CONDITION_NUMBER_LIMIT means
        inversion is unstable. Reset."""
        # Build a matrix with two nearly-identical rows → very high
        # condition number without hitting exactly-singular.
        A = np.eye(CONTEXT_DIM)
        # Make row 0 nearly equal to row 1 → condition number spikes.
        A[0] = A[1] + 1.0e-15
        payload = {
            "A_matrix": A.tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 1.0,
            "n_obs": 100,
        }
        restored = LinUCBArm.from_dict(payload)
        # After reset, A is a proper identity.
        assert np.allclose(restored.A, np.eye(CONTEXT_DIM))

    def test_well_conditioned_A_survives(self):
        """A healthy identity + small updates has excellent condition
        number and MUST NOT be reset. Guards against false-positive
        resets on legitimate posteriors."""
        arm = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)
        # Accumulate 20 well-scaled updates.
        rng = np.random.RandomState(0)
        for _ in range(20):
            x = rng.randn(CONTEXT_DIM)
            arm.update(x, reward=rng.uniform(0.0, 1.0))

        restored = LinUCBArm.from_dict(arm.to_dict())
        # Should preserve n_obs, not reset.
        assert restored.n_obs == 20


class TestShapeGuards:
    def test_non_square_A_triggers_reset(self):
        """A non-square A matrix is nonsensical — reset defensively."""
        payload = {
            # 5x7 → not a valid covariance-like matrix.
            "A_matrix": np.zeros((5, 7)).tolist(),
            "b_vector": np.zeros(5).tolist(),
            "alpha": 1.0,
            "n_obs": 10,
        }
        restored = LinUCBArm.from_dict(payload)
        # Reset lands at CONTEXT_DIM.
        assert restored.A.shape == (CONTEXT_DIM, CONTEXT_DIM)
        assert restored.n_obs == 0


class TestResetPreservesAlpha:
    def test_alpha_survives_reset(self):
        """Alpha is a per-arm tuning parameter — a NaN in A shouldn't
        forget the operator's exploration setting."""
        payload = {
            "A_matrix": np.eye(CONTEXT_DIM).tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 2.5,  # non-default
            "n_obs": 100,
        }
        payload["A_matrix"][0][0] = float("nan")
        restored = LinUCBArm.from_dict(payload)
        assert restored.alpha == pytest.approx(2.5)


class TestResetArmIsFunctional:
    def test_reset_arm_can_update_and_predict(self):
        """The reset arm must be functionally identical to a fresh
        arm — otherwise a corrupt state would poison the arm forever."""
        payload = {
            "A_matrix": np.eye(CONTEXT_DIM).tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 1.0,
            "n_obs": 42,
        }
        payload["A_matrix"][5][5] = float("nan")

        restored = LinUCBArm.from_dict(payload)

        x = np.ones(CONTEXT_DIM)
        # predict should return a finite float.
        score = restored.predict(x)
        assert np.isfinite(score)
        # update must not raise.
        restored.update(x, reward=1.0)
        assert restored.n_obs == 1  # 0 → 1 after the update

    def test_reset_arm_matches_fresh_arm_signature(self):
        """Reset arm and fresh arm should behave identically on any
        deterministic operation. Locks in the semantic equivalence."""
        payload = {
            "A_matrix": np.eye(CONTEXT_DIM).tolist(),
            "b_vector": np.zeros(CONTEXT_DIM).tolist(),
            "alpha": 1.0,
            "n_obs": 100,
        }
        payload["A_matrix"][0][0] = float("nan")
        restored = LinUCBArm.from_dict(payload)

        fresh = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)

        x = np.arange(CONTEXT_DIM, dtype=np.float64) / CONTEXT_DIM
        assert restored.predict(x) == pytest.approx(fresh.predict(x))
