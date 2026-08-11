"""Auto-accept low-risk strategist proposals (arm_add classification).

Motivating problem: the weekly strategist LLM generates 4-6 proposals
per niche. Applying them requires operator dashboard clicks to add
each proposal's index to ``strategist_reports.proposals_accepted``.
Since operator has not opened the dashboard for 24+ days (see the
calibration ratchet incident, 2026-07-23), zero proposals get
applied — the strategist runs weekly but the loop is one-way.

This module classifies each unaccepted arm_add proposal into two
buckets:

* **Auto-accept** — extends an EXISTING dimension for an existing
  niche. New ``style:{niche}:variant`` when other style arms exist.
  Low blast radius: a new style variant just introduces one more
  Beta(1,1) arm; the bandit's cold-start exploration surfaces it
  gradually.

* **Operator-gate** — new source dimension (``source:...``), new
  transformation family (``transform__newdim__...`` where the
  dimension has never been seen), or any shape we don't recognise.
  These are structural changes with unclear scope.

Additional guards:
* Only high-confidence proposals (strategist tagged them ``high``)
* Rate-limit: at most 2 auto-accepts per niche per week
* Flag-gated by ``GENLAB_PROPOSAL_AUTO_ACCEPT_ENABLED``

See:
* ``strategist_actions.py:apply_pending_actions`` — consumer that
  reads ``proposals_accepted`` and materialises accepted rows
* Rule #22 sibling: proposals bypass operator review, so misclass-
  ifying a structural change as "safe" would compound over weeks.
  The narrow whitelist + confidence + rate-limit chain prevents that.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)


_ENABLE_ENV_VAR: Final[str] = "GENLAB_PROPOSAL_AUTO_ACCEPT_ENABLED"

# Per-niche rate limit: no more than this many auto-accepts per
# rolling 7-day window. Conservative — a runaway strategist can't
# fill the bandit space with dozens of new arms via auto-accept.
MAX_AUTO_ACCEPTS_PER_WEEK: Final[int] = 2

# Confidence field values that unlock auto-accept. Strategist writes
# "low", "medium", "high" per hypothesis (see auto_promote_hypotheses
# for the sibling filter).
_HIGH_CONFIDENCE_VALUES: Final[frozenset[str]] = frozenset({"high"})


@dataclass(frozen=True)
class AcceptDecision:
    """Result of the auto-accept classifier for one proposal.

    Fields
    ------
    should_auto_accept : bool
    reason : str
        Starts with:
        * ``"auto_accept:"`` — safe classifier passes
        * ``"operator_gate:"`` — structural change, needs review
        * ``"skip:"`` — malformed proposal or flag off
    """

    should_auto_accept: bool
    reason: str


def _integration_enabled() -> bool:
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


def classify_arm_add(
    proposal: dict[str, Any],
    *,
    existing_arm_ids: frozenset[str] = frozenset(),
    proposal_confidence: str = "",
) -> AcceptDecision:
    """Classify a single arm_add proposal.

    Args:
        proposal: The raw strategist proposal dict. Must have
            ``type == "arm_add"`` and a ``proposed.arm_id`` field.
        existing_arm_ids: All arm_ids already present in bandit_arms
            for the niche. Used to detect whether a proposal extends
            an existing dimension.
        proposal_confidence: The strategist's tagged confidence for
            this proposal, from strategist_reports.causal_hypotheses
            or the proposal itself. Only "high" unlocks auto-accept.

    Returns:
        AcceptDecision. Never raises.
    """
    if proposal.get("type") != "arm_add":
        return AcceptDecision(False, "skip:not_arm_add")

    proposed = proposal.get("proposed") or {}
    # 2026-07-24: strategist writes ``proposed`` as a JSON-encoded
    # string, not a dict. Prod discovery: 9/9 arm_add proposals in the
    # 5 unreviewed reports got skip:malformed_proposed_field because
    # ``isinstance(str, dict)`` is False. Defensive parse when it
    # looks like JSON, fall through to malformed for narrative
    # descriptions (some proposed fields are prose, not structured).
    if isinstance(proposed, str):
        s = proposed.strip()
        if s.startswith("{") and s.endswith("}"):
            import json as _json

            try:
                proposed = _json.loads(s)
            except Exception:
                return AcceptDecision(
                    False, "skip:malformed_proposed_field (unparseable JSON)"
                )
        else:
            return AcceptDecision(
                False, "skip:malformed_proposed_field (narrative not JSON)"
            )
    if not isinstance(proposed, dict):
        return AcceptDecision(False, "skip:malformed_proposed_field")

    arm_id = str(proposed.get("arm_id", "")).strip()
    if not arm_id:
        return AcceptDecision(False, "skip:missing_arm_id")

    # Confidence/risk filter — proposals with low/medium confidence
    # stay gated regardless of shape.
    #
    # 2026-08-11 Bug 3: real strategist proposals emit `risk` (low/
    # medium/high), NOT `confidence`. Prod discovery: 0/25 proposals
    # in 30 days had `confidence` field, so the field-name mismatch
    # was silently blocking every auto-accept for months. Historical
    # `proposal_confidence` kwarg is now the union of `confidence`
    # (if caller passes it — from causal_hypotheses lookup) OR
    # inverted `risk` field on the proposal itself: risk=low →
    # treat as confidence=high (safe to auto-accept).
    risk_value = str(proposal.get("risk", "")).strip().lower()
    confidence_from_risk = "high" if risk_value == "low" else ""
    effective_confidence = (
        proposal_confidence.strip().lower() or confidence_from_risk
    )
    if effective_confidence not in _HIGH_CONFIDENCE_VALUES:
        return AcceptDecision(
            False,
            f"operator_gate:effective_confidence={effective_confidence!r} "
            f"(proposal_confidence={proposal_confidence!r}, risk={risk_value!r}) "
            f"not in _HIGH",
        )

    # Shape-based classification.
    if arm_id.startswith("style:"):
        # Style arm — check whether other style arms exist for this
        # niche (proves the dimension exists in this niche's bandit).
        style_prefix = "style:"
        if any(a.startswith(style_prefix) for a in existing_arm_ids):
            return AcceptDecision(
                True,
                f"auto_accept:style_variant (arm_id={arm_id})",
            )
        return AcceptDecision(
            False,
            f"operator_gate:first_style_arm_for_niche (arm_id={arm_id})",
        )

    if arm_id.startswith("transform__"):
        # Extract dimension: transform__<dim>__<value>
        rest = arm_id[len("transform__") :]
        parts = rest.split("__", 1)
        if len(parts) != 2 or not parts[0]:
            return AcceptDecision(
                False, f"operator_gate:malformed_transform_arm_id ({arm_id})"
            )
        dim = parts[0]
        dim_prefix = f"transform__{dim}__"
        if any(a.startswith(dim_prefix) for a in existing_arm_ids):
            return AcceptDecision(
                True,
                f"auto_accept:transform_variant (dim={dim}, arm_id={arm_id})",
            )
        return AcceptDecision(
            False,
            f"operator_gate:first_transform_dim ({dim})",
        )

    if arm_id.startswith("source:"):
        # New content source — always operator scope. Sources
        # broaden the fetch surface and interact with quota planning
        # + relevance filters.
        return AcceptDecision(
            False, f"operator_gate:new_source ({arm_id})"
        )

    if arm_id.startswith("hook_type:"):
        # Hook-type arms similar to style — check for existing hook_type
        hook_prefix = "hook_type:"
        if any(a.startswith(hook_prefix) for a in existing_arm_ids):
            return AcceptDecision(
                True,
                f"auto_accept:hook_type_variant (arm_id={arm_id})",
            )
        return AcceptDecision(
            False,
            f"operator_gate:first_hook_type_arm (arm_id={arm_id})",
        )

    # Unknown shape — always operator scope.
    return AcceptDecision(
        False,
        f"operator_gate:unknown_shape (arm_id={arm_id})",
    )


# -----------------------------------------------------------------
# 2026-08-11 Session 2: reward_weight classifier
# -----------------------------------------------------------------
#
# Motivation: strategy_phase.py already consumes reward_weight
# overrides from `strategist_reports.proposals[idx]` when idx is in
# `proposals_accepted`. But auto-accept only classifies arm_add, so
# reward_weight proposals sit forever unless the operator reviews
# them (which they haven't since 2026-06-29). Result: strategist
# emits reward_weight proposals weekly, none ever apply.
#
# Blast radius: reward_shaper.py:295 REPLACES the weight (not
# multiplies) and clamps 0.0 <= value <= 5.0 at consumer time.
# A malicious/broken value silently no-ops rather than crashing.
# Extra safety here: bound the RELATIVE change against BASE_WEIGHTS
# so auto-accept can't swing dm_send_rate from 0.25 -> 4.9 in one
# click. Operator-gate the wild-swing cases.
#
# Target format (matches strategy_phase.py:213):
#   "{niche_id}.reward_weight.{platform}.{metric}"
#
# Guards:
#   1. type == "reward_weight"
#   2. target parses AND platform+metric are known
#   3. proposed value in [0.0, 5.0] absolute
#   4. proposed value within [0.5*base, 2.0*base] relative
#   5. same confidence/risk gate as arm_add

# Max relative change from BASE weight for auto-accept. Change
# within [0.5x, 2.0x] of base is a re-tune; outside → operator scope.
_REWARD_WEIGHT_MAX_RELATIVE_CHANGE: Final[float] = 2.0
_REWARD_WEIGHT_MIN_RELATIVE_CHANGE: Final[float] = 0.5

# Absolute delta floor: if the base is very small (e.g. 0.05 skip_rate),
# 0.5x-2.0x is a tiny band. Allow an absolute ±0.1 delta as a floor
# so small-magnitude weights aren't stuck at their base value.
_REWARD_WEIGHT_ABS_DELTA_FLOOR: Final[float] = 0.10


def _load_base_weights() -> dict[str, dict[str, float]]:
    """Import BASE_WEIGHTS at call time (not module load) to avoid a
    circular import via genlab_core.learning.reward_shaper. Returns
    an empty dict on any error — fail-closed to skip:import_failed."""
    try:
        from genlab_core.learning.reward_shaper import BASE_WEIGHTS

        return BASE_WEIGHTS
    except Exception:
        return {}


def classify_reward_weight(
    proposal: dict[str, Any],
    *,
    niche_id: str = "",
    proposal_confidence: str = "",
) -> AcceptDecision:
    """Classify a single reward_weight proposal.

    Args:
        proposal: Raw strategist proposal dict. Must have
            ``type == "reward_weight"``, ``target``, and ``proposed``.
        niche_id: The niche this proposal was emitted for. Used to
            validate the target string's niche prefix.
        proposal_confidence: Strategist-tagged confidence. Only
            "high" (or risk="low" via inversion) unlocks auto-accept.

    Returns:
        AcceptDecision. Never raises.
    """
    if proposal.get("type") != "reward_weight":
        return AcceptDecision(False, "skip:not_reward_weight")

    target = str(proposal.get("target", "")).strip()
    if not target:
        return AcceptDecision(False, "skip:missing_target")

    # Target format: "{niche_id}.reward_weight.{platform}.{metric}"
    parts = target.split(".")
    if len(parts) != 4 or parts[1] != "reward_weight":
        return AcceptDecision(
            False, f"skip:malformed_target ({target!r})"
        )
    proposal_niche, _, platform, metric = parts

    if niche_id and proposal_niche != niche_id:
        return AcceptDecision(
            False,
            f"skip:target_niche_mismatch "
            f"(target_niche={proposal_niche!r} runner_niche={niche_id!r})",
        )

    # Validate platform + metric exist in BASE_WEIGHTS — otherwise
    # the consumer at reward_shaper.py:293 silently no-ops the
    # override. Better to operator-gate an unknown metric than to
    # accept an override that will never take effect.
    base_weights = _load_base_weights()
    if not base_weights:
        return AcceptDecision(
            False, "skip:base_weights_unavailable"
        )
    platform_weights = base_weights.get(platform)
    if not platform_weights:
        return AcceptDecision(
            False, f"operator_gate:unknown_platform ({platform!r})"
        )
    if metric not in platform_weights:
        return AcceptDecision(
            False,
            f"operator_gate:unknown_metric "
            f"(platform={platform!r} metric={metric!r}) — "
            "override would silently no-op at consumer",
        )
    base = float(platform_weights[metric])

    # Proposed value must be a number.
    proposed = proposal.get("proposed")
    try:
        proposed_val = float(proposed)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return AcceptDecision(
            False, f"skip:non_numeric_proposed ({proposed!r})"
        )

    # Absolute range check — matches the consumer clamp so we don't
    # auto-accept something the consumer would silently drop.
    if not 0.0 <= proposed_val <= 5.0:
        return AcceptDecision(
            False,
            f"operator_gate:proposed_out_of_range "
            f"(proposed={proposed_val:.3f} not in [0.0, 5.0])",
        )

    # Relative-change check — auto-accept only for re-tunes within
    # [0.5x, 2.0x] of base, OR within ±0.10 absolute delta (floor
    # for small-magnitude weights like skip_rate=-0.05).
    delta = proposed_val - base
    within_abs_floor = abs(delta) <= _REWARD_WEIGHT_ABS_DELTA_FLOOR
    if base > 0:
        ratio = proposed_val / base
        within_relative = (
            _REWARD_WEIGHT_MIN_RELATIVE_CHANGE
            <= ratio
            <= _REWARD_WEIGHT_MAX_RELATIVE_CHANGE
        )
    elif base == 0:
        # Base is zero — any nonzero proposal is a qualitative shift.
        # Only allow within the abs floor.
        within_relative = False
    else:
        # Base is negative (skip_rate=-0.05). Ratios flip sign; use
        # absolute delta only.
        within_relative = False

    if not (within_relative or within_abs_floor):
        return AcceptDecision(
            False,
            f"operator_gate:wild_swing "
            f"(base={base:.3f} proposed={proposed_val:.3f} "
            f"delta={delta:+.3f})",
        )

    # Confidence/risk gate — same rule as classify_arm_add.
    risk_value = str(proposal.get("risk", "")).strip().lower()
    confidence_from_risk = "high" if risk_value == "low" else ""
    effective_confidence = (
        proposal_confidence.strip().lower() or confidence_from_risk
    )
    if effective_confidence not in _HIGH_CONFIDENCE_VALUES:
        return AcceptDecision(
            False,
            f"operator_gate:effective_confidence={effective_confidence!r} "
            f"(proposal_confidence={proposal_confidence!r}, risk={risk_value!r}) "
            "not in _HIGH",
        )

    return AcceptDecision(
        True,
        f"auto_accept:reward_weight_retune "
        f"(target={target}, base={base:.3f} -> proposed={proposed_val:.3f})",
    )


# -----------------------------------------------------------------
# 2026-08-11 Session 3: gate_threshold + novelty_rate classifiers
# -----------------------------------------------------------------
#
# Same class-of-bug as reward_weight (Session 2): consumers exist,
# producer emits proposals, but auto-accept has no classifier ->
# every proposal sits in the report unaccepted indefinitely.
#
# Consumers:
#   * gate_threshold -> strategy_phase.py:220-228, applied at
#     auto_approval_gate.py:167. Default 0.3, clamped [0.05, 0.85]
#     at consumer.
#   * novelty_rate -> strategy_phase.py:229-236, applied by
#     push_to_backlog force-explore rate. Default 0.25, clamped
#     [0.0, 0.50] at consumer.
#
# Blast radius sizing:
#   The classifier's delta-from-baseline bound (±0.15) is
#   deliberately narrower than the consumer's absolute clamp so a
#   runaway strategist can't ratchet the value across the whole
#   allowed range via successive weekly accepts. Compare-to-BASELINE
#   (not compare-to-current) keeps drift bounded even after N
#   accepts.

# Consumer defaults — see auto_approval_gate.py:66 and the
# strategy_phase.py docstring at line 83.
_GATE_THRESHOLD_BASELINE: Final[float] = 0.3
_NOVELTY_RATE_BASELINE: Final[float] = 0.25

# Max delta from baseline for auto-accept. Anything beyond this
# is a large enough policy shift to warrant explicit operator eyes.
_SCALAR_MAX_DELTA_FROM_BASELINE: Final[float] = 0.15


def _classify_scalar_override(
    proposal: dict[str, Any],
    *,
    expected_type: str,
    baseline: float,
    absolute_min: float,
    absolute_max: float,
    max_delta_from_baseline: float,
    proposal_confidence: str,
) -> AcceptDecision:
    """Shared implementation for gate_threshold + novelty_rate.

    Both are single-scalar overrides with the same shape:
      * `proposed` is a numeric value
      * `type` is the proposal-type string
      * Absolute range clamp matches the consumer's clamp
      * Delta from baseline is bounded to prevent runaway ratchets
      * Same confidence/risk gate as arm_add + reward_weight
    """
    if proposal.get("type") != expected_type:
        return AcceptDecision(False, f"skip:not_{expected_type}")

    proposed = proposal.get("proposed")
    try:
        proposed_val = float(proposed)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return AcceptDecision(
            False, f"skip:non_numeric_proposed ({proposed!r})"
        )

    if not absolute_min <= proposed_val <= absolute_max:
        return AcceptDecision(
            False,
            f"operator_gate:proposed_out_of_range "
            f"(proposed={proposed_val:.3f} not in "
            f"[{absolute_min:.2f}, {absolute_max:.2f}])",
        )

    # 1e-9 epsilon so IEEE-754 rounding (e.g. 0.45 - 0.30 =
    # 0.15000000000000002) doesn't spuriously reject boundary values.
    delta = proposed_val - baseline
    if abs(delta) - max_delta_from_baseline > 1e-9:
        return AcceptDecision(
            False,
            f"operator_gate:delta_exceeds_baseline_bound "
            f"(baseline={baseline:.3f} proposed={proposed_val:.3f} "
            f"delta={delta:+.3f} max_abs={max_delta_from_baseline:.2f})",
        )

    risk_value = str(proposal.get("risk", "")).strip().lower()
    confidence_from_risk = "high" if risk_value == "low" else ""
    effective_confidence = (
        proposal_confidence.strip().lower() or confidence_from_risk
    )
    if effective_confidence not in _HIGH_CONFIDENCE_VALUES:
        return AcceptDecision(
            False,
            f"operator_gate:effective_confidence={effective_confidence!r} "
            f"(proposal_confidence={proposal_confidence!r}, "
            f"risk={risk_value!r}) not in _HIGH",
        )

    return AcceptDecision(
        True,
        f"auto_accept:{expected_type} "
        f"(baseline={baseline:.3f} -> proposed={proposed_val:.3f})",
    )


def classify_gate_threshold(
    proposal: dict[str, Any],
    *,
    proposal_confidence: str = "",
) -> AcceptDecision:
    """Classify a single gate_threshold proposal.

    Consumer: auto_approval_gate at auto_approval_gate.py:167 uses
    the override in place of `composite_score >= 0.3`. Clamps to
    `[0.05, 0.85]`. Default baseline is 0.3.

    Auto-accept range: baseline ±0.15 -> [0.15, 0.45].
    """
    return _classify_scalar_override(
        proposal,
        expected_type="gate_threshold",
        baseline=_GATE_THRESHOLD_BASELINE,
        absolute_min=0.05,
        absolute_max=0.85,
        max_delta_from_baseline=_SCALAR_MAX_DELTA_FROM_BASELINE,
        proposal_confidence=proposal_confidence,
    )


def classify_novelty_rate(
    proposal: dict[str, Any],
    *,
    proposal_confidence: str = "",
) -> AcceptDecision:
    """Classify a single novelty_rate proposal.

    Consumer: push_to_backlog force-explore rate. Clamps to
    `[0.0, 0.50]`. Default baseline is 0.25.

    Auto-accept range: baseline ±0.15 -> [0.10, 0.40].
    """
    return _classify_scalar_override(
        proposal,
        expected_type="novelty_rate",
        baseline=_NOVELTY_RATE_BASELINE,
        absolute_min=0.0,
        absolute_max=0.50,
        max_delta_from_baseline=_SCALAR_MAX_DELTA_FROM_BASELINE,
        proposal_confidence=proposal_confidence,
    )


def is_enabled() -> bool:
    """Public flag check. Called by the CLI runner before any
    classification loops."""
    return _integration_enabled()


__all__ = [
    "MAX_AUTO_ACCEPTS_PER_WEEK",
    "AcceptDecision",
    "classify_arm_add",
    "classify_gate_threshold",
    "classify_novelty_rate",
    "classify_reward_weight",
    "is_enabled",
]
