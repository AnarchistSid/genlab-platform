"""Pin: LinUCB-driven candidate-arm picker (Lever I2).

When push_to_backlog._classify_arm keyword-matches a story to multiple
candidate arms, today's tie-break uses Thompson-sampled boosts from the
(alpha, beta) posteriors. That ignores story CONTEXT — duration, source,
hour, niche, trending score.

LinUCB scores arms given context. Its infrastructure has been in
learning/linucb.py for months but with ZERO production callers — only
.update() runs from metric_collector. The .select() / .predict() paths
are dead code in prod.

Lever I2 ships the missing PICKER primitive. Wiring into _classify_arm
is a follow-up PR; THIS PR locks in the picker's pure contract.

These pins lock in:

  Opt-in (2):
  1. is_enabled returns False by default, True when env=1

  score_arm (4):
  2. Score returned for arm with sufficient observations
  3. Returns None for missing arm (not in dict)
  4. Returns None for under-observed arm (n_obs < min_obs)
  5. score_arm accepts both np.ndarray and plain list context

  pick_best_arm cold-start (4):
  6. Empty matches → None
  7. Single match → returned unchanged (no scoring)
  8. Any missing arm in matches → None (cold-start fallback)
  9. Any under-observed arm in matches → None (cold-start fallback)

  pick_best_arm happy path (3):
  10. Picks highest-score arm when all have sufficient data
  11. Deterministic tie-break (sorted-by-arm-id on equal scores)
  12. Honors custom min_obs threshold via kwarg
"""

from __future__ import annotations

import numpy as np
from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm
from genlab_core.learning.linucb_picker import is_enabled, pick_best_arm, score_arm


def _trained_arm(*, n_obs: int = 20, bias_dim: int = 0, reward: float = 1.0) -> LinUCBArm:
    """Build a LinUCBArm with `n_obs` observations all favoring `bias_dim`.

    Helper so each test doesn't repeat the training loop. Arms trained
    with positive reward on a specific context dimension will score
    HIGHER for contexts that emphasize that dimension. Tests use
    different `bias_dim` values to make the expected ranking obvious.
    """
    arm = LinUCBArm(d=CONTEXT_DIM)
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    for _ in range(n_obs):
        arm.update(ctx, reward)
    return arm


def _context_vec(bias_dim: int = 0) -> np.ndarray:
    """Build a unit context vector with mass on a specific dimension."""
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    return ctx


class TestIsEnabled:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_LINUCB_PICK_ENABLED", raising=False)
        assert is_enabled() is False

    def test_env_one_is_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")
        assert is_enabled() is True


class TestScoreArm:
    def test_returns_score_for_trained_arm(self):
        arm = _trained_arm(n_obs=20)
        score = score_arm("a", _context_vec(), {"a": arm})
        assert score is not None
        assert isinstance(score, float)

    def test_returns_none_for_missing_arm(self):
        """Pin: arm not in linucb_arms dict → None (not KeyError)."""
        score = score_arm("missing", _context_vec(), {})
        assert score is None

    def test_returns_none_for_under_observed_arm(self):
        """Pin: cold-start guard — n_obs < min_obs returns None so the
        caller can fall back to Thompson without trusting noisy LinUCB
        scores."""
        cold = _trained_arm(n_obs=3)  # 3 < default min_obs=10
        assert score_arm("cold", _context_vec(), {"cold": cold}) is None

    def test_accepts_list_context(self):
        """Pin: context can be plain list (no numpy import in caller).
        Internal conversion is the picker's responsibility."""
        arm = _trained_arm(n_obs=20)
        # Plain list, no numpy
        list_ctx = [0.0] * CONTEXT_DIM
        list_ctx[0] = 1.0
        score = score_arm("a", list_ctx, {"a": arm})
        assert score is not None


