"""Auto-approval enforcement worker.

See ``docs/SCHEDULING-CONTRACT.md`` for the three-field contract
(status × action_taken × scheduled_for) this module operates on. In
particular: setting action_taken='approved' on a VISUAL_READY blueprint
without a slot makes it re-eligible for auto-scheduling within ~1h.

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
import hashlib
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
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-5

logger = logging.getLogger(__name__)


def _pick_next_available_slot(
    *,
    backlog_client: Any,
    niche_id: str,
    exclude_record_id: str = "",
    canonical_slot_ist: str = "12:00",
) -> str | None:
    """Return the next available scheduled_for ISO string for ``niche_id``.

    Cap-aware: walks IST days 0..7 forward from "now + 1h", counts
    existing VISUAL_READY+approved + PUBLISHED blueprints per
    (niche, day), returns the canonical 12:00 IST slot on the first
    day where count < effective_cap.

    Returns ``None`` if no day in the next 7 has capacity — caller
    should treat this as "skip this approval, retry next pass" not
    "schedule for some default".

    Mirrors ``dashboard/server/core/publishing_queue.py::_next_available_slot``
    so the worker shares the same per-day-cap contract as the operator
    path (PR #220). Implemented here in genlab-core because the worker
    runs outside the dashboard's import boundary.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from genlab_core.publishing.daily_cap import _load_caps, _load_caps_config
    from genlab_core.scheduling.multi_publish_gate import (
        effective_cap,
        is_multi_publish_enabled,
    )

    ist = ZoneInfo("Asia/Kolkata")

    # Effective cap (defaults to 1 with multi_publish off).
    caps_config = _load_caps_config()
    caps = _load_caps()
    daily_post_cap = int(caps.get("instagram", 1))
    ceiling = int((caps_config.get("max_per_day_ceiling") or {}).get("instagram", daily_post_cap))
    enabled = is_multi_publish_enabled(caps_config, platform="instagram")
    cap = effective_cap(
        niche_id=niche_id,
        platform="instagram",
        daily_post_cap=daily_post_cap,
        max_per_day_ceiling=ceiling,
        multi_publish_enabled=enabled,
    )

    # Count existing scheduled_for per (niche, IST date), excluding self.
    posts_per_day: dict[str, int] = {}
    try:
        records = backlog_client.get_blueprints_by_status("VISUAL_READY", niche_id=niche_id)
        records += backlog_client.get_blueprints_by_status("PUBLISHED", niche_id=niche_id)
    except Exception:
        records = []
    for r in records:
        f = r.get("fields", r) if isinstance(r, dict) else {}
        rid = str(r.get("id", "") or f.get("id", "") or "")
        if exclude_record_id and rid == str(exclude_record_id):
            continue
        if (f.get("niche_id") or "").strip() != niche_id:
            continue
        sched_raw = f.get("scheduled_for", "")
        if not sched_raw:
            continue
        try:
            dt = datetime.fromisoformat(str(sched_raw).replace("Z", "+00:00"))
            day_key = dt.astimezone(ist).strftime("%Y-%m-%d")
            posts_per_day[day_key] = posts_per_day.get(day_key, 0) + 1
        except (ValueError, TypeError):
            pass

    # Walk forward, picking first day with headroom.
    now_ist = datetime.now(ist)
    earliest = now_ist + timedelta(hours=1)
    base_date = earliest.date()
    hour, minute = (int(x) for x in canonical_slot_ist.split(":"))

    for day_offset in range(0, 8):
        candidate_date = base_date + timedelta(days=day_offset)
        day_key = candidate_date.strftime("%Y-%m-%d")
        if posts_per_day.get(day_key, 0) >= cap:
            continue
        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            hour,
            minute,
            tzinfo=ist,
        )
        if candidate <= earliest:
            continue
        from datetime import UTC as _UTC

        return candidate.astimezone(_UTC).isoformat().replace("+00:00", "Z")

    return None


