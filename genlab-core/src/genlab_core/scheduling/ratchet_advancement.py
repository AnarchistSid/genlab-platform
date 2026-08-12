"""AUTO #2 ratchet advancement signal.

## Problem this observes

`auto_approver` rollout_pct advancement is manual today — operator
edits `publishing.yaml` per niche when they decide the gate is
trustworthy. Two signals INFORM that decision:

  1. Operator-agreement calibration (`calibration_logger.stats`) —
     ≥30 samples AND ≥90% agreement between operator clicks and
     gate verdict
  2. Outcome-based readiness (`outcome_readiness.check_outcome_readiness`) —
     ≥30 auto-approved blueprints, ≥75% cleared reward_48h > 0.05

Per the 2026-07-23 memory:

> "Calibration ratchet stuck 24 days — dashboard shows ZERO review
>  clicks; nightly_scheduler auto-approves without writing
>  calibration rows, so AUTO #2 gate ('≥30 samples + ≥90% agree')
>  can never advance."

The calibration path is dead when operator doesn't click review.
The outcome path is the ONLY signal that can advance the ratchet
autonomously — but its computed values were only surfaced via a
dashboard endpoint, never logged where operators actually notice
(journal, ratchet audit).

## What this ships

`check_ratchet_advancement_signal(niche_id) -> RatchetAdvancementSignal`
fetches both signals + combines. `log_ratchet_signal(niche_id)`
wraps it with an INFO log line that operators can grep.

Log format:

    [ratchet] niche=sports combined=false calibration=0/0 agree=0.00 outcome=12/28 rate=0.43

## What this doesn't do

  * Doesn't auto-advance rollout_pct. Manual advancement stays the
    default — a bug that ratchets 5 niches to 100% based on stale
    reward data is a catastrophic blast radius. Ship the signal
    first; operator eyeballs for 1-2 weeks; then a follow-up
    commit can wire actual auto-advance behind a separate flag.
  * Doesn't back-fill missing calibration rows. Broken calibration
    is upstream from here.

## Flag control

`GENLAB_OUTCOME_READINESS_RATCHET_ENABLED` — when set, the outcome
signal contributes to `combined_ready` (OR logic with calibration).
When unset, `combined_ready = calibration_ready` (pre-fix
behavior, no change).

## Fail-open

Every DB / query error returns a zeroed signal + `combined_ready=False`.
Never raises.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RatchetAdvancementSignal:
    """Combined readiness for one niche."""

    niche_id: str
    # Operator-agreement path
    calibration_samples: int
    calibration_agreement_count: int
    calibration_agreement_rate: float
    calibration_ready: bool
    # Reward-outcome path
    outcome_samples: int
    outcome_good_count: int
    outcome_good_rate: float
    outcome_ready: bool
    # Combined verdict (respects GENLAB_OUTCOME_READINESS_RATCHET_ENABLED)
    combined_ready: bool


def _outcome_signal_enabled() -> bool:
    from genlab_core.settings import env_true

    return env_true("GENLAB_OUTCOME_READINESS_RATCHET_ENABLED")


def check_ratchet_advancement_signal(
    niche_id: str,
    *,
    calibration_window_days: int = 7,
    outcome_window_days: int = 14,
) -> RatchetAdvancementSignal:
    """Compute per-niche ratchet advancement signal.

    Args:
        niche_id: canonical niche identifier
        calibration_window_days: rolling window for operator-agreement
            stats (default 7 — matches calibration_logger default)
        outcome_window_days: rolling window for reward-outcome stats
            (default 14 — matches outcome_readiness default, spans
            2 weekly strategist cycles)

    Returns:
        RatchetAdvancementSignal. Never raises.
    """
    cal_samples = 0
    cal_agreement_count = 0
    cal_ready = False
    cal_rate = 0.0
    try:
        from genlab_core.scheduling.calibration_logger import stats
        cal = stats(niche_id=niche_id, window_days=calibration_window_days)
        cal_samples = cal.sample_count
        cal_agreement_count = cal.agreement_count
        cal_rate = cal.agreement_rate
        cal_ready = cal.ready_for_enforcement
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ratchet] calibration stats failed niche=%s: %s", niche_id, exc,
        )

    outcome_samples = 0
    outcome_good_count = 0
    outcome_rate = 0.0
    outcome_ready = False
    try:
        outcome = _query_outcome_readiness(niche_id, outcome_window_days)
        if outcome is not None:
            outcome_samples = outcome.sample_count
            outcome_good_count = outcome.outcome_good_count
            outcome_rate = outcome.outcome_good_rate
            outcome_ready = outcome.ready
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ratchet] outcome_readiness failed niche=%s: %s", niche_id, exc,
        )

    # Combined verdict — OR logic when outcome path is flag-enabled.
    # Default off preserves pre-fix behavior (calibration-only).
    if _outcome_signal_enabled():
        combined = cal_ready or outcome_ready
    else:
        combined = cal_ready

    return RatchetAdvancementSignal(
        niche_id=niche_id,
        calibration_samples=cal_samples,
        calibration_agreement_count=cal_agreement_count,
        calibration_agreement_rate=cal_rate,
        calibration_ready=cal_ready,
        outcome_samples=outcome_samples,
        outcome_good_count=outcome_good_count,
        outcome_good_rate=outcome_rate,
        outcome_ready=outcome_ready,
        combined_ready=combined,
    )


def _query_outcome_readiness(niche_id: str, window_days: int):
    """Open a psycopg connection + run check_outcome_readiness.

    Returns OutcomeReadiness dataclass or None on any failure.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return None
    try:
        import psycopg
        from psycopg.rows import dict_row

        from genlab_core.scheduling.outcome_readiness import (
            check_outcome_readiness,
        )
    except ImportError:
        return None

    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            return check_outcome_readiness(
                conn, niche_id, window_days=window_days,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[ratchet] outcome_readiness DB error niche=%s: %s", niche_id, exc,
        )
        return None


def log_ratchet_signal(niche_id: str) -> RatchetAdvancementSignal:
    """Fetch + log the combined ratchet signal.

    Emits INFO log line for operator grep:

        [ratchet] niche=sports combined=false calibration=0/0 agree=0.00
        outcome=12/28 rate=0.43

    Returns the signal so callers can also act on it (though today
    no caller does — pure observability).
    """
    signal = check_ratchet_advancement_signal(niche_id)
    logger.info(
        "[ratchet] niche=%s combined=%s "
        "calibration=%d/%d agree=%.2f ready=%s "
        "outcome=%d/%d rate=%.2f ready=%s",
        signal.niche_id,
        signal.combined_ready,
        signal.calibration_agreement_count,
        signal.calibration_samples,
        signal.calibration_agreement_rate,
        signal.calibration_ready,
        signal.outcome_good_count,
        signal.outcome_samples,
        signal.outcome_good_rate,
        signal.outcome_ready,
    )
    return signal


__all__ = [
    "RatchetAdvancementSignal",
    "check_ratchet_advancement_signal",
    "log_ratchet_signal",
]
