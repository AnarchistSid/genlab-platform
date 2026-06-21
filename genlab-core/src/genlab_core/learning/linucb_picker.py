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
import os
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


def is_enabled() -> bool:
    """Single source of truth for the opt-in flag.

    Centralized so callers short-circuit cheaply
    (``if not is_enabled(): return None``) without duplicating the
    "0"/"1" check. Tests toggle via monkeypatch on this one function.
    """
    return os.environ.get("GENLAB_LINUCB_PICK_ENABLED", "0") == "1"


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
        s = score_arm(arm_id, context, linucb_arms, min_obs=min_obs)
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