# ── D2.7a strategy lookups (read-only data sources) ──────────────────
# Module-level wrappers so the worker passes them as callbacks into
# ``gate_strategies.apply_strategies``. Each returns ``None`` on any
# failure (missing DSN, missing psycopg, DB error) — the strategies'
# fail-mode logic handles None correctly.


def _lookup_bandit_arm(niche_id: str, arm_id: str) -> dict | None:
    """Read one (niche, arm) row from bandit_arms.

    Returns ``{alpha, beta, n_plays}`` or None on miss / error.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg  # noqa: F401 — kept for the except ImportError clause
    except ImportError:
        return None
    try:
        # 2026-07-14: was `pg_connect(niche_id=niche_id)` followed by
        # `SET app.niche_id = ''` (session-scoped, no LOCAL). The
        # session-scoped SET could leak if this connection were ever
        # pooled — the next borrower would inherit admin mode. Fixed
        # by opening the connection in admin mode directly with
        # niche_id="all" (the canonical admin-scope value in
        # tenant_context.py:157). RLS bypass is now the transaction-
        # local GUC set by pg_connect itself, not a follow-up SET.
        with pg_connect(dsn, connect_timeout=5, niche_id="all") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT alpha, beta, n_plays FROM bandit_arms "
                    "WHERE niche_id = %s AND arm_id = %s",
                    (niche_id, arm_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                alpha, beta, n_plays = row
                return {
                    "alpha": float(alpha or 1.0),
                    "beta": float(beta or 1.0),
                    "n_plays": int(n_plays or 0),
                }
    except Exception:  # noqa: BLE001
        logger.warning("[auto_approver] bandit lookup failed", exc_info=True)
        return None


def _lookup_calibration_stats(niche_id: str, window_days: int) -> dict | None:
    """Reach into calibration_logger.stats() for the empirical agreement floor.

    Returns ``{sample_count, agreement_rate, agreement_count}`` or
    None on error.
    """
    try:
        from genlab_core.scheduling.calibration_logger import stats as cal_stats

        s = cal_stats(niche_id=niche_id, window_days=window_days)
        return {
            "sample_count": s.sample_count,
            "agreement_count": s.agreement_count,
            "agreement_rate": s.agreement_rate,
        }
    except Exception:  # noqa: BLE001
        logger.warning("[auto_approver] calibration lookup failed", exc_info=True)
        return None


def _safe_json_list(raw: Any) -> list:
    """Best-effort decode of a JSON list field. Returns [] on any
    failure (None, malformed JSON, wrong type) — never raises.

    Mirrors the helper used in
    ``dashboard/server/api/blueprints.py::_safe_json_parse(..., expected_type=list)``
    so the auto_approver's ``extra`` wrapper construction lines up
    byte-for-byte with the dashboard preview endpoint's normalization.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        import json

        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, list) else []
        except (ValueError, TypeError):
            return []
    return []


# Environment kill switch — wins over any per-niche YAML setting.
# Useful when the operator needs to halt all auto-approvals immediately
# (e.g., during an incident) without editing 5 YAML files.
_KILL_SWITCH_ENV = "GENLAB_AUTO_APPROVE_DISABLED"

# File-backed kill switch (D3.10, AUTO #2 runbook). Persists across
# worker restarts, set/cleared by the dashboard's
# /api/v1/auto-approval/kill-switch endpoint without operator SSH.
# The env var is still honoured (incident response from the shell);
# the file flag wins if EITHER is set. The default path is under
# .runtime so it survives clones but stays out of git via the
# project's .gitignore.
_KILL_SWITCH_FILE_ENV = "GENLAB_AUTO_APPROVE_KILL_FILE"
_KILL_SWITCH_DEFAULT_FILE = "/opt/genlab/.runtime/auto_approve_kill_switch"


def _kill_switch_file_path() -> str:
    """Resolve where the file-backed kill switch lives.

    Tests can override via the env var; production uses the default.
    """
    return os.environ.get(_KILL_SWITCH_FILE_ENV, "").strip() or _KILL_SWITCH_DEFAULT_FILE


