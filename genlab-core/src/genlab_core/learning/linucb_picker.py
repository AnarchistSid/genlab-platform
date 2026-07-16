"""LinUCB-driven candidate-arm picker for tie-breaking (Lever I2).

When ``push_to_backlog._classify_arm`` keyword-matches a story to multiple
candidate arms (e.g. a clip that's both a "trailer" and a "cast
announcement"), today it breaks the tie via Thompson-sampled boosts from
the (alpha, beta) bandit posteriors. That works but ignores the CONTEXT
of the story — duration, source, hour-of-day, niche, trending score, etc.

LinUCB scores arms given context. The infrastructure has shipped for
months (``learning/linucb.py``) but with ZERO production callers — only
``.update()`` runs after reward arrives in ``metric_collector.py``. The
``.select()`` / ``.predict()`` paths are dead code in prod despite the
LinUCBArm state being persisted on every reward.

Lever I2 ships the missing PICKER:

  pick_best_arm(matches, context, linucb_arms) → str | None

  - Returns the highest-LinUCB-score arm from ``matches`` when every
    matched arm has a model with at least ``min_obs`` observations
  - Returns ``None`` (cold-start signal) when ANY matched arm has too
    few observations to make a confident LinUCB score
  - Caller falls back to today's Thompson-boost tie-break on None

This is the **picker primitive**. The wiring into
``push_to_backlog._classify_arm`` is a follow-up PR — keeps THIS PR's
scope small + makes the wiring change reviewable in isolation.

Once wired, LinUCB-driven selection composes with Lever I1
(``select_arm_with_random_control``) for clean counterfactual data:

  matches = keyword_classify(story)
  linucb_pick = pick_best_arm(matches, context, linucb_arms)
  bandit_pick = linucb_pick or thompson_tiebreak(matches, arm_boosts)
  selection = select_arm_with_random_control(bandit_pick, matches, ...)

Opt-in via ``GENLAB_LINUCB_PICK_ENABLED=1`` env. Default disabled = no
behavior change. Pattern matches Lever C / K / O / G1 / G2 / I1+I3 —
opt-in env flag, fail-open None, pure-logic split.

Run via:
    python -m genlab_core.learning.linucb_picker --enabled-check
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import numpy as np

    from genlab_core.learning.linucb import LinUCBArm

logger = logging.getLogger(__name__)


# Minimum observation count per arm before LinUCB scores are trusted.
# Below this, the UCB exploration bonus dominates and rankings are noise.
# 10 obs picked from the LinUCB literature's typical warm-up window
# (Li, Chu, Langford, Schapire 2010) — enough that A^{-1} is far from
# the identity and theta has meaningful direction.
_DEFAULT_MIN_OBS: Final[int] = 10

# Softmax temperature for the propensity-aware variant. Matches
# ``LinUCBBandit.DEFAULT_TEMPERATURE`` in ``learning/linucb.py`` (0.5)
# so the propensity values logged by ``pick_best_arm_with_propensity``
# are interpretable under the SAME convention as
# ``LinUCBBandit.select_with_propensity``. If one drifts, IPS replay
# can't recover meaningful counterfactual estimates across decisions
# made via the two paths. Pinned in
# ``tests/test_linucb_picker_propensity.py::test_pick_with_propensity_
# temperature_default_matches_linucb_bandit``.
_DETERMINISTIC_TEMPERATURE: Final[float] = 0.5

# Floor on per-arm propensity. IPS weights are 1/p, so a near-zero
# propensity becomes an outlier that dominates the estimator (the
# variance-explosion risk Move #8 flags). Matches ``_MIN_PROPENSITY``
# in ``learning/linucb.py`` — both clamps must use the same floor so
# downstream IPS replay treats decisions from both paths uniformly.
_MIN_PROPENSITY: Final[float] = 1e-6


def is_enabled() -> bool:
    """Single source of truth for the opt-in flag.

    Centralized so callers short-circuit cheaply
    (``if not is_enabled(): return None``) without duplicating the
    "0"/"1" check. Tests toggle via monkeypatch on this one function.
    """
    from genlab_core.settings import env_true

    return env_true("GENLAB_LINUCB_PICK_ENABLED")


def score_arm(
    arm_id: str,
    context: np.ndarray | list[float],
    linucb_arms: dict[str, LinUCBArm],
    *,
    min_obs: int = _DEFAULT_MIN_OBS,
) -> float | None:
    """Compute the UCB score for one arm given context.

    Returns None when:
    - ``arm_id`` has no LinUCB model in ``linucb_arms``
    - The arm's ``n_obs`` is below ``min_obs`` (cold-start guard)

    Pure function — accepts context as either np.ndarray or plain list
    (converted internally) so callers don't have to import numpy just to
    invoke the picker.
    """
    arm = linucb_arms.get(arm_id)
    if arm is None:
        return None
    if getattr(arm, "n_obs", 0) < min_obs:
        return None

    # Lazy numpy import — keeps the module loadable in environments
    # that don't have numpy (e.g. dashboard-only deployments).
    try:
        import numpy as np
    except ImportError:
        return None

    ctx = context if hasattr(context, "shape") else np.asarray(context, dtype=np.float64)
    try:
        return float(arm.predict(ctx))
    except Exception as exc:
        # predict() has internal guards (singular matrix, NaN, etc.)
        # that return 0.5 instead of raising, but defensive belt:
        # any unexpected exception returns None so caller falls back.
        logger.debug("[linucb_picker] score_arm(%s) failed: %s", arm_id, exc)
        return None


def score_arm_with_platform_split(
    arm_id: str,
    context: np.ndarray | list[float],
    linucb_arms: dict[str, LinUCBArm],
    *,
    min_obs: int = _DEFAULT_MIN_OBS,
) -> float | None:
    """Compute UCB score for ``arm_id`` aggregating across per-platform variants.

    PR Z (2026-06-29) per-platform bandit arm split:

    When ``GENLAB_PER_PLATFORM_BANDIT_ENABLED=1``, this function looks
    up the base arm ``arm_id`` AND each per-platform variant
    (e.g. ``arm_id__instagram``, ``arm_id__youtube``) and aggregates
    via ``max`` — biases toward exploration when even one platform
    shows strong signal for this content type. Documented choice:

      * **max** (what we ship): if IG learned ``gameplay_clip`` works
        well, the base score lifts even when YT hasn't picked up
        signal yet. Encourages cross-platform discovery.
      * **mean** (alternative): averages across platforms. More stable
        but slower to react when one platform has clear signal.

    Max selected because the asymmetric-decision-vs-reward split (see
    ``bandit_platform_split.py`` module docstring) means we pick ONE
    arm for a blueprint that publishes to MULTIPLE platforms — picking
    based on the best-performing platform's UCB is the closest
    behaviour to "publish where it'll work best."

    Cold-start contract:
    - If the BASE arm has a confident score, return it even when all
      platform variants are cold-started — the base arm IS the
      pre-PR-Z signal and is always at least as well-observed as any
      per-platform variant.
    - If the base arm itself is cold-started AND all variants are
      cold-started, return None (caller falls back to Thompson).
    - When a variant exists but is under-observed (n_obs < min_obs),
      :func:`score_arm` already returns None — we filter Nones before
      aggregating.

    When the flag is OFF, this function is equivalent to
    :func:`score_arm` (returns the base score directly). Caller can
    use it unconditionally; the env check is centralized.
    """
    # Lazy import keeps bandit_platform_split off the import-time path
    # for the dashboard-only deployments that don't ship the learning
    # module's full dependency tree.
    from genlab_core.learning.bandit_platform_split import (
        expand_arm_for_platforms,
    )
    from genlab_core.learning.bandit_platform_split import (
        is_enabled as _split_enabled,
    )

    if not _split_enabled():
        # Flag OFF — preserve existing single-arm behaviour identically.
        return score_arm(arm_id, context, linucb_arms, min_obs=min_obs)

    # Flag ON — score the base AND every per-platform variant.
    # ``expand_arm_for_platforms`` returns the base first, then the
    # platform-specific arms in declaration order.
    candidate_arms = expand_arm_for_platforms(arm_id)
    scores: list[float] = []
    for candidate in candidate_arms:
        s = score_arm(candidate, context, linucb_arms, min_obs=min_obs)
        if s is not None:
            scores.append(s)

    if not scores:
        # All variants cold-started OR missing. Signal cold-start so
        # caller's Thompson fallback handles the pick — same contract
        # as :func:`score_arm`.
        return None
    return max(scores)


def pick_best_arm(
    matches: list[str],
    context: np.ndarray | list[float],
    linucb_arms: dict[str, LinUCBArm],
    *,
    min_obs: int = _DEFAULT_MIN_OBS,
) -> str | None:
    """Return the highest-UCB-score arm from ``matches`` or None.

    Cold-start contract: returns None if ANY arm in ``matches`` lacks a
    model OR has ``n_obs < min_obs``. The cold-start fallback to
    Thompson-boost tie-break is the caller's responsibility — keeps the
    picker pure (no fallback policy embedded).

    Single-candidate short-circuit: if ``len(matches) == 1`` returns the
    only candidate without scoring. Saves a numpy import + a predict()
    call in the common case.

    Tie-break: when two arms score identically (rare with float scores
    but possible after clamping), sorted-by-arm-id determinism so
    repeated calls produce the same answer for the same inputs.

    Pure function — no I/O, no env reads. Caller gates entry via
    ``is_enabled()``.
    """
    if not matches:
        return None
    if len(matches) == 1:
        # Single-candidate short-circuit. Caller will accept the arm
        # regardless; no need to LinUCB-score a one-arm decision.
        return matches[0]

    scores: dict[str, float] = {}
    for arm_id in matches:
        # PR Z (2026-06-29): scoring helper aggregates per-platform
        # variants via max when GENLAB_PER_PLATFORM_BANDIT_ENABLED=1.
        # Identical to ``score_arm`` when flag is OFF — caller's
        # cold-start contract is unchanged in that path.
        s = score_arm_with_platform_split(arm_id, context, linucb_arms, min_obs=min_obs)
        if s is None:
            # Cold-start signal — even one under-observed arm collapses
            # the whole pick. Don't compare a confident arm against a
            # noisy one; let the caller's Thompson fallback handle it.
            logger.debug(
                "[linucb_picker] cold-start fallback: arm %s missing or under-observed",
                arm_id,
            )
            return None
        scores[arm_id] = s

    # Sorted-by-arm-id determinism on ties. Without this, dict iteration
    # order would leak through max() and tests would flake.
    best_arm = max(sorted(scores), key=lambda a: scores[a])
    logger.debug(
        "[linucb_picker] picked %s from %d candidates, scores=%s",
        best_arm,
        len(matches),
        {k: round(v, 4) for k, v in scores.items()},
    )
    return best_arm


def pick_best_arm_with_propensity(
    matches: list[str],
    context: np.ndarray | list[float],
    linucb_arms: dict[str, LinUCBArm],
    *,
    min_obs: int = _DEFAULT_MIN_OBS,
    temperature: float = _DETERMINISTIC_TEMPERATURE,
) -> tuple[str | None, float | None]:
    """Return ``(arm_id, propensity)`` — the IPS-aware variant of
    :func:`pick_best_arm`.

    The arm-selection rule (argmax UCB) is IDENTICAL to
    :func:`pick_best_arm`. The only addition is computing
    ``p(arm | context)`` as the **softmax weight** of the picked arm,
    matching the convention used by
    :meth:`learning.linucb.LinUCBBandit.select_with_propensity`
    so a future IPS replay can reweight decisions made via this
    picker and decisions made via the LinUCBBandit's direct path
    under the SAME formula.

    Cold-start contract: returns ``(None, None)`` if ANY arm in
    ``matches`` lacks a model OR has ``n_obs < min_obs`` — caller falls
    back to Thompson-boost (which has no propensity concept), so both
    halves of the tuple stay ``None`` together.

    Single-candidate short-circuit: ``len(matches) == 1`` returns
    ``(matches[0], 1.0)`` — the only candidate is trivially picked
    with probability 1 regardless of LinUCB state. Saves a numpy
    import + a predict() call in the common case.

    Floor: each arm's softmax weight is clamped to ``_MIN_PROPENSITY``
    so a dominated arm never reports a propensity of 0.0. This bounds
    the 1/p IPS weight and matches the convention from
    :func:`learning.linucb.compute_softmax_probabilities`.

    Pure function — no I/O, no env reads. Caller gates entry via
    :func:`is_enabled` exactly as it does for :func:`pick_best_arm`.
    """
    if not matches:
        return None, None
    if len(matches) == 1:
        # Single-candidate short-circuit. Propensity = 1.0 because the
        # selection is degenerate — no probability mass to distribute.
        return matches[0], 1.0

    scores: dict[str, float] = {}
    for arm_id in matches:
        # PR Z (2026-06-29): scoring helper aggregates per-platform
        # variants via max when GENLAB_PER_PLATFORM_BANDIT_ENABLED=1.
        # Identical to ``score_arm`` when flag is OFF — propensity
        # softmax shape is unchanged in that path.
        s = score_arm_with_platform_split(arm_id, context, linucb_arms, min_obs=min_obs)
        if s is None:
            # Cold-start signal — collapses BOTH halves of the tuple so
            # the caller treats it identically to a missing arm in the
            # non-propensity path. Returning (arm, None) would let the
            # caller mistakenly believe IPS data was available.
            logger.debug(
                "[linucb_picker] cold-start fallback (with_propensity): "
                "arm %s missing or under-observed",
                arm_id,
            )
            return None, None
        scores[arm_id] = s

    # Lazy numpy import (mirrors score_arm — keeps the module loadable
    # in environments that don't have numpy until a scoring path runs).
    try:
        import numpy as np
    except ImportError:
        # Falls back to the non-propensity argmax path; caller treats
        # this as cold-start (no IPS data) so degraded environments
        # never publish bogus propensity values.
        logger.debug("[linucb_picker] numpy unavailable — propensity unsupported")
        return None, None

    # Softmax over UCB scores, numerically stable (subtract max before
    # exp). Mirrors learning/linucb.py.compute_softmax_probabilities
    # but inlined to avoid the dict→dict round-trip + to keep the
    # picker module dependency-free apart from numpy.
    arm_ids = list(scores.keys())
    raw = np.array([scores[a] for a in arm_ids], dtype=np.float64)
    shifted = (raw - raw.max()) / temperature
    exp_vals = np.exp(shifted)
    total = exp_vals.sum()
    if not np.isfinite(total) or total <= 0.0:
        # Degenerate: uniform distribution. Shouldn't happen with finite
        # scores + positive temperature; the IPS estimator downstream is
        # safer with a defined fallback than a NaN.
        probs = np.full_like(exp_vals, 1.0 / len(arm_ids))
    else:
        probs = exp_vals / total
    # Floor the per-arm propensity. The renormalisation drift introduced
    # by clamping near-zero values is bounded by len(matches) *
    # _MIN_PROPENSITY which for any plausible matches count (≤10) is
    # less than 1e-5 — well below IPS estimator precision.
    probs = np.maximum(probs, _MIN_PROPENSITY)

    # Pick the arm with the highest UCB score (argmax — same rule as
    # pick_best_arm). Determinism on ties via sorted-by-arm-id, also
    # mirroring pick_best_arm. Propensity returned is the picked arm's
    # softmax weight (the clamped value, NOT the renormalised value —
    # matches the LinUCBBandit convention).
    best_arm = max(sorted(scores), key=lambda a: scores[a])
    best_propensity = float(probs[arm_ids.index(best_arm)])
    logger.debug(
        "[linucb_picker] picked %s with propensity=%.6f from %d candidates",
        best_arm,
        best_propensity,
        len(matches),
    )
    return best_arm, best_propensity


def _format_arm_state(arm: Any) -> str:
    """CLI-only helper — human-readable arm state for diagnostic output."""
    n_obs = getattr(arm, "n_obs", "?")
    alpha = getattr(arm, "alpha", "?")
    return f"n_obs={n_obs}, alpha={alpha}"


if __name__ == "__main__":
    # CLI entry — operators check enable state + see the min_obs threshold.
    import argparse

    parser = argparse.ArgumentParser(description="LinUCB-driven candidate-arm picker (Lever I2)")
    parser.add_argument(
        "--enabled-check", action="store_true", help="Print whether opt-in env is set"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.enabled_check:
        print(f"GENLAB_LINUCB_PICK_ENABLED: {is_enabled()}")
        if not is_enabled():
            print("Set GENLAB_LINUCB_PICK_ENABLED=1 to activate the LinUCB picker.")
        else:
            print(f"Min observations per arm before LinUCB scores trusted: {_DEFAULT_MIN_OBS}")
    else:
        print("Use --enabled-check to verify opt-in env state.")
        print("Programmatic usage:")
        print("  from genlab_core.learning.linucb_picker import pick_best_arm")
        print("  arm = pick_best_arm(matches, context, linucb_arms)")
