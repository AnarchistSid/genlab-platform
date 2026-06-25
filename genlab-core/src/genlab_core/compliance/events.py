"""compliance_events durable audit log (PR #566, 2026-06-25).

Single helper that EVERY compliance check + decision flows through.
Writes one row per decision so operators can reconstruct "why did
this publish get blocked" or "when did the shadowban warnings start"
months after the fact.

## Public surface

  log_compliance_event(
      niche_id: str,
      event_type: str,
      decision: Literal['allow', 'block', 'warn'],
      *,
      blueprint_id: uuid.UUID | str | None = None,
      platform: str | None = None,
      reasons: list[str] | None = None,
      metadata: dict | None = None,
  ) -> bool
    Returns True on successful write. FAIL-OPEN: returns False
    on DB error but never raises — logging is observability,
    not enforcement; losing one row beats blocking a publish.

  ComplianceDecision dataclass — return shape from every
    compliance check. Frozen for safety (same rationale as
    PR #557's Tenant and PR #559's User DTOs).

## Closed enums (kept tight to spot typos at write time)

  VALID_EVENT_TYPES — the 8 documented event types from the
    migration. Unknown types get rejected at log time (not
    silently accepted — that would let a typo create new
    pseudo-types and fragment the audit log).

  VALID_DECISIONS — {'allow', 'block', 'warn'}. The 3 outcomes
    every check can produce.

## Mode discipline

In observation-only mode (PR #566 default), every check that
WOULD block instead logs decision='warn' with the same reasons
list. The block→warn substitution happens in the CALLER, not
here — we just record what the caller decided.

A future PR will add `enforcement_enabled_for(niche, event_type)`
that returns True per (niche, check) when calibration data
justifies enforcing. Until then, every check is observation.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


# Closed enums — expanded only when a new compliance check ships
VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "pre_publish_check",
        "ai_disclosure_added",
        "copyright_flag",
        "spam_pattern_detected",
        "account_health_warning",
        "auto_publish_block",
        "manual_override",
        # Catch-all for new check types in development — should be
        # promoted to a real type before the new check ships
        "unknown",
    }
)

VALID_DECISIONS: frozenset[str] = frozenset({"allow", "block", "warn"})


@dataclass(frozen=True)
class ComplianceDecision:
    """Frozen return shape from every compliance check.

    decision — 'allow' | 'block' | 'warn'.
    reasons — list of rule-name strings explaining the decision.
              Empty list when decision='allow' (no reasons to give).
    metadata — per-check-type detail dict. Defaults to empty.

    Frozen so callers can't accidentally rewrite a decision
    mid-pipeline. Same rationale as the Tenant and User DTOs
    from PRs #557 and #559.
    """

    decision: Literal["allow", "block", "warn"]
    reasons: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision not in VALID_DECISIONS:
            raise ValueError(
                f"decision must be one of {sorted(VALID_DECISIONS)}; got {self.decision!r}"
            )


def _connect():
    """Lazy-import + connect helper. Returns None on missing DSN
    or connection failure — log_compliance_event then fail-OPEN
    and returns False (audit-log loss < publish block)."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[compliance] DATABASE_URL not set; event logging disabled")
        return None
    try:
        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — fail-open per contract
        logger.warning("[compliance] connection failed: %s", exc)
        return None


