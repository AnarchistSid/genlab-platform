"""Opt-in gate decision strategies (D2.7a, AUTO #2 runbook).

These strategies LAYER on top of ``auto_approval_gate.evaluate()``.
They never change the base gate's defaults. The base decision is the
input; an adjusted decision is the output. Both strategies default
to off — per-niche opt-in via ``publishing.yaml``.

Two strategies ship in this PR. The other letters (A, C, D, F) are
explicitly not implemented; the PR description explains why.

Strategy B — Bandit confidence boost
====================================
Goal: reduce false-negative rejections of arms the bandit has learned
to trust. When the blueprint's bandit arm has strong empirical
performance, give the gate a small confidence bump — enough to push
borderline approvals over the per-niche min_confidence floor without
overriding hard failures.

Mechanism:

* Read the arm's posterior from ``bandit_arms`` (or via the
  lookup callback the caller injects so tests can stub it).
* Require ``n_plays >= min_plays`` (default 20) for the boost to fire
  — below that the posterior is too noisy.
* Compute the residual ``observed_avg - posterior_mean`` (same math
  as ``bandit_residual.py``).
* Only boost when ``residual > 0`` (arm performs better than the
  prior dragged-down posterior — the gate's confidence is under-
  shooting reality).
* Boost magnitude: ``min(boost_max, abs(residual))``, capped at the
  configurable ``boost_max`` (default 0.10).
* Never bump approved=False to True — the base gate's hard failures
  remain authoritative.

Strategy E — Empirical agreement floor
======================================
Goal: prevent premature enforcement on niches where the gate is
empirically wrong. The calibration table already tracks operator-vs-
gate agreement per niche; this strategy turns that data into a hard
block when agreement is too low or sample size too small.

Mechanism:

* Read the niche's last-N-day calibration stats (or via lookup
  callback for tests).
* If ``sample_count < min_samples`` (default 10): refuse to approve.
  Cold-start niches need a baseline before auto-approval can fire.
* If ``agreement_rate < min_agreement`` (default 0.7): refuse to
  approve. The gate disagrees with the operator too often to trust.
* When blocked, the decision still records the original passed/failed
  checks for explainability — only ``approved`` flips to False and
  ``failed_checks`` gains ``empirical_agreement_floor``.
* When the base gate already says reject, this strategy is a no-op
  (no point in piling on rejections; the original reasons are clearer).

Why B and E (not C/D/F)
=======================

C — Multi-window agreement (24h + 48h + 168h): the existing 7d window
is already a reasonable smoothing; multi-window doesn't add signal
that justifies the implementation cost.

D — Time-of-day filter (only approve during operator-active hours):
the D3.10 kill switch already covers the incident-window use case
and per-niche schedule.yaml already shapes publish timing.

F — Score-stack threshold (composite + virality + qc combined into
one weighted score): would change the gate's existing thresholds,
which the runbook D2.7a constraint forbids ("not change the existing
thresholds — D1.3 did that").
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from genlab_core.scheduling.auto_approval_gate import AutoApprovalDecision

logger = logging.getLogger(__name__)


# ── Default config knobs ─────────────────────────────────────────────
# Conservative defaults — even when a niche flips a strategy ON, the
# effective change should be small unless thresholds are tuned.
_DEFAULT_BANDIT_MIN_PLAYS = 20
_DEFAULT_BANDIT_BOOST_MAX = 0.10
_DEFAULT_PRIOR_ALPHA = 1.0  # mirrors metric_collector + bandit_residual

_DEFAULT_AGREEMENT_MIN = 0.70
_DEFAULT_AGREEMENT_MIN_SAMPLES = 10
_DEFAULT_AGREEMENT_WINDOW_DAYS = 7


@dataclass(frozen=True)
class StrategyConfig:
    """Per-niche opt-in switches + tuning knobs for the layered strategies.

    Default-constructed instance is a no-op (both flags off) — the
    layered strategies leave the base decision untouched.
    """

    # Strategy B: bandit-confidence boost
    bandit_boost_enabled: bool = False
    bandit_min_plays: int = _DEFAULT_BANDIT_MIN_PLAYS
    bandit_boost_max: float = _DEFAULT_BANDIT_BOOST_MAX

    # Strategy E: empirical agreement floor
    agreement_floor_enabled: bool = False
    agreement_min: float = _DEFAULT_AGREEMENT_MIN
    agreement_min_samples: int = _DEFAULT_AGREEMENT_MIN_SAMPLES
    agreement_window_days: int = _DEFAULT_AGREEMENT_WINDOW_DAYS

    @classmethod
    def from_yaml_dict(cls, raw: dict | None) -> "StrategyConfig":
        """Parse a YAML ``strategies:`` block into a StrategyConfig.

        Shape (all keys optional):

            strategies:
              bandit_boost:
                enabled: true
                min_plays: 25
                boost_max: 0.08
              agreement_floor:
                enabled: true
                min_agreement: 0.75
                min_samples: 15
                window_days: 7

        Missing block / missing keys → defaults (all off).
        """
        raw = raw or {}
        b = raw.get("bandit_boost") or {}
        e = raw.get("agreement_floor") or {}
        return cls(
            bandit_boost_enabled=bool(b.get("enabled", False)),
            bandit_min_plays=int(b.get("min_plays", _DEFAULT_BANDIT_MIN_PLAYS)),
            bandit_boost_max=float(b.get("boost_max", _DEFAULT_BANDIT_BOOST_MAX)),
            agreement_floor_enabled=bool(e.get("enabled", False)),
            agreement_min=float(e.get("min_agreement", _DEFAULT_AGREEMENT_MIN)),
            agreement_min_samples=int(
                e.get("min_samples", _DEFAULT_AGREEMENT_MIN_SAMPLES)
            ),
            agreement_window_days=int(
                e.get("window_days", _DEFAULT_AGREEMENT_WINDOW_DAYS)
            ),
        )


# Lookup callback types — caller injects so tests don't need a DB and
# the gate doesn't depend on the bandit/calibration modules at import
# time. Each returns None when data is missing.

# (niche_id, arm_id) → {alpha, beta, n_plays}
BanditLookup = Callable[[str, str], dict | None]

# (niche_id, window_days) → {sample_count, agreement_count, agreement_rate}
CalibrationLookup = Callable[[str, int], dict | None]


def _bandit_observed_avg_and_posterior(
    *, alpha: float, beta: float, n_plays: int
) -> tuple[float | None, float]:
    """Returns (observed_avg, posterior_mean).

    Observed_avg is None when n_plays == 0 (undefined). Posterior is
    always defined as alpha / (alpha + beta).
    """
    posterior = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.0
    if n_plays <= 0:
        return None, posterior
    observed = (alpha - _DEFAULT_PRIOR_ALPHA) / n_plays
    observed = max(0.0, min(1.0, observed))  # clamp for float drift
    return observed, posterior


def apply_strategy_b(
    decision: AutoApprovalDecision,
    *,
    blueprint: dict,
    config: StrategyConfig,
    bandit_lookup: BanditLookup,
) -> AutoApprovalDecision:
    """Bandit confidence boost. Returns a new AutoApprovalDecision.

    No-op when the strategy is disabled, the blueprint has no
    arm_id/niche_id, the arm hasn't been played enough times, or the
    observed-avg is below the posterior mean (boost would push the
    wrong direction).
    """
    if not config.bandit_boost_enabled:
        return decision
    if not decision.approved:
        # Rejected blueprints stay rejected — no point boosting confidence.
        return decision

    niche_id = blueprint.get("niche_id") or ""
    arm_id = blueprint.get("arm_id") or ""
    if not niche_id or not arm_id:
        return decision

    try:
        arm = bandit_lookup(niche_id, arm_id)
    except Exception:  # noqa: BLE001 — never propagate from the gate
        logger.warning(
            "[strategy_b] bandit lookup raised for (%s, %s); skipping boost",
            niche_id,
            arm_id,
            exc_info=True,
        )
        return decision

    if not arm:
        return decision

    try:
        alpha = float(arm.get("alpha") or _DEFAULT_PRIOR_ALPHA)
        beta = float(arm.get("beta") or _DEFAULT_PRIOR_ALPHA)
        n_plays = int(arm.get("n_plays") or 0)
    except (TypeError, ValueError):
        return decision

    if n_plays < config.bandit_min_plays:
        return decision

    observed_avg, posterior = _bandit_observed_avg_and_posterior(
        alpha=alpha, beta=beta, n_plays=n_plays
    )
    if observed_avg is None or observed_avg <= posterior:
        # Only boost when the arm is performing ABOVE the (under-
        # shooting) posterior. Below-posterior means the prior is
        # over-confident already — boosting further is the wrong move.
        return decision

    boost = min(config.bandit_boost_max, observed_avg - posterior)
    new_confidence = min(1.0, decision.confidence + boost)
    new_reasons = list(decision.reasons) + [
        f"Strategy B: bandit arm {arm_id} obs_avg={observed_avg:.3f} > "
        f"posterior={posterior:.3f} (n={n_plays}) → +{boost:.3f} confidence"
    ]
    return replace(
        decision,
        confidence=round(new_confidence, 3),
        reasons=new_reasons,
    )


def apply_strategy_e(
    decision: AutoApprovalDecision,
    *,
    niche_id: str,
    config: StrategyConfig,
    calibration_lookup: CalibrationLookup,
) -> AutoApprovalDecision:
    """Empirical agreement floor. Returns a new AutoApprovalDecision.

    Flips approved=True → False when calibration agreement is too low
    or sample size too small. No-op when the strategy is disabled or
    the base gate already rejects.
    """
    if not config.agreement_floor_enabled:
        return decision
    if not decision.approved:
        # Base gate already rejecting — no point piling on.
        return decision
    if not niche_id:
        return decision

    try:
        stats = calibration_lookup(niche_id, config.agreement_window_days)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[strategy_e] calibration lookup raised for %s; failing CLOSED (rejecting)",
            niche_id,
            exc_info=True,
        )
        # Fail-closed: if we can't check empirical agreement, don't
        # auto-approve. The operator's existing manual flow keeps
        # publishing on schedule.
        return _reject_with_reason(
            decision,
            check="empirical_agreement_floor",
            reason="Strategy E: calibration lookup failed — failing closed",
        )

    if not stats:
        return _reject_with_reason(
            decision,
            check="empirical_agreement_floor",
            reason=(
                f"Strategy E: no calibration data for niche={niche_id} "
                f"(window={config.agreement_window_days}d) — failing closed"
            ),
        )

    sample_count = int(stats.get("sample_count") or 0)
    agreement_rate = float(stats.get("agreement_rate") or 0.0)

    if sample_count < config.agreement_min_samples:
        return _reject_with_reason(
            decision,
            check="empirical_agreement_floor",
            reason=(
                f"Strategy E: only {sample_count} calibration samples "
                f"(need {config.agreement_min_samples}) — failing closed"
            ),
        )

    if agreement_rate < config.agreement_min:
        return _reject_with_reason(
            decision,
            check="empirical_agreement_floor",
            reason=(
                f"Strategy E: agreement_rate={agreement_rate:.2%} < "
                f"{config.agreement_min:.0%} floor (samples={sample_count}) — "
                "operator disagrees too often"
            ),
        )

    # Passed both checks — record for explainability, leave decision unchanged.
    new_reasons = list(decision.reasons) + [
        f"Strategy E: agreement_rate={agreement_rate:.2%} (samples={sample_count}) "
        "above floor"
    ]
    return replace(decision, reasons=new_reasons)


def _reject_with_reason(
    decision: AutoApprovalDecision, *, check: str, reason: str
) -> AutoApprovalDecision:
    """Flip approved=False, append the rejection check + reason, drop
    confidence to 0.0 so the worker's min_confidence gate cannot ever
    let this through.
    """
    return replace(
        decision,
        approved=False,
        confidence=0.0,
        failed_checks=list(decision.failed_checks) + [check],
        reasons=list(decision.reasons) + [reason],
    )


def apply_strategies(
    decision: AutoApprovalDecision,
    *,
    blueprint: dict,
    niche_id: str,
    config: StrategyConfig,
    bandit_lookup: BanditLookup | None = None,
    calibration_lookup: CalibrationLookup | None = None,
) -> AutoApprovalDecision:
    """Convenience: apply B then E in canonical order.

    When a lookup is omitted, the corresponding strategy becomes a
    no-op (still configurable via flags but with no data source).
    """
    if config.bandit_boost_enabled and bandit_lookup is not None:
        decision = apply_strategy_b(
            decision,
            blueprint=blueprint,
            config=config,
            bandit_lookup=bandit_lookup,
        )
    if config.agreement_floor_enabled and calibration_lookup is not None:
        decision = apply_strategy_e(
            decision,
            niche_id=niche_id,
            config=config,
            calibration_lookup=calibration_lookup,
        )
    return decision
