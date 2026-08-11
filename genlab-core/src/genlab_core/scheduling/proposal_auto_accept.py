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


def is_enabled() -> bool:
    """Public flag check. Called by the CLI runner before any
    classification loops."""
    return _integration_enabled()


__all__ = [
    "MAX_AUTO_ACCEPTS_PER_WEEK",
    "AcceptDecision",
    "classify_arm_add",
    "is_enabled",
]