def stats_by_niche(window_days: int = 7) -> dict[str, dict]:
    """Per-niche aggregation of compliance events in the window.

    Returns a dict keyed by ``niche_id``, value:

      {
        "total":   int,      # all events in window
        "warns":   int,      # decision='warn' count
        "blocks":  int,      # decision='block' count
        "allows":  int,      # decision='allow' count
        "top_event_type": str | None,  # most-frequent warn/block
                                        # event_type, None if 0 warns
        "top_event_count": int,        # count of top_event_type
        "by_event_type": {             # full breakdown
            event_type: {"warn": N, "block": N, "allow": N},
            ...
        }
      }

    Niches with zero events in the window are NOT in the returned
    dict — the dashboard component fills them with a zero-state row.
    This keeps the response small for the common case (most niches
    have zero events on a quiet day).

    Fail-OPEN: returns empty dict on DB error / missing DSN. Same
    contract as the other compliance read helpers — the dashboard
    card renders zero-state rather than crashing.

    Args:
      window_days — clamped to [1, 90] to avoid runaway scans
                    (compliance_events grows ~100 rows/day at
                    Phase A volumes; 90d ≈ 9k rows). Default 7d
                    matches the Mission Control card's headline
                    period.
    """
    # Defensive clamp — endpoint also enforces, but library
    # callers from scripts/tests shouldn't blow the heap either
    window_days = max(1, min(90, int(window_days)))

    conn_cm = _connect()
    if conn_cm is None:
        return {}

    try:
        with conn_cm as conn:
            rows = conn.execute(
                """
                SELECT niche_id, event_type, decision, COUNT(*) AS n
                FROM compliance_events
                WHERE created_at > NOW() - make_interval(days => %s)
                GROUP BY niche_id, event_type, decision
                """,
                (window_days,),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[compliance] stats_by_niche failed: %s", exc)
        return {}

    # Each row is (niche_id, event_type, decision, n). Build the
    # nested aggregation incrementally so a single niche with many
    # event_types lands in one dict.
    out: dict[str, dict] = {}
    for row in rows:
        # Row shape varies with row_factory — tolerate both tuple
        # and dict (compliance.events._connect doesn't pin dict_row
        # like niche_pause does, so we get tuples by default)
        if isinstance(row, dict):
            niche_id = row["niche_id"]
            event_type = row["event_type"]
            decision = row["decision"]
            n = int(row["n"])
        else:
            niche_id, event_type, decision, n = row[0], row[1], row[2], int(row[3])

        bucket = out.setdefault(
            niche_id,
            {
                "total": 0,
                "warns": 0,
                "blocks": 0,
                "allows": 0,
                "top_event_type": None,
                "top_event_count": 0,
                "by_event_type": {},
            },
        )
        bucket["total"] += n
        if decision == "warn":
            bucket["warns"] += n
        elif decision == "block":
            bucket["blocks"] += n
        elif decision == "allow":
            bucket["allows"] += n

        et = bucket["by_event_type"].setdefault(event_type, {"warn": 0, "block": 0, "allow": 0})
        if decision in ("warn", "block", "allow"):
            et[decision] += n

    # Post-pass: derive top_event_type per niche (most-frequent
    # warn OR block — allows aren't actionable, so they're excluded
    # from "what should the operator investigate first")
    for bucket in out.values():
        top_type = None
        top_n = 0
        for et, counts in bucket["by_event_type"].items():
            attention = counts["warn"] + counts["block"]
            if attention > top_n:
                top_n = attention
                top_type = et
        bucket["top_event_type"] = top_type
        bucket["top_event_count"] = top_n

    return out


def log_compliance_event(
    niche_id: str,
    event_type: str,
    decision: str,
    *,
    blueprint_id: uuid.UUID | str | None = None,
    platform: str | None = None,
    reasons: list[str] | None = None,
    metadata: dict | None = None,
) -> bool:
    """Durable audit-log write for a single compliance decision.

    Returns True on successful write. FAIL-OPEN: returns False
    on any failure (DSN missing, connection error, invalid input)
    but NEVER raises. Compliance audit logging is observability,
    not enforcement — losing one row must NEVER block a publish.

    Args:
      niche_id    — non-empty; RLS scope
      event_type  — must be in VALID_EVENT_TYPES (typo guard)
      decision    — must be in VALID_DECISIONS
      blueprint_id — optional UUID (some events aren't blueprint-tied)
      platform    — optional platform name ('instagram', 'youtube', ...)
      reasons     — list of rule-name strings; defaults to []
      metadata    — per-event-type detail dict; defaults to {}

    Validation errors (empty niche_id, unknown event_type, unknown
    decision) log at WARNING and return False without writing — the
    caller's failed write is observable but never propagates as a
    raise. This is the only place those enums are enforced; downstream
    queries can trust the column values.
    """
    if not niche_id:
        logger.warning("[compliance] refusing to log: empty niche_id")
        return False
    if event_type not in VALID_EVENT_TYPES:
        logger.warning(
            "[compliance] refusing to log: unknown event_type=%r (typo guard); valid: %s",
            event_type,
            sorted(VALID_EVENT_TYPES),
        )
        return False
    if decision not in VALID_DECISIONS:
        logger.warning(
            "[compliance] refusing to log: unknown decision=%r; valid: %s",
            decision,
            sorted(VALID_DECISIONS),
        )
        return False

    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        import json

        # JSON-encode here so we control the serialisation (vs trusting
        # psycopg's adapter chain to round-trip every shape). Empty
        # default to satisfy NOT NULL on both columns.
        reasons_json = json.dumps(reasons or [])
        metadata_json = json.dumps(metadata or {})
        with conn_cm as conn:
            conn.execute(
                """
                INSERT INTO compliance_events
                    (niche_id, blueprint_id, platform, event_type,
                     decision, reasons, metadata)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                """,
                (
                    niche_id,
                    str(blueprint_id) if blueprint_id else None,
                    platform,
                    event_type,
                    decision,
                    reasons_json,
                    metadata_json,
                ),
            )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[compliance] log write failed (event_type=%r, decision=%r): %s",
            event_type,
            decision,
            exc,
        )
        return False

    # AFTER successful DB write: fire Slack alert on block decisions
    # (opt-in via GENLAB_COMPLIANCE_SLACK_WEBHOOK env). Order matters
    # — we never want to alert on a decision we failed to persist.
    # notify_compliance_block is fail-OPEN; its return value does NOT
    # affect the function's success contract (DB write is the source
    # of truth, Slack is operator convenience).
    if decision == "block":
        try:
            from genlab_core.compliance.slack_notifier import (
                notify_compliance_block,
            )

            notify_compliance_block(
                niche_id=niche_id,
                event_type=event_type,
                blueprint_id=blueprint_id,
                platform=platform,
                reasons=reasons,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "[compliance] slack notify dispatch failed for event_type=%r: %s",
                event_type,
                exc,
            )

    return True