def _niche_is_paused(niche_id: str) -> bool:
    """PR #577 consumer wire (2026-06-25): consult the niche_pauses
    table via the genlab_core.scheduling.niche_pause helper.

    Returns True when the niche has an active pause (row exists +
    paused_until is NULL OR > NOW()). Returns False on:
      * No active pause
      * DB error / missing DSN (helper fails-OPEN)
      * ImportError (running against pre-PR-577 core — graceful
        degrade so this wire is safe to deploy independently)

    Lazy import so the auto_approver module loads cheaply in
    environments that don't need pause checking (tests, CLI dry
    runs). The is_paused helper itself has a 5s connect timeout
    so a totally-down DB worst-case adds 5s to the pass start
    rather than crashing it.
    """
    try:
        from genlab_core.scheduling.niche_pause import is_paused

        return is_paused(niche_id)
    except ImportError:
        # Pre-PR-577 core → no pause table → not paused. Safe default.
        return False
    except Exception as exc:  # noqa: BLE001 — never crash the pass
        logger.warning(
            "[auto_approver] niche_pause.is_paused(%r) failed: %s",
            niche_id,
            exc,
        )
        return False


def _kill_switch_active() -> tuple[bool, str]:
    """Return (active, source) — checks env var AND file flag.

    Source is one of "env", "file", "both", "none" — used by the
    dashboard endpoint to tell the operator WHY it's off so they
    know what to flip to re-enable.
    """
    env_set = os.environ.get(_KILL_SWITCH_ENV, "").strip() not in (
        "",
        "0",
        "false",
        "False",
    )
    file_set = os.path.isfile(_kill_switch_file_path())
    if env_set and file_set:
        return True, "both"
    if env_set:
        return True, "env"
    if file_set:
        return True, "file"
    return False, "none"


# Marker written to blueprint.action_taken_source on auto-approval so
# operator-driven and worker-driven approvals are distinguishable in
# the backlog. Calibration logger reads this to exclude auto-approvals
# from the confusion matrix (a gate-vs-gate comparison would be circular).
AUTO_APPROVAL_SOURCE_TAG = "auto_approver_v1"


# W4.3 dice resolution. 10000 buckets gives 4-decimal-place granularity
# on rollout_pct (good enough for operator-readable percentages) and
# fits comfortably in a Python int from sha256's 256-bit hex digest.
_DICE_BUCKETS: int = 10000


def _check_compliance_gate(
    *,
    blueprint: dict,
    niche_id: str,
    record_id: str,
) -> list[str]:
    """PR #576 (2026-06-25): consult the Phase A compliance policy_gate
    for each platform the blueprint would publish to.

    Returns the list of platform-tagged reasons when ANY platform
    returns decision='block'; empty list when all platforms are
    'allow' or 'warn' (observation-only Phase A — proceed).

    Today policy_gate is observation-only, so 'block' never fires.
    This integration ships the safety architecture for when checks
    flip to enforcement per-(niche, check). The COMPLIANCE_EVENTS
    audit log records each consultation (one row per platform per
    auto-approval attempt) so operators can analyse what the gate
    WOULD have done before flipping any check to block.

    Best-effort: import + check failures log at DEBUG and return
    empty list (proceed). Auto-approval flow must NEVER fail because
    of a compliance-check side effect — the gate logs its own
    decisions; a missed check just degrades the audit log, not the
    publish path.
    """
    try:
        from genlab_core.compliance.policy_gate import check_publish_policy
    except ImportError:
        # Old core (pre-PR-566) → no compliance gate → proceed
        return []
    # The platforms the auto-approver pass would publish to. Today
    # this is the full default list — operators tune per-niche in
    # publishing.yaml's platforms_enabled, but at the auto-approver
    # layer we check the same set for simplicity (matches the
    # publish_all_platforms default).
    platforms_to_check = ("instagram", "youtube", "facebook", "twitter", "threads")
    block_reasons: list[str] = []
    for platform in platforms_to_check:
        try:
            decision = check_publish_policy(blueprint, platform, niche_id)
        except Exception as exc:  # noqa: BLE001 — best-effort
            # Round-3 audit P3 cleanup (2026-06-26): WARN, not DEBUG.
            # When the compliance gate raises for a platform, this
            # auto-approver path SILENTLY treats it as "no block
            # reasons for that platform" — i.e. the gate is OFF for
            # that platform on that blueprint. That's a compliance
            # enforcement hole; operator must see it to investigate.
            # Pairs with the compliance moat (#570→#585).
            logger.warning(
                "[auto_approver] compliance check raised for bp=%s platform=%s "
                "— gate silently skipped for this (bp, platform), "
                "block decision may be MISSED: %s",
                record_id,
                platform,
                exc,
            )
            continue
        if decision.decision == "block":
            # Tag reasons with the platform that produced them so
            # operators can see "instagram blocked + youtube allowed"
            # in compliance_events forensics.
            for r in decision.reasons:
                block_reasons.append(f"{platform}:{r}")
    return block_reasons


