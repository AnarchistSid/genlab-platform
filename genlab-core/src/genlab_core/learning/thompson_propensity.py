"""Thompson-selection propensity estimator — Intelligence #8b (2026-07-18).

Computes ``P(chosen_arm | Beta posteriors)`` for the Thompson-tie-break
path in ``push_to_backlog._classify_arm_with_propensity``. Enables
meaningful IPS/DR estimation on the ~96% of decisions that bypass
LinUCB entirely (Thompson sampling on style / content_type / hook_style
arms, keyword-match fallbacks, etc).

## Design

Thompson sampling picks the arm with the highest sample drawn from
that arm's Beta(α, β) posterior. The MARGINAL propensity of a specific
arm being chosen is:

    P(arm_i wins) = ∫ ∏_{j≠i} Φ_j(x) φ_i(x) dx

where φ_j is the Beta(α_j, β_j) pdf and Φ_j its cdf. This integral has
no closed form for 3+ Beta distributions, so we estimate via Monte
Carlo:

    1. Draw one Beta sample per arm from its (α, β)
    2. Record which arm has the highest sample
    3. Repeat K times
    4. Report ``wins_for_chosen / K`` as the propensity

## Why this was reported "impossible"

The pre-existing comment at push_to_backlog.py:1367-1370 said Thompson
has no IPS-compatible propensity because "its sampling distribution
changes shape on every call as the posteriors update". That's true for
the point-in-time propensity (which is always 1.0 given the draws that
already happened), but it conflates it with the MARGINAL propensity
(P(arm wins) integrated over Thompson draws) which IS what IPS wants.

This module implements the marginal via MC estimation. Trade-off vs
the pre-existing design:

- Compute cost: K=200 Beta draws per pick × N arms = 200-2000 random
  draws. ~1ms at pipeline scale, negligible.
- Approximation error: SE = √(p(1-p)/K) ≈ 0.035 at p=0.5, K=200. Fine
  for IPS reweighting.
- Truncation floor: same MIN_PROPENSITY as linucb_picker to bound
  1/p importance weights.

## Rollout

Behind ``GENLAB_THOMPSON_PROPENSITY_ENABLED_{NICHE}=1`` per-niche flag
(mirrors Intelligence #8 stochastic LinUCB pattern). Global fallback
via ``GENLAB_THOMPSON_PROPENSITY_ENABLED=1``. Default off preserves
pre-#8b behavior (Thompson picks report ``propensity=None`` and get
excluded from IPS replay).

Canary: enable on ai_creators first (fewest matches per decision →
smallest MC cost per run). Watch pending_feedback for
``propensity IS NOT NULL AND propensity < 1`` rows before enabling
across all niches.
"""

from __future__ import annotations

import logging
import os
import random

logger = logging.getLogger(__name__)

# Monte Carlo trial count. 200 gives SE ≈ 0.035 at p=0.5, which is well
# below the precision IPS reweighting can actually use.
_DEFAULT_MC_TRIALS = 200

# Truncation floor — matches linucb_picker._MIN_PROPENSITY so 1/p
# importance weights are bounded consistently across both propensity
# sources.
_MIN_PROPENSITY = 1e-3


def compute_thompson_propensity(
    chosen_arm_id: str,
    arm_posteriors: dict[str, tuple[float, float]],
    *,
    k: int = _DEFAULT_MC_TRIALS,
    seed: int | None = None,
) -> float:
    """Estimate ``P(chosen_arm_id wins Thompson pick)`` via Monte Carlo.

    Args:
        chosen_arm_id: The arm that was actually picked (via argmax of
            Beta draws in the pipeline's Thompson path).
        arm_posteriors: ``{arm_id: (alpha, beta)}`` for all arms in
            the match set. Alpha/beta values below 1 are clamped up to
            1 (matches the pipeline's Thompson-boost fallback).
        k: Number of Monte Carlo trials. Larger = tighter estimate.
        seed: Optional PRNG seed for reproducibility (tests use this).

    Returns:
        Float in ``[MIN_PROPENSITY, 1.0]``. Truncated to MIN_PROPENSITY
        so 1/p importance weights don't blow up on a rare-arm pick.

    Fail-safe: returns ``1.0`` when arm_posteriors is empty or the
    chosen arm isn't in the posteriors dict (degenerate cases where
    Thompson didn't actually run — treat like a deterministic pick).
    """
    if not arm_posteriors:
        return 1.0
    if chosen_arm_id not in arm_posteriors:
        return 1.0
    if len(arm_posteriors) == 1:
        # Single-arm case is degenerate — no distribution to sample from
        return 1.0

    # Clamp priors below 1 up to 1 (matches _get_bandit_arm_boost pattern)
    clamped = {arm_id: (max(1.0, a), max(1.0, b)) for arm_id, (a, b) in arm_posteriors.items()}

    rng = random.Random(seed) if seed is not None else random

    wins = 0
    arm_ids = list(clamped.keys())
    for _ in range(k):
        best_arm = None
        best_sample = -1.0
        for arm_id in arm_ids:
            a, b = clamped[arm_id]
            try:
                sample = rng.betavariate(a, b)
            except (ValueError, OverflowError):
                sample = 0.5
            if sample > best_sample:
                best_sample = sample
                best_arm = arm_id
        if best_arm == chosen_arm_id:
            wins += 1

    return max(_MIN_PROPENSITY, wins / k)


def is_thompson_propensity_enabled(niche_id: str) -> bool:
    """Return True iff Thompson propensity capture is enabled for niche_id.

    Checks per-niche env var first
    (``GENLAB_THOMPSON_PROPENSITY_ENABLED_{NICHE_UPPER}=1``), falls
    back to global (``GENLAB_THOMPSON_PROPENSITY_ENABLED=1``). Default
    off — preserves pre-#8b propensity=None behavior for Thompson picks.

    Mirrors the per-niche canary pattern from Intelligence #8's stochastic
    LinUCB flag.
    """
    if not niche_id:
        return False

    per_niche = os.environ.get(f"GENLAB_THOMPSON_PROPENSITY_ENABLED_{niche_id.upper()}")
    if per_niche is not None:
        return per_niche.strip() == "1"
    return os.environ.get("GENLAB_THOMPSON_PROPENSITY_ENABLED", "0").strip() == "1"


__all__ = [
    "compute_thompson_propensity",
    "is_thompson_propensity_enabled",
]
