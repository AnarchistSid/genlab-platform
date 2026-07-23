"""Bandit regret signal check — surface when the bandit is
under-performing a naive baseline for multiple consecutive months.

Motivating scenario: the counterfactual replay artifact (produced
monthly by run_counterfactual_replay.py) carries per-arm IPS + naive
reward estimates + relative_lift. When ``relative_lift < 0`` across
multiple arms, the bandit is picking arms that underperform their
naive counterparts — a signal that exploration parameters
(``α`` in LinUCB, softmax ``T`` in stochastic sampling, ε in the
transformation-arm picker) may be over- or under-tuned.

This check surfaces the signal so operator can investigate. It does
NOT auto-tune the parameters — meta-parameter changes are structural
and require operator judgement on the trade-off (e.g. reducing
exploration might improve short-term reward but slow discovery of
new winners).

Design:
* Read the latest counterfactual_replay artifact.
* Count arms where relative_lift is present AND < 0 AND n_decisions
  ≥ threshold (below that the lift is noise).
* Fire WARNING when ≥ MIN_NEGATIVE_ARMS have negative lift.
* auto_fix is an operator suggestion ("investigate exploration
  parameters") — NOT in the completed-values whitelist so the
  auto_fix_applied resolver correctly leaves it visible.

Fail-open: any read / parse error returns empty alerts.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from genlab_core.monitoring.alerts import Alert

logger = logging.getLogger(__name__)


# Minimum decisions for an arm's lift signal to be meaningful. Below
# this the relative_lift is dominated by sampling noise.
_MIN_DECISIONS_FOR_SIGNAL: Final[int] = 20

# Fire the alert when this many arms show negative lift.
_MIN_NEGATIVE_ARMS: Final[int] = 3

# Rolling window — the signal must persist for this many days to
# qualify as a "chronic" signal. Latest artifact's generated_at must
# be within the last day (fresh) AND the previous artifact must show
# the same pattern.
_MAX_ARTIFACT_AGE_DAYS: Final[int] = 45


def _artifact_dir() -> Path:
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "counterfactual-replay"


def _find_recent_all_artifacts(days: int = _MAX_ARTIFACT_AGE_DAYS) -> list[Path]:
    """Return the latest ``replay-*-all.json`` files up to N days old,
    newest first."""
    dir_ = _artifact_dir()
    if not dir_.is_dir():
        return []
    now = datetime.now(UTC)
    candidates: list[tuple[Path, datetime]] = []
    for path in dir_.glob("replay-*-all.json"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        age_days = (now - mtime).total_seconds() / 86400
        if age_days > days:
            continue
        candidates.append((path, mtime))
    candidates.sort(key=lambda p: p[1], reverse=True)
    return [p for p, _ in candidates]


def _count_negative_lift_arms(payload: dict) -> tuple[int, list[str]]:
    """Return (count, arm_ids) of arms with meaningful negative lift."""
    per_arm = payload.get("per_arm") or []
    negatives: list[str] = []
    for arm in per_arm:
        if not isinstance(arm, dict):
            continue
        n = int(arm.get("n_decisions") or 0)
        if n < _MIN_DECISIONS_FOR_SIGNAL:
            continue
        lift = arm.get("relative_lift")
        if lift is None:
            continue
        try:
            lift_f = float(lift)
        except (TypeError, ValueError):
            continue
        if lift_f < 0.0:
            negatives.append(str(arm.get("arm_id", "?")))
    return len(negatives), negatives


def check_bandit_regret_signal() -> list[Alert]:
    """Fire a WARNING when the bandit's counterfactual regret signal
    shows consistent under-performance vs baseline."""
    try:
        artifacts = _find_recent_all_artifacts()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[bandit_regret] artifact discovery failed: %s", exc)
        return []
    if len(artifacts) < 2:
        # Need at least 2 to detect a "chronic" pattern. Single-artifact
        # noise doesn't trip the alert.
        return []

    try:
        latest = json.loads(artifacts[0].read_text())
        previous = json.loads(artifacts[1].read_text())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[bandit_regret] artifact parse failed: %s", exc)
        return []

    latest_neg, latest_arms = _count_negative_lift_arms(latest)
    previous_neg, _ = _count_negative_lift_arms(previous)

    if latest_neg < _MIN_NEGATIVE_ARMS or previous_neg < _MIN_NEGATIVE_ARMS:
        return []

    return [
        Alert(
            check="bandit_regret_signal",
            severity="warning",
            message=(
                f"Bandit counterfactual regret signal: "
                f"{latest_neg} arms show negative IPS-lift in the latest "
                f"replay AND {previous_neg} arms in the previous run. "
                f"Consider tuning exploration parameters "
                f"(LinUCB α, softmax T, ε_floor). Affected arms: "
                f"{', '.join(latest_arms[:5])}"
            ),
            details={
                "latest_negative_count": latest_neg,
                "previous_negative_count": previous_neg,
                "min_negative_threshold": _MIN_NEGATIVE_ARMS,
                "latest_artifact_mtime": artifacts[0].stat().st_mtime,
                "affected_arms": latest_arms[:10],
            },
            auto_fix=(
                "Investigate exploration parameters — check the "
                "AutoApprovalCalibrationCard + BanditPosteriorDrift "
                "card in Mission Control for context. See "
                "genlab_core.scheduling.optimal_time_learner and "
                "genlab_core.learning.linucb for parameter surfaces."
            ),
        )
    ]


__all__ = [
    "check_bandit_regret_signal",
]
