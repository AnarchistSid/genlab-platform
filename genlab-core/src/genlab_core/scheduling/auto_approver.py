"""Auto-approval enforcement worker.

AUTO #2 (2026-06-13): the second half of the autonomous-agent loop.
AUTO #1 ships the observation surface (gate + calibration logger +
Mission Control card). This module ships the enforcement worker that
actually approves blueprints — but ONLY when:

  1. Per-niche ``auto_publish.enabled: true`` is set in
     ``publishing.yaml`` (default: false for every niche today)
  2. The environment kill-switch ``GENLAB_AUTO_APPROVE_DISABLED`` is
     NOT set (defense-in-depth — operator can disable globally
     without touching YAML)
  3. ``AutoApprovalGate.evaluate()`` returns ``approved=True``
  4. Gate confidence ≥ the niche's ``min_confidence`` (default 0.85
     — conservative to start; operator tunes once calibration data
     accumulates)
  5. The blueprint hasn't already had ``action_taken`` set (full
     idempotency — re-running the worker is safe)
  6. Per-pass approval count for the niche < ``max_approvals_per_pass``
     (blast-radius cap — a misconfigured gate can't approve everything
     in one go)

Until the operator flips ``enabled: true``, this module is a no-op
that logs "policy disabled" and returns. The flip happens in YAML, not
in code, so no deploy is required to enable enforcement once
calibration data shows ready_for_enforcement.

Usage::

    # Library
    from genlab_core.scheduling.auto_approver import run_pass
    result = run_pass("gaming", dry_run=False)
    print(f"auto-approved: {result.auto_approved}")

    # CLI (intended for launchd / cron / Prefect)
    python -m genlab_core.scheduling.auto_approver --niche gaming
    python -m genlab_core.scheduling.auto_approver --niche all --dry-run

How v1 differs from a future hardened v2
========================================
v1 (this commit):
  * Per-niche policy from publishing.yaml
  * Structured-log audit trail via structlog (event="auto_approval")
  * Postgres write goes through the same BacklogClient path the
    operator uses, so the existing on-approve side effects (auto-
    scheduling, status flip to APPROVED) all fire identically.

v2 (future, when operator wants stronger audit surface):
  * Dedicated ``auto_approval_events`` Postgres table
  * Per-niche rate window (not just per-pass cap)
  * Dashboard "auto-approved today" feed alongside the calibration card
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from genlab_core.scheduling.auto_approval_gate import (
    AutoApprovalDecision,
)
from genlab_core.scheduling.auto_approval_gate import (
    evaluate as gate_evaluate_default,
)

logger = logging.getLogger(__name__)

# Environment kill switch — wins over any per-niche YAML setting.
# Useful when the operator needs to halt all auto-approvals immediately
# (e.g., during an incident) without editing 5 YAML files.
_KILL_SWITCH_ENV = "GENLAB_AUTO_APPROVE_DISABLED"

# Marker written to blueprint.action_taken_source on auto-approval so
# operator-driven and worker-driven approvals are distinguishable in
# the backlog. Calibration logger reads this to exclude auto-approvals
# from the confusion matrix (a gate-vs-gate comparison would be circular).
AUTO_APPROVAL_SOURCE_TAG = "auto_approver_v1"


# ── Policy ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AutoApprovalPolicy:
    """Per-niche auto-approval policy. Loaded from publishing.yaml's
    ``auto_publish`` block. Defaults preserve current behavior — every
    niche is OFF until the operator flips ``enabled``.
    """

    enabled: bool = False
    min_confidence: float = 0.85
    max_approvals_per_pass: int = 3


def load_policy(niche_id: str, *, genlab_root: Path | None = None) -> AutoApprovalPolicy:
    """Load the per-niche auto-approval policy.

    Reads ``{niche_root}/config/publishing.yaml`` (or
    ``CriticalRush/niches/gaming/config/publishing.yaml`` for gaming's
    nested layout). Returns a conservative default policy when the
    file is missing, when the ``auto_publish`` block is absent, or
    when any field has an invalid type — never raises.
    """
    # Defer the niche-root resolution import so library callers that
    # don't run inside the GenLab workspace don't take a hard dep on it.
    from genlab_core.pipeline.cli import NICHE_DIR_NAMES, _resolve_genlab_root

    root = genlab_root or _resolve_genlab_root()
    dir_name = NICHE_DIR_NAMES.get(niche_id)
    if not dir_name:
        logger.warning(
            "[auto_approver] unknown niche_id=%s — defaulting to disabled policy",
            niche_id,
        )
        return AutoApprovalPolicy()

    niche_root = root / dir_name
    # Gaming nests its config under niches/gaming/ — try that path first
    # then fall back to the flat layout used by the other 4 channels.
    candidates = [
        niche_root / "niches" / niche_id / "config" / "publishing.yaml",
        niche_root / "config" / "publishing.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                with open(candidate, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError) as exc:
                logger.warning(
                    "[auto_approver] %s yaml read failed (%s) — defaulting to disabled",
                    niche_id,
                    exc,
                )
                return AutoApprovalPolicy()
            if not isinstance(data, dict):
                data = {}
            break
    else:
        # No publishing.yaml found — default to disabled. Worker will log + skip.
        return AutoApprovalPolicy()

    block = data.get("auto_publish") or {}
    if not isinstance(block, dict):
        logger.warning(
            "[auto_approver] %s auto_publish block is not a dict — defaulting to disabled",
            niche_id,
        )
        return AutoApprovalPolicy()

    try:
        return AutoApprovalPolicy(
            enabled=bool(block.get("enabled", False)),
            min_confidence=float(block.get("min_confidence", 0.85)),
            max_approvals_per_pass=int(block.get("max_approvals_per_pass", 3)),
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[auto_approver] %s policy parse failed (%s) — defaulting to disabled",
            niche_id,
            exc,
        )
        return AutoApprovalPolicy()


# ── Pass result ────────────────────────────────────────────────────────────


@dataclass
class AutoApprovalPassResult:
    """Summary of one auto-approval pass over a niche's blueprints."""

    niche_id: str
    policy: AutoApprovalPolicy
    candidates_examined: int = 0
    auto_approved: list[str] = field(default_factory=list)
    skipped_low_confidence: list[str] = field(default_factory=list)
    skipped_gate_rejected: list[str] = field(default_factory=list)
    skipped_idempotent: list[str] = field(default_factory=list)
    cap_reached: bool = False
    kill_switch_active: bool = False
    policy_disabled: bool = False
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