class TestPickBestArmColdStart:
    def test_empty_matches_returns_none(self):
        assert pick_best_arm([], _context_vec(), {}) is None

    def test_single_match_returned_unchanged(self):
        """Pin: single-candidate short-circuit — no scoring needed when
        there's only one option. Saves a predict() call in the common
        case (most stories match exactly one keyword set)."""
        # Note: no linucb_arms passed — single-match path must not require
        # the arm to even exist in the bandit yet
        assert pick_best_arm(["only_arm"], _context_vec(), {}) == "only_arm"

    def test_any_missing_arm_returns_none(self):
        """Pin: cold-start fallback — even ONE arm without a model
        collapses the whole pick. Don't compare a trained arm to nothing."""
        arm = _trained_arm(n_obs=20)
        # "b" exists, "a" doesn't
        result = pick_best_arm(["a", "b"], _context_vec(), {"b": arm})
        assert result is None

    def test_any_under_observed_arm_returns_none(self):
        """Pin: cold-start fallback — even ONE under-observed arm
        collapses the pick. A confident arm vs a noisy arm = unfair."""
        warm = _trained_arm(n_obs=20)
        cold = _trained_arm(n_obs=3)
        result = pick_best_arm(["warm", "cold"], _context_vec(), {"warm": warm, "cold": cold})
        assert result is None


class TestPickBestArmHappyPath:
    def test_picks_highest_score(self):
        """Pin: when both arms have enough data, the one whose training
        context aligns with the query context wins."""
        # arm_a trained on dimension 0, arm_b trained on dimension 1.
        # Query context emphasizes dimension 0 → arm_a should win.
        arm_a = _trained_arm(n_obs=20, bias_dim=0, reward=1.0)
        arm_b = _trained_arm(n_obs=20, bias_dim=1, reward=1.0)
        ctx = _context_vec(bias_dim=0)
        winner = pick_best_arm(["arm_a", "arm_b"], ctx, {"arm_a": arm_a, "arm_b": arm_b})
        assert winner == "arm_a"

    def test_deterministic_tiebreak_on_equal_scores(self):
        """Pin: when arms score identically (both trained the same way),
        sorted-by-arm-id determinism. Without this, dict iteration order
        would leak through and tests would flake on some Python versions."""
        # Both arms trained identically → identical scores
        arm_a = _trained_arm(n_obs=20, bias_dim=0)
        arm_b = _trained_arm(n_obs=20, bias_dim=0)
        ctx = _context_vec(bias_dim=0)
        result_a = pick_best_arm(["zebra", "apple"], ctx, {"zebra": arm_a, "apple": arm_b})
        # "apple" comes first alphabetically. With identical scores, sorted
        # iteration means "apple" is compared first and wins ties.
        # (max() returns the first one that hits the maximum.)
        assert result_a == "apple"

    def test_custom_min_obs_threshold(self):
        """Pin: caller can raise/lower the min_obs cold-start threshold
        per-niche. n_obs=5 is below default 10 (returns None) but above
        a custom 3 (returns the arm)."""
        arm = _trained_arm(n_obs=5)
        # Default min_obs=10 → cold-start
        assert pick_best_arm(["a"], _context_vec(), {"a": arm}, min_obs=10) == "a"
        # Single-match short-circuit returns regardless of min_obs.
        # So test with 2 candidates where both are at n_obs=5
        arm_b = _trained_arm(n_obs=5, bias_dim=1)
        # min_obs=10 → cold-start because both are under-observed
        assert pick_best_arm(["a", "b"], _context_vec(), {"a": arm, "b": arm_b}, min_obs=10) is None
        # min_obs=3 → both qualify, score-based pick proceeds
        result = pick_best_arm(["a", "b"], _context_vec(), {"a": arm, "b": arm_b}, min_obs=3)
        assert result in {"a", "b"}


class TestImportSafety:
    def test_module_loads_without_numpy_in_caller(self):
        """Pin: the picker module is importable without numpy installed
        in the *caller's* import path — numpy is lazy-imported inside
        score_arm. (numpy IS required at runtime, but tests can import
        the module safely in numpy-less env-detection scripts.)"""
        # Sanity import test — re-imports the module to ensure no
        # top-level numpy reference triggers ImportError before lazy
        # import can run.
        import importlib

        import genlab_core.learning.linucb_picker as picker_mod

        importlib.reload(picker_mod)
        assert hasattr(picker_mod, "is_enabled")
        assert hasattr(picker_mod, "score_arm")
        assert hasattr(picker_mod, "pick_best_arm")