def _dice_value(record_id: str) -> float:
    """Return a deterministic dice value in [0.0, 1.0) for a blueprint.

    The SAME record_id always produces the SAME value, so the W4.3
    rollout decision is a *property of the blueprint* rather than a
    *property of the pass*. This is the post-Wave-4-audit semantic
    correction (2026-06-17): the original ``random.random()`` per-pass
    implementation produced "graduated latency", not "graduated
    rollout" — at rollout_pct=0.1, every blueprint would eventually
    win the dice on some pass and auto-approve, just slower.

    The hash is sha256 — overkill for this use, but cheap and removes
    any ambiguity about distribution. Bucket count is fixed at
    ``_DICE_BUCKETS`` (10000) so an operator can reason "rollout 0.1
    means hash bucket 0-999".
    """
    h = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    return (int(h, 16) % _DICE_BUCKETS) / _DICE_BUCKETS


# ── Policy ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AutoApprovalPolicy:
    """Per-niche auto-approval policy. Loaded from publishing.yaml's
    ``auto_publish`` block. Defaults preserve current behavior — every
    niche is OFF until the operator flips ``enabled``.

    The ``strategies`` field carries D2.7a's opt-in layered strategies
    (B = bandit confidence boost, E = empirical agreement floor). Both
    default to off; the worker applies them via
    ``gate_strategies.apply_strategies`` after the base gate evaluate.
    """

    enabled: bool = False
    min_confidence: float = 0.85
    max_approvals_per_pass: int = 3
    # D2.7a: opt-in layered strategies. Default StrategyConfig() is
    # all-off → no behaviour change for niches that haven't opted in.
    # Imported lazily inside load_policy to avoid the cross-module
    # import cycle at module load time.
    strategies: Any = None  # actually StrategyConfig — typed Any to avoid forward-ref
    # W4.3 (2026-06-17): graduated rollout knob. ``rollout_pct`` is the
    # probability (0.0-1.0) that any single eligible blueprint actually
    # gets auto-approved when ``enabled=true``. Lets operators flip a
    # niche on at e.g. 10% traffic, watch outcomes for a week, then
    # ramp to 50%, then 100% — without committing all 5/day at once.
    #
    # Set to 1.0 (default) preserves the pre-W4.3 behaviour: every
    # blueprint that passes the gate gets approved (up to
    # ``max_approvals_per_pass``).
    #
    # Setting to 0.0 effectively disables auto-approval even with
    # ``enabled=true`` — useful as a soft kill switch for one niche
    # without yanking the whole subsystem (the global kill switch
    # remains as the panic button).
    #
    # The dice roll is DETERMINISTIC per blueprint: the dice value is
    # ``sha256(record_id) % 10000 / 10000.0``, so the SAME blueprint
    # gets the SAME dice outcome on every pass. This matches the
    # operator's mental model of graduated rollout: at 0.1, the same
    # ~10% of blueprints get approved (the ones whose hash falls
    # under the threshold); the remaining 90% are deferred-not-doomed
    # — they'll auto-approve when the operator raises rollout_pct.
    #
    # The 2026-06-17 post-Wave-4 audit flagged the original
    # implementation: it used ``random.random()`` and ran the dice
    # per-pass. That produced the wrong semantic: at 0.1, blueprints
    # weren't "10% approved" — they were "100% approved after enough
    # passes", just slower. Operators ramping 10% → 50% → 100% over
    # a week would have seen 100% of the queue accumulate, not a
    # graduated exposure to risk.
    #
    # Tradeoff vs random: the same 10% will repeatedly auto-approve
    # across runs, which makes operator inspection harder (no
    # rerolling). Acceptable because the goal of rollout_pct is
    # *volume control*, not *fairness across blueprints*.
    rollout_pct: float = 1.0


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
        # D2.7a: parse the optional ``strategies`` sub-block. Lazy
        # import keeps the gate_strategies module out of the import
        # graph for callers that never touch the policy loader.
        from genlab_core.scheduling.gate_strategies import StrategyConfig

        strategies = StrategyConfig.from_yaml_dict(block.get("strategies"))
        # W4.3: rollout_pct ∈ [0.0, 1.0]. Defensively clamp instead of
        # raising — a yaml typo (rollout_pct: 50 meaning 50%) should
        # degrade to 1.0 (full traffic) not block all approvals. We log
        # so operators can spot the typo, but never fail-closed on a
        # rollout knob (that's what enabled=false is for).
        rollout_pct_raw = float(block.get("rollout_pct", 1.0))
        if rollout_pct_raw < 0.0 or rollout_pct_raw > 1.0:
            logger.warning(
                "[auto_approver] %s rollout_pct=%s out of [0.0, 1.0] — clamping to 1.0",
                niche_id,
                rollout_pct_raw,
            )
            rollout_pct = 1.0
        else:
            rollout_pct = rollout_pct_raw
        return AutoApprovalPolicy(
            enabled=bool(block.get("enabled", False)),
            min_confidence=float(block.get("min_confidence", 0.85)),
            max_approvals_per_pass=int(block.get("max_approvals_per_pass", 3)),
            strategies=strategies,
            rollout_pct=rollout_pct,
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
    # W4.3: blueprints that passed the gate + confidence but lost the
    # rollout_pct dice roll. Distinct from skipped_low_confidence so
    # operators can see the rollout knob's actual throttle effect in
    # dashboards (vs gate quality issues).
    skipped_rollout: list[str] = field(default_factory=list)
    # PR #576 (2026-06-25): blueprints that passed the gate +
    # confidence + rollout dice but were blocked by Phase A
    # compliance checks (copyright_attribution_check, spam_pattern_check,
    # account_health_check). Distinct from the other skip lists so
    # operators can see compliance-driven blocks separately from
    # gate-quality or rollout throttle.
    skipped_compliance_block: list[str] = field(default_factory=list)
    cap_reached: bool = False
    kill_switch_active: bool = False
    policy_disabled: bool = False
    # PR #577 consumer wire (2026-06-25): True when the niche has an
    # active row in niche_pauses. Distinct from kill_switch_active
    # (global) + policy_disabled (per-niche YAML flag) so operators
    # can see WHICH guard halted the pass on the dashboard.
    niche_paused: bool = False
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

    # ── Guard 1: global kill switch (env var OR file flag) ───────────────
    active, source = _kill_switch_active()
    if active:
        result.kill_switch_active = True
        logger.info(
            "[auto_approver] niche=%s — kill switch active (source=%s), no approvals performed",
            niche_id,
            source,
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

    # ── Guard 3: per-niche pause (PR #577) ────────────────────────────────
    # Operator emergency-stop. is_paused() fails-OPEN (returns False
    # on DB error) so a transient DB hiccup doesn't accidentally
    # disable autonomy. When a pause IS active, skip the entire pass
    # + log to compliance_events for forensic visibility.
    if _niche_is_paused(niche_id):
        result.niche_paused = True
        logger.info(
            "[auto_approver] niche=%s — niche is paused (PR #577 emergency-stop), "
            "no approvals performed",
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
        # 2026-07-15: print traceback to stderr (bypass logging.lastResort).
        import sys
        import traceback

        result.errors.append(f"blueprint query failed: {exc}")
        print(
            f"[auto_approver] niche={niche_id} candidate query failed: "
            f"{exc}\n{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )
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

        # ── Build the gate's `extra` wrapper (showstopper #1, 2026-06-15) ──
        # The gate's ``evaluate()`` reads composite_score / virality_score
        # / validation_status / visual_paths from ``blueprint["extra"]``.
        # Some storage paths flatten those to top-level fields. Without
        # this wrapper, the gate sees None for every score and aggregates
        # to confidence~0.5 for EVERY blueprint regardless of quality —
        # meaning the worker either auto-approves nothing (when
        # ``min_confidence > 0.5``) or everything (≤0.5). It also makes
        # calibration data not predict worker behavior because the
        # preview endpoint + ``calibration_logger`` DO build this
        # wrapper, while the worker DIDN'T.
        #
        # Mirrors ``dashboard/server/api/blueprints.py::auto_approval_preview``
        # — keep the two sites in sync; if the gate adds a new field, both
        # wrappers must expose it.
        if not isinstance(blueprint.get("extra"), dict):
            blueprint["extra"] = {
                "visual_paths": _safe_json_list(blueprint.get("visual_paths")),
                "composite_score": blueprint.get("composite_score"),
                "virality_score": blueprint.get("virality_score"),
                "validation_status": blueprint.get("validation_status"),
            }

        # ── Idempotency: never re-approve ─────────────────────────────────
        existing_action = (blueprint.get("action_taken") or "").strip()
        if existing_action:
            result.skipped_idempotent.append(record_id)
            continue

        # ── Run the gate ──────────────────────────────────────────────────
        try:
            decision = gate_evaluate(blueprint)
        except Exception as exc:
            # 2026-07-15: format the traceback into the message so it
            # ALWAYS lands in journalctl. Prior shape used exc_info=True
            # but the CLI runs under systemd Type=oneshot with no
            # configured logging handler → Python falls back to
            # logging.lastResort which strips tracebacks. Same-day
            # discovery: the errors=1 flap was invisible for HOURS
            # after the exc_info=True deploy because the traceback
            # never made it to the journal. print() to stderr goes
            # straight there, no handler configuration required.
            import sys
            import traceback

            result.errors.append(f"gate evaluation failed for {record_id}: {exc}")
            print(
                f"[auto_approver] niche={niche_id} bp={record_id} "
                f"gate error: {exc}\n{traceback.format_exc()}",
                file=sys.stderr,
                flush=True,
            )
            continue

        # ── D2.7a: layer opt-in strategies on top of the base gate ────────
        # Strategies are no-ops unless the niche opts in via publishing.yaml
        # ``auto_publish.strategies: {...}``. Even when on, B only nudges
        # confidence on borderline approvals and E only flips approved→False
        # when calibration data says the gate is empirically wrong. The
        # base ``decision`` is the input; the adjusted decision is what we
        # gate the rest of the pass on.
        if policy.strategies is not None and (
            policy.strategies.bandit_boost_enabled or policy.strategies.agreement_floor_enabled
        ):
            try:
                from genlab_core.scheduling.gate_strategies import apply_strategies

                decision = apply_strategies(
                    decision,
                    blueprint=blueprint,
                    niche_id=niche_id,
                    config=policy.strategies,
                    bandit_lookup=_lookup_bandit_arm,
                    calibration_lookup=_lookup_calibration_stats,
                )
            except Exception as exc:  # noqa: BLE001 — strategies are advisory
                # 2026-07-15: same silent-traceback fix as gate_evaluate
                # handler. logger.warning under systemd oneshot goes
                # through logging.lastResort which strips tracebacks.
                # Print directly to stderr → journalctl.
                import sys
                import traceback

                print(
                    f"[auto_approver] niche={niche_id} bp={record_id} "
                    f"strategy layer error (preserving base decision): "
                    f"{exc}\n{traceback.format_exc()}",
                    file=sys.stderr,
                    flush=True,
                )

        if not decision.approved:
            result.skipped_gate_rejected.append(record_id)
            continue
        if decision.confidence < policy.min_confidence:
            result.skipped_low_confidence.append(record_id)
            continue

        # ── W4.3: graduated rollout dice (deterministic) ──────────────────
        # Blueprint passed gate + min_confidence. The rollout knob
        # throttles approval *volume* (vs *quality*, which is
        # min_confidence's job).
        #
        # Per-blueprint deterministic dice: the dice value is
        # ``sha256(record_id) % 10000 / 10000``, fixed per blueprint.
        # The SAME blueprint gets the SAME outcome on every pass —
        # so at rollout_pct=0.1, the same ~10% always-approve, and
        # the other 90% are deferred-not-doomed (they auto-approve
        # when the operator raises rollout_pct).
        #
        # The 2026-06-17 post-Wave-4 audit flagged the original
        # ``random.random()`` per-pass implementation: at 0.1 it
        # didn't gate VOLUME, it just SLOWED the queue — eventually
        # every blueprint would win the dice on some pass and
        # auto-approve. That's not graduated rollout, that's
        # graduated latency.
        #
        # Fast-path: skip the dice at 1.0 so the default code path
        # is identical to pre-W4.3 (zero behaviour change for niches
        # that haven't opted in).
        if policy.rollout_pct < 1.0 and _dice_value(record_id) >= policy.rollout_pct:
            result.skipped_rollout.append(record_id)
            logger.info(
                "[auto_approver] niche=%s bp=%s — passed gate+confidence "
                "but lost rollout dice (pct=%.2f)",
                niche_id,
                record_id,
                policy.rollout_pct,
            )
            continue

        # ── PR #576 (2026-06-25): consult compliance policy_gate ──────────
        # Before auto-approving, run the per-platform compliance checks
        # registered in Phase A (PRs #566-#570): copyright attribution,
        # spam pattern detection, account health monitoring.
        #
        # Today policy_gate is observation-only — every check returns
        # 'allow' or 'warn'. 'block' is reserved for when the operator
        # explicitly enables enforcement per-(niche, check). So this
        # integration is a NO-OP behavior-wise today, but the
        # compliance_events table records each consultation and the
        # auto-approver respects 'block' automatically when checks flip
        # to enforcement (no future code change needed).
        #
        # Per-platform check fires once per platform the blueprint
        # would publish to. ANY platform 'block' → skip auto-approval
        # for ALL platforms (conservative: don't partial-publish into
        # a state where some platforms got the post and others didn't).
        compliance_block_reasons = _check_compliance_gate(
            blueprint=blueprint, niche_id=niche_id, record_id=record_id
        )
        if compliance_block_reasons:
            result.skipped_compliance_block.append(record_id)
            logger.info(
                "[auto_approver] niche=%s bp=%s — compliance gate BLOCKED "
                "auto-approval (reasons=%s)",
                niche_id,
                record_id,
                "; ".join(compliance_block_reasons[:5]),
            )
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
    # 2026-06-15 audit T#56: pick a cap-aware slot before approving.
    # Without this, the worker writes action_taken=approved but no
    # scheduled_for — the publisher then has no schedule gate to honor
    # and either publishes immediately (wrong time) OR
    # _schedule_gate.evaluate rejects (blueprint stuck).
    #
    # Mirrors the dashboard's _next_available_slot per-day-cap logic
    # (PR #220) so the worker shares the same producer/consumer
    # contract. Returns None on no-capacity-for-7-days → worker skips
    # this approval (safer than over-scheduling).
    try:
        scheduled_for = _pick_next_available_slot(
            backlog_client=backlog_client,
            niche_id=niche_id,
            exclude_record_id=record_id,
        )
    except Exception as slot_exc:  # noqa: BLE001
        # Fail-CLOSED: cap-lookup failure must NEVER let the worker
        # over-schedule. Skip this blueprint; next pass retries.
        # 2026-07-15: print traceback to stderr (bypass logging.lastResort).
        import sys
        import traceback

        result.errors.append(f"slot lookup failed for {record_id}: {slot_exc}")
        print(
            f"[auto_approver] niche={niche_id} bp={record_id} "
            f"slot lookup failed: {slot_exc}\n{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
        )
        return False

    if scheduled_for is None:
        logger.info(
            "[auto_approver] niche=%s bp=%s — no cap-available slot in next 7 days, skipping",
            niche_id,
            record_id,
        )
        result.errors.append(f"no slot available for {record_id}")
        return False

    update_fields = {
        "action_taken": "approved",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "scheduled_for": scheduled_for,
        # Source tag — calibration logger skips these in the confusion
        # matrix; dashboard "auto-approved today" feed counts them.
        "action_taken_source": AUTO_APPROVAL_SOURCE_TAG,
        "auto_approval_confidence": float(decision.confidence),
    }
    try:
        backlog_client.blueprints.update(record_id, update_fields, typecast=True)
    except Exception as exc:
        # 2026-07-15: print traceback to stderr (bypass logging.lastResort).
        import sys
        import traceback

        result.errors.append(f"backlog update failed for {record_id}: {exc}")
        print(
            f"[auto_approver] niche={niche_id} bp={record_id} "
            f"backlog update failed: {exc}\n{traceback.format_exc()}",
            file=sys.stderr,
            flush=True,
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

    # 2026-07-15: configure the root logger so ANY downstream module's
    # ``logger.warning(msg, exc_info=True)`` call actually renders the
    # traceback. Without this, Python falls back to
    # ``logging.lastResort`` under systemd Type=oneshot, which uses a
    # bare ``"%(levelname)s: %(message)s"`` formatter that strips
    # tracebacks. Same-day incident: gate_evaluate exceptions in this
    # unit were invisible for weeks because of exactly this.
    #
    # The explicit print-to-stderr in each except-handler (see
    # ``result.errors.append`` sites) is the last-resort defence;
    # this basicConfig is the FIRST defence — it makes untraced
    # ``logger.warning(exc_info=True)`` calls from imported libraries
    # (bandit_lookup, calibration_lookup, gate_strategies, etc.)
    # ALSO render their tracebacks.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

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
        # 2026-07-14: added rollout + compliance counters. Without them
        # examined=9 minus (approved+low_conf+rejected+idempotent) leaves
        # a silent gap; operators can't tell if the auto-approver produced
        # zero approvals because of gate quality or because rollout dice
        # deferred everything. The two counters close the audit loop.
        print(
            f"[{niche_id}] examined={result.candidates_examined} "
            f"approved={len(result.auto_approved)} "
            f"low_conf={len(result.skipped_low_confidence)} "
            f"rejected={len(result.skipped_gate_rejected)} "
            f"idempotent={len(result.skipped_idempotent)} "
            f"rollout_deferred={len(result.skipped_rollout)} "
            f"compliance_blocked={len(result.skipped_compliance_block)} "
            f"errors={len(result.errors)} "
            f"dry_run={result.dry_run} disabled={result.policy_disabled} "
            f"kill={result.kill_switch_active} paused={result.niche_paused} "
            f"cap={result.cap_reached}"
        )
        if result.errors:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(_cli())