# ── Worker ────────────────────────────────────────────────────────────────


def run_pass(
    niche_id: str,
    *,
    backlog_client: Any = None,
    gate_evaluate: Callable[[dict], AutoApprovalDecision] | None = None,
    dry_run: bool = False,
    genlab_root: Path | None = None,
) -> AutoApprovalPassResult:
    """One auto-approval pass over a niche's VISUAL_READY blueprints.

    Parameters injected for testability:
      * ``backlog_client`` — defaults to ``BacklogClient()``; tests pass
        a mock so no real Postgres roundtrip happens
      * ``gate_evaluate`` — defaults to ``auto_approval_gate.evaluate``;
        tests pass a controlled function to drive specific outcomes
      * ``dry_run`` — log the would-approve decisions without writing to
        backlog. Useful for the operator's first runs after flipping the
        enabled flag.
    """
    policy = load_policy(niche_id, genlab_root=genlab_root)
    result = AutoApprovalPassResult(niche_id=niche_id, policy=policy, dry_run=dry_run)

    # ── Guard 1: env kill switch ──────────────────────────────────────────
    if os.environ.get(_KILL_SWITCH_ENV, "").strip() not in ("", "0", "false", "False"):
        result.kill_switch_active = True
        logger.info(
            "[auto_approver] niche=%s — %s set, no approvals performed",
            niche_id,
            _KILL_SWITCH_ENV,
        )
        return result

    # ── Guard 2: per-niche policy ─────────────────────────────────────────
    if not policy.enabled:
        result.policy_disabled = True
        logger.debug(
            "[auto_approver] niche=%s — auto_publish.enabled=false, no approvals",
            niche_id,
        )
        return result

    # ── Resolve dependencies (lazy so tests don't need a live DB) ─────────
    if backlog_client is None:
        from genlab_core.http.backlog_client import BacklogClient

        backlog_client = BacklogClient()
    if gate_evaluate is None:
        gate_evaluate = gate_evaluate_default

    # ── Query candidate blueprints ────────────────────────────────────────
    try:
        candidates = backlog_client.blueprints.all(
            formula="AND({status}='VISUAL_READY', OR({action_taken}='', {action_taken}=BLANK()))",
            niche_id=niche_id,
            max_records=max(1, policy.max_approvals_per_pass * 3),
        )
    except Exception as exc:
        result.errors.append(f"blueprint query failed: {exc}")
        logger.warning("[auto_approver] niche=%s candidate query failed: %s", niche_id, exc)
        return result

    result.candidates_examined = len(candidates)

    for raw in candidates:
        if len(result.auto_approved) >= policy.max_approvals_per_pass:
            result.cap_reached = True
            logger.info(
                "[auto_approver] niche=%s — per-pass cap reached (%d), "
                "remaining candidates deferred to next pass",
                niche_id,
                policy.max_approvals_per_pass,
            )
            break

        # The BacklogClient `.all()` row shape mirrors `_transform_media`:
        # `{"id": ..., "fields": {...}}` for the SharePoint compat layer,
        # OR a flat dict on the Postgres path. Normalize.
        record_id = str(raw.get("id") or raw.get("record_id") or "").strip()
        fields = raw.get("fields", raw) if isinstance(raw, dict) else {}
        blueprint = {"id": record_id, **fields}
        if not record_id:
            continue

        # ── Idempotency: never re-approve ─────────────────────────────────
        existing_action = (blueprint.get("action_taken") or "").strip()
        if existing_action:
            result.skipped_idempotent.append(record_id)
            continue

        # ── Run the gate ──────────────────────────────────────────────────
        try:
            decision = gate_evaluate(blueprint)
        except Exception as exc:
            result.errors.append(f"gate evaluation failed for {record_id}: {exc}")
            logger.warning(
                "[auto_approver] niche=%s bp=%s gate error: %s",
                niche_id,
                record_id,
                exc,
            )
            continue

        if not decision.approved:
            result.skipped_gate_rejected.append(record_id)
            continue
        if decision.confidence < policy.min_confidence:
            result.skipped_low_confidence.append(record_id)
            continue

        # ── Decide → act ──────────────────────────────────────────────────
        if dry_run:
            logger.info(
                "[auto_approver] DRY_RUN: would approve bp=%s niche=%s confidence=%.3f reasons=%s",
                record_id,
                niche_id,
                decision.confidence,
                "; ".join(decision.reasons[:3]),
            )
            result.auto_approved.append(record_id)
            continue

        if not _execute_approval(
            backlog_client=backlog_client,
            record_id=record_id,
            decision=decision,
            niche_id=niche_id,
            result=result,
        ):
            # _execute_approval already recorded the error; move on
            continue
        result.auto_approved.append(record_id)

    return result


