"""Pin: per-platform variant aggregation in the LinUCB picker (PR Z).

When ``GENLAB_PER_PLATFORM_BANDIT_ENABLED=1``,
:func:`score_arm_with_platform_split` looks up the base arm AND each
per-platform variant (e.g. ``arm__instagram``, ``arm__youtube``) and
aggregates the UCB scores via ``max``. The max-aggregation choice is
documented in the helper's docstring — biases toward exploration when
any platform shows strong signal. ``pick_best_arm`` +
``pick_best_arm_with_propensity`` both call the helper transparently
so all callers benefit from per-platform signal once the flag is set.

These pins lock in:

  Aggregation (1):
  1. Max-aggregation: base=0.4, IG=0.7, YT=0.5 → 0.7

  Cold-start fallback (1):
  2. Variants under-observed → falls back to BASE score (not None,
     because the base arm still has signal)

  Flag-off equivalence (1):
  3. Flag OFF → identical to ``score_arm`` (variants ignored entirely)
"""

from __future__ import annotations

import numpy as np
from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm
from genlab_core.learning.linucb_picker import score_arm_with_platform_split


def _trained_arm(
    *,
    n_obs: int = 20,
    bias_dim: int = 0,
    reward: float = 1.0,
) -> LinUCBArm:
    """Build a LinUCBArm with `n_obs` observations all favoring `bias_dim`.

    Mirrors the helper in test_linucb_picker.py — kept inline here so
    this test file is independently runnable.
    """
    arm = LinUCBArm(d=CONTEXT_DIM)
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    for _ in range(n_obs):
        arm.update(ctx, reward)
    return arm


def _context_vec(bias_dim: int = 0) -> np.ndarray:
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    return ctx


class TestMaxAggregation:
    def test_score_arm_with_platform_split_max_aggregation(self, monkeypatch):
        """Pin: max-aggregation rule.

        Three arms in the dict: base + 2 per-platform variants. Train
        each to a different reward magnitude so their UCB scores are
        distinguishable. The helper must return the MAXIMUM, not the
        mean or the first hit.
        """
        monkeypatch.setenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", "1")

        # Train each arm to a different reward magnitude. Lower reward
        # → lower UCB score. We can't pin exact float values without
        # the LinUCB internals, so we use reward=0.3/0.7/0.5 and check
        # the helper picks the YT (reward 0.7) variant's score.
        base = _trained_arm(n_obs=20, reward=0.3)
        ig = _trained_arm(n_obs=20, reward=0.7)
        yt = _trained_arm(n_obs=20, reward=0.5)

        linucb_arms = {
            "gameplay_clip": base,
            "gameplay_clip__instagram": ig,
            "gameplay_clip__youtube": yt,
        }

        result = score_arm_with_platform_split("gameplay_clip", _context_vec(), linucb_arms)
        assert result is not None, "all arms warm → must return a score, not None"

        # The result must equal the MAX of the three individual scores.
        # Compute each and verify.
        from genlab_core.learning.linucb_picker import score_arm

        base_s = score_arm("gameplay_clip", _context_vec(), linucb_arms)
        ig_s = score_arm("gameplay_clip__instagram", _context_vec(), linucb_arms)
        yt_s = score_arm("gameplay_clip__youtube", _context_vec(), linucb_arms)

        assert base_s is not None and ig_s is not None and yt_s is not None
        expected = max(base_s, ig_s, yt_s)
        assert result == expected, (
            f"max-aggregation: got {result}, expected max({base_s}, {ig_s}, {yt_s}) = {expected}"
        )


class TestColdStartFallback:
    def test_score_arm_with_platform_split_falls_back_to_base_when_variants_cold_start(
        self, monkeypatch
    ):
        """Pin: variants under-observed → fall back to BASE score.

        The base arm IS the pre-PR-Z signal and is always at least as
        well-observed as any per-platform variant (because reward
        attribution was hitting the base arm exclusively until the
        flag flipped). Returning None when only variants are cold
        would needlessly drop into the Thompson fallback right when
        the base score is exactly what callers want.
        """
        monkeypatch.setenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", "1")

        warm_base = _trained_arm(n_obs=20)  # base is warm
        cold_ig = _trained_arm(n_obs=3)  # IG under-observed (< default min_obs=10)
        cold_yt = _trained_arm(n_obs=3)  # YT under-observed

        linucb_arms = {
            "gameplay_clip": warm_base,
            "gameplay_clip__instagram": cold_ig,
            "gameplay_clip__youtube": cold_yt,
        }

        result = score_arm_with_platform_split("gameplay_clip", _context_vec(), linucb_arms)
        # Base is warm → its score is the ONLY non-None signal → that's
        # what max() returns.
        from genlab_core.learning.linucb_picker import score_arm

        base_s = score_arm("gameplay_clip", _context_vec(), linucb_arms)
        assert result is not None, (
            "base arm is warm — must NOT return None even though variants are cold"
        )
        assert result == base_s, (
            f"variants cold + base warm → must return base score; got {result}, expected {base_s}"
        )


class TestFlagOffEquivalence:
    def test_score_arm_with_platform_split_flag_off_returns_base_only(self, monkeypatch):
        """Pin: flag OFF → identical to ``score_arm``.

        Per-platform variants in ``linucb_arms`` are IGNORED — only
        the base arm is consulted. Equivalence is verified by comparing
        ``score_arm_with_platform_split`` and ``score_arm`` outputs on
        the same inputs.
        """
        monkeypatch.delenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", raising=False)

        base = _trained_arm(n_obs=20, reward=0.3)
        ig = _trained_arm(n_obs=20, reward=0.99)  # would dominate if consulted
        yt = _trained_arm(n_obs=20, reward=0.99)

        linucb_arms = {
            "gameplay_clip": base,
            "gameplay_clip__instagram": ig,
            "gameplay_clip__youtube": yt,
        }

        from genlab_core.learning.linucb_picker import score_arm

        split_result = score_arm_with_platform_split("gameplay_clip", _context_vec(), linucb_arms)
        base_result = score_arm("gameplay_clip", _context_vec(), linucb_arms)

        assert split_result == base_result, (
            f"flag OFF must produce identical output to score_arm; "
            f"got split={split_result}, base={base_result}"
        )
