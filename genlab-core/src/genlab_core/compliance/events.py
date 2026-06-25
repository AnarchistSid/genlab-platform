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
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[compliance] log write failed (event_type=%r, decision=%r): %s",
            event_type,
            decision,
            exc,
        )
        return False