def _execute_approval(
    *,
    backlog_client: Any,
    record_id: str,
    decision: AutoApprovalDecision,
    niche_id: str,
    result: AutoApprovalPassResult,
) -> bool:
    """Write the auto-approval to the backlog. Returns True on success.

    Mirrors the on-approve side effects of the dashboard's
    `_execute_review_action`: sets `action_taken=approved`,
    `reviewed_at=<now>`, and tags the source so the calibration logger
    can exclude this row from the confusion matrix (a gate auto-approval
    voting agree with itself would inflate accuracy).
    """
    update_fields = {
        "action_taken": "approved",
        "reviewed_at": datetime.now(UTC).isoformat(),
        # Source tag — calibration logger skips these in the confusion
        # matrix; dashboard "auto-approved today" feed counts them.
        "action_taken_source": AUTO_APPROVAL_SOURCE_TAG,
        "auto_approval_confidence": float(decision.confidence),
    }
    try:
        backlog_client.blueprints.update(record_id, update_fields, typecast=True)
    except Exception as exc:
        result.errors.append(f"backlog update failed for {record_id}: {exc}")
        logger.warning(
            "[auto_approver] niche=%s bp=%s backlog update failed: %s",
            niche_id,
            record_id,
            exc,
        )
        return False

    # Structured audit log — the v1 audit surface. Operators can grep
    # the JSONL logs for `event=auto_approval`. A future v2 migrates to
    # a dedicated `auto_approval_events` Postgres table when needed.
    logger.info(
        "[auto_approver] APPROVED bp=%s niche=%s confidence=%.3f passed=%s",
        record_id,
        niche_id,
        decision.confidence,
        ",".join(decision.passed_checks),
        extra={
            "event": "auto_approval",
            "blueprint_id": record_id,
            "niche_id": niche_id,
            "confidence": decision.confidence,
            "passed_checks": decision.passed_checks,
            "source": AUTO_APPROVAL_SOURCE_TAG,
        },
    )
    return True


# ── CLI ────────────────────────────────────────────────────────────────────


def _cli() -> int:
    """CLI entry point. Used by launchd / cron / Prefect."""
    from genlab_core.pipeline.cli import NICHE_DIR_NAMES

    parser = argparse.ArgumentParser(
        description="AUTO #2 auto-approval enforcement worker.",
    )
    parser.add_argument(
        "--niche",
        required=True,
        help="Niche identifier, or 'all' to run every niche.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log would-approve decisions without writing to backlog.",
    )
    args = parser.parse_args()

    niches = list(NICHE_DIR_NAMES.keys()) if args.niche == "all" else [args.niche]

    exit_code = 0
    for niche_id in niches:
        result = run_pass(niche_id, dry_run=args.dry_run)
        print(
            f"[{niche_id}] examined={result.candidates_examined} "
            f"approved={len(result.auto_approved)} "
            f"low_conf={len(result.skipped_low_confidence)} "
            f"rejected={len(result.skipped_gate_rejected)} "
            f"idempotent={len(result.skipped_idempotent)} "
            f"errors={len(result.errors)} "
            f"dry_run={result.dry_run} disabled={result.policy_disabled} "
            f"kill={result.kill_switch_active} cap={result.cap_reached}"
        )
        if result.errors:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(_cli())
