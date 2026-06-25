"""Policy gate — pre-publish chokepoint (PR #566, 2026-06-25).

THE single function every publisher calls before sending content to
a platform. Aggregates the per-check decisions from upcoming PRs
(#567 disclosure, #568 copyright, #569 spam, #570 account health)
into one ComplianceDecision.

## Public surface

  check_publish_policy(blueprint, platform, niche_id) -> ComplianceDecision
    Runs every registered check, aggregates results into the
    most-restrictive decision (block > warn > allow), logs the
    aggregate to compliance_events, returns the decision.

  register_check(name, fn) -> None
    Hook for upcoming PRs (#567-#570) to plug their checks in
    without touching this module. fn signature:
      fn(blueprint, platform, niche_id) -> ComplianceDecision

## Observation-only mode

In PR #566, NO checks are registered. The gate aggregates an
empty list and returns ComplianceDecision('allow', [], {}). This
is intentional — the gate ships as a callable chokepoint that
upstream callers can route through TODAY (in dry-run or testing
contexts) without producing false-positive blocks.

Subsequent PRs each register one check. The aggregate decision
becomes 'warn' or 'block' as checks fire. The block→warn
substitution for observation-only mode happens via
enforcement_enabled_for(niche, check_name) — a future PR's
toggle that lets operators flip individual checks from
observation to enforcement per niche.

## Aggregation rule (most-restrictive wins)

  any block → result is block, reasons = concat(all block reasons)
  no block + any warn → result is warn, reasons = concat(all warn)
  all allow → allow

This is the right safety default: a single safety check raising
a block should NOT be overridden by 5 'allow' checks elsewhere.
The aggregator is conservative; the caller can always override
explicitly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from genlab_core.compliance.events import ComplianceDecision, log_compliance_event

logger = logging.getLogger(__name__)

# Type alias for a check function. Takes the blueprint dict + the
# target platform string + the niche_id (RLS scope), returns a
# ComplianceDecision.
CheckFn = Callable[[Mapping, str, str], ComplianceDecision]

# Registered checks. Populated by upcoming PRs via register_check.
# Module-level so registration happens at import time; check_publish_policy
# iterates this dict on every call.
_REGISTERED_CHECKS: dict[str, CheckFn] = {}


def register_check(name: str, fn: CheckFn) -> None:
    """Register a compliance check. Called at module-import time
    by check implementations in upcoming PRs. Idempotent — re-
    registering the same name OVERRIDES the previous fn (lets
    tests substitute mocks without un-registering first)."""
    if not name:
        raise ValueError("check name must be non-empty")
    _REGISTERED_CHECKS[name] = fn


def registered_check_names() -> list[str]:
    """Inspection helper — returns the names of currently
    registered checks. Pin tests + dashboard use this to surface
    'what's wired up' to operators."""
    return sorted(_REGISTERED_CHECKS.keys())


def check_publish_policy(
    blueprint: Mapping,
    platform: str,
    niche_id: str,
) -> ComplianceDecision:
    """Run every registered check, aggregate into the most-
    restrictive decision, log to compliance_events, return.

    Aggregation rule (defined in module docstring):
      any block → block
      no block + any warn → warn
      all allow → allow

    Logging is best-effort (log_compliance_event fail-opens).
    The decision is returned EVEN IF logging fails — compliance
    audit-log loss must NEVER block a publish.

    If a registered check raises, the exception is converted to
    a 'warn' decision with the check name in reasons. Rationale:
    a buggy check shouldn't crash the publish path. The WARNING
    log surfaces the bug for the operator to fix.
    """
    if not niche_id:
        # Defensive: an empty niche_id means the caller forgot to
        # pass scope. Refuse to make a decision; log the misuse.
        logger.warning("[policy_gate] empty niche_id; refusing to evaluate")
        return ComplianceDecision(
            decision="warn",
            reasons=["empty_niche_id"],
            metadata={"caller_bug": True},
        )

    all_reasons: list[str] = []
    all_metadata: dict = {}
    decisions_seen: set[str] = set()

    for check_name, check_fn in _REGISTERED_CHECKS.items():
        try:
            result = check_fn(blueprint, platform, niche_id)
        except Exception as exc:  # noqa: BLE001 — convert to warn
            logger.warning(
                "[policy_gate] check %r raised %s; converting to warn",
                check_name,
                exc,
            )
            all_reasons.append(f"check_raised:{check_name}")
            decisions_seen.add("warn")
            continue
        if not isinstance(result, ComplianceDecision):
            logger.warning(
                "[policy_gate] check %r returned non-ComplianceDecision %r; treating as warn",
                check_name,
                type(result).__name__,
            )
            all_reasons.append(f"check_bad_return:{check_name}")
            decisions_seen.add("warn")
            continue
        decisions_seen.add(result.decision)
        # Tag each reason with its source check for forensic clarity
        all_reasons.extend(f"{check_name}:{r}" for r in result.reasons)
        if result.metadata:
            all_metadata[check_name] = result.metadata

    # Most-restrictive wins
    if "block" in decisions_seen:
        aggregate = "block"
    elif "warn" in decisions_seen:
        aggregate = "warn"
    else:
        aggregate = "allow"

    decision_obj = ComplianceDecision(
        decision=aggregate,
        reasons=all_reasons,
        metadata=all_metadata,
    )

    # Best-effort log — never raises (fail-open by helper contract)
    log_compliance_event(
        niche_id=niche_id,
        event_type="pre_publish_check",
        decision=aggregate,
        blueprint_id=blueprint.get("id") if isinstance(blueprint, Mapping) else None,
        platform=platform,
        reasons=all_reasons,
        metadata=all_metadata,
    )

    return decision_obj
