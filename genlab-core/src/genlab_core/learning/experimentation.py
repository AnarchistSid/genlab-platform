"""Experimentation layer for the LinUCB bandit (Lever I1 + I3).

Today the bandit (`learning/linucb.py`) is a pure exploiter — given
context, it always picks the arm with the highest UCB. That works for
optimization, but two failure modes lurk:

1. **No counterfactual data**. We never observe "what would arm B have
   done?" if arm A was always chosen — so the bandit can converge on a
   local max and never discover a globally-better arm. Like Adam
   without momentum getting stuck in a saddle point.

2. **No experimental hypotheses**. We can ask "which hook style is
   winning right now?" but not "do question hooks beat imperative hooks
   for sports specifically?" The latter requires named experiments with
   pre-registered hypotheses + statistical power tracking.

Lever I1 (random control) + I3 (ab_tests) close both gaps.

## I1 — Random Control (Epsilon-Greedy Wrapper)

``select_arm_with_random_control`` wraps the bandit's pick. With
probability ``epsilon`` (default 0.05 = 5%), ignore the bandit and pick
uniformly from all arms. The resulting ``ArmSelection`` records which
mode was used so downstream metric attribution can separate
exploration plays from exploitation plays.

5% feels small but matters: at 1 reel/niche/day × 5 niches × 365 days
= ~91 random plays per niche per year. Enough to generate clean
counterfactual data without hurting baseline performance.

## I3 — Named A/B Tests

``assign_to_experiment`` looks up a registered ``ABTest`` and returns
which arm the blueprint should run. Assignment is **deterministic by
blueprint_id** — the same blueprint always lands in the same arm if
re-evaluated. Two implications:

1. Re-running the pipeline on a partial fail doesn't reshuffle arm
   assignment (no double-counting)
2. Hash collisions stay constant across pipeline re-runs (no churn)

## Opt-in

``GENLAB_EXPERIMENTATION_ENABLED=1`` env enables both I1 + I3.
Default disabled = no behavior change. Pattern matches Lever C / K /
O / G1 / G2 — opt-in env flag, fail-open None, pure-logic split,
whitelisted enum for downstream aggregation cleanliness.

Run via:
    python -m genlab_core.learning.experimentation --enabled-check
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

logger = logging.getLogger(__name__)


# Default epsilon for I1: 5% of plays are random exploration. Tune
# per-niche later via the ``epsilon`` kwarg on ``select_arm_with_random_control``.
_DEFAULT_EPSILON: Final[float] = 0.05

# Allocation tolerance — allocations must sum to 1.0 ± this. Strict
# enough to catch typos (0.5/0.4 = 0.9) but tolerant of float-summation
# noise (0.333 × 3 = 0.999).
_ALLOCATION_SUM_TOLERANCE: Final[float] = 0.01

# Selection-mode tags — closed set so downstream attribution
# aggregations don't get polluted by free-text.
SELECTION_MODE_BANDIT: Final[str] = "bandit"
SELECTION_MODE_RANDOM_CONTROL: Final[str] = "random_control"
SELECTION_MODE_AB_TEST: Final[str] = "ab_test"


@dataclass(frozen=True)
class ArmSelection:
    """Result of arm selection — which arm + how it was picked.

    The ``selection_mode`` field lets metric collection separate
    exploration plays from exploitation plays when computing per-arm
    reward statistics. Without this tag, random-control plays would
    distort the bandit's belief about its own choices.
    """

    arm_id: str
    selection_mode: str  # one of SELECTION_MODE_*
    experiment_id: str | None = None  # set when mode == AB_TEST


@dataclass(frozen=True)
class ABTest:
    """Registered experiment definition.

    ``allocation`` maps arm_id → probability mass in [0, 1]. Must sum to
    1.0 ± _ALLOCATION_SUM_TOLERANCE.

    ``niche_ids`` restricts which niches participate — most experiments
    are niche-scoped because what wins for gaming may not win for anime.
    Empty list (default) = all niches.

    ``end_date`` of None = open-ended. The runtime ``is_active()``
    check supports both fixed-window and rolling experiments.
    """

    experiment_id: str
    arms: list[str]
    allocation: dict[str, float]
    start_date: str  # ISO-8601
    end_date: str | None = None
    niche_ids: list[str] = field(default_factory=list)


def is_enabled() -> bool:
    """Single source of truth for the opt-in flag.

    Centralized so callers can short-circuit cheaply
    (``if not is_enabled(): return None``) without duplicating the
    "0"/"1" check. Tests toggle via monkeypatch on this one function.
    """
    return os.environ.get("GENLAB_EXPERIMENTATION_ENABLED", "0") == "1"


def validate_allocation(allocation: dict[str, float]) -> bool:
    """True when allocation values sum to 1.0 within tolerance.

    Pure function — no I/O. Used both at ABTest construction (caller
    should validate before registration) and at runtime (defensive check
    before assignment).
    """
    if not allocation:
        return False
    for v in allocation.values():
        if v < 0.0 or v > 1.0:
            return False
    total = sum(allocation.values())
    return abs(total - 1.0) <= _ALLOCATION_SUM_TOLERANCE


def select_arm_with_random_control(
    bandit_pick: str,
    all_arms: list[str],
    *,
    epsilon: float = _DEFAULT_EPSILON,
    rng: random.Random | None = None,
) -> ArmSelection:
    """Epsilon-greedy wrapper: with probability epsilon, ignore the
    bandit pick and choose uniformly from ``all_arms``.

    Pure function (no I/O, no env reads). The opt-in check happens at
    the caller — this function ALWAYS applies the epsilon math so
    callers can use it in tests without env juggling.

    ``rng`` is injectable for deterministic tests. In production,
    callers pass ``random.Random()`` (or nothing → module-default).

    Edge cases:
    - epsilon == 0.0 → always returns bandit pick
    - epsilon == 1.0 → always returns random pick
    - all_arms has only the bandit pick → random pick == bandit pick
      but selection_mode still flips to RANDOM_CONTROL (data integrity:
      the play was decided by exploration intent, not exploitation)
    - bandit_pick not in all_arms → still respected on the
      exploitation branch (caller's responsibility)
    """
    if not all_arms:
        # Nothing to pick from — return whatever the bandit said and
        # tag it as bandit mode. Empty arms is a caller bug, not ours.
        return ArmSelection(arm_id=bandit_pick, selection_mode=SELECTION_MODE_BANDIT)

    rng = rng or random.Random()
    epsilon = max(0.0, min(1.0, float(epsilon)))

    if rng.random() < epsilon:
        chosen = rng.choice(all_arms)
        return ArmSelection(arm_id=chosen, selection_mode=SELECTION_MODE_RANDOM_CONTROL)

    return ArmSelection(arm_id=bandit_pick, selection_mode=SELECTION_MODE_BANDIT)


def is_active(experiment: ABTest, *, now: datetime | None = None) -> bool:
    """True when ``now`` falls within the experiment's date window.

    Pure function. ``now`` is injectable for deterministic tests; in
    production, callers pass nothing → uses ``datetime.now(UTC)``.
    """
    now = now or datetime.now(UTC)
    try:
        start = datetime.fromisoformat(experiment.start_date)
    except (TypeError, ValueError):
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if now < start:
        return False
    if experiment.end_date:
        try:
            end = datetime.fromisoformat(experiment.end_date)
        except (TypeError, ValueError):
            return False
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        if now > end:
            return False
    return True


def _hash_to_unit(blueprint_id: str, experiment_id: str) -> float:
    """Deterministic mapping of (blueprint, experiment) → [0, 1).

    Blueprint-id alone would couple all experiments — every test would
    bucket the same blueprints into the same arms. Salting with the
    experiment_id decorrelates across experiments.

    SHA-256 because Python's built-in ``hash`` is salted per-process
    (PYTHONHASHSEED) and would produce different results on prod vs
    local — that would break the deterministic-assignment invariant.
    """
    salted = f"{experiment_id}::{blueprint_id}".encode()
    digest = hashlib.sha256(salted).digest()
    # Use first 8 bytes → 64-bit int → divide by max for unit interval
    n = int.from_bytes(digest[:8], "big")
    return n / float(1 << 64)


def assign_to_experiment(
    blueprint_id: str,
    experiment: ABTest,
    *,
    now: datetime | None = None,
) -> ArmSelection | None:
    """Deterministically assign a blueprint to an experiment arm.

    Returns None when the experiment is not active or the allocation
    is invalid (caller falls back to the bandit's normal pick).

    Same blueprint_id + same experiment_id ALWAYS produces the same
    arm assignment. This is the key invariant that makes pipeline
    re-runs safe — no double-counting, no churn.
    """
    if not is_active(experiment, now=now):
        return None
    if not validate_allocation(experiment.allocation):
        logger.debug(
            "[experimentation] invalid allocation for %s: %s",
            experiment.experiment_id,
            experiment.allocation,
        )
        return None

    u = _hash_to_unit(blueprint_id, experiment.experiment_id)

    # Walk cumulative allocation buckets — first one to exceed u wins.
    # Sorted-by-arm-id so assignment is reproducible across allocation
    # dict iteration orders.
    cumulative = 0.0
    for arm_id in sorted(experiment.allocation):
        cumulative += experiment.allocation[arm_id]
        if u < cumulative:
            return ArmSelection(
                arm_id=arm_id,
                selection_mode=SELECTION_MODE_AB_TEST,
                experiment_id=experiment.experiment_id,
            )

    # Fall-through: float rounding put u at exactly 1.0 — return the
    # last arm (sorted) so the contract still holds.
    last_arm = sorted(experiment.allocation)[-1]
    return ArmSelection(
        arm_id=last_arm,
        selection_mode=SELECTION_MODE_AB_TEST,
        experiment_id=experiment.experiment_id,
    )


if __name__ == "__main__":
    # CLI entry — operators check enable state without writing Python.
    import argparse

    parser = argparse.ArgumentParser(
        description="Experimentation layer for the LinUCB bandit (Lever I1 + I3)"
    )
    parser.add_argument(
        "--enabled-check", action="store_true", help="Print whether opt-in env is set"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.enabled_check:
        print(f"GENLAB_EXPERIMENTATION_ENABLED: {is_enabled()}")
        if not is_enabled():
            print("Set GENLAB_EXPERIMENTATION_ENABLED=1 to activate I1+I3.")
        else:
            print(f"Default epsilon (random control rate): {_DEFAULT_EPSILON}")
            print("Selection modes:")
            print(f"  - {SELECTION_MODE_BANDIT}")
            print(f"  - {SELECTION_MODE_RANDOM_CONTROL}")
            print(f"  - {SELECTION_MODE_AB_TEST}")
    else:
        print("Use --enabled-check to verify opt-in env state.")
        print("Programmatic usage:")
        print("  from genlab_core.learning.experimentation import (")
        print("      ABTest, assign_to_experiment, select_arm_with_random_control,")
        print("  )")
