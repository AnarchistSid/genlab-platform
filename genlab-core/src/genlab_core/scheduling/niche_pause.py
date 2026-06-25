"""Per-niche pause primitive (PR #577, 2026-06-25).

The operator's emergency-stop. When something goes wrong — a
copyright strike lands, account shadowbanned, abuse detected — the
operator needs to PAUSE publishing for that niche immediately.
Without this primitive: edit publishing.yaml + redeploy. With this
primitive: one DB row, every downstream consumer (auto-approver,
publisher) checks before acting.

## Public surface

  is_paused(niche_id) -> bool
    True when the niche has an active pause row. Auto-resolves
    auto-expiry (paused_until in the past → treated as unpaused;
    the row is left for a future sweeper to garbage-collect).

  get_pause(niche_id) -> NichePause | None
    Full pause record (for dashboard display + operator inspection).
    None on no active pause OR DB error.

  list_active_pauses() -> list[NichePause]
    All currently-paused niches (admin dashboard view).
    Empty list on DB error.

  pause(niche_id, reason, paused_until=None, paused_by=None) -> bool
    Insert / replace the pause row. Returns True on success.

  unpause(niche_id) -> bool
    Delete the pause row. Idempotent — returns True even when no
    row existed (operator clicking 'resume' on an already-resumed
    niche shouldn't error).

## Failure modes

  All read helpers FAIL-OPEN (return None / [] / False).
  All write helpers FAIL-CLOSED (return False on any error).

The asymmetry is deliberate. A read failure should NOT cause the
auto-approver to halt publishing (we want it to proceed under the
assumption "no pause known"). A write failure SHOULD surface to
the operator who tried to pause — silently dropping a pause is
the worst-case outcome (operator thinks they stopped publishing,
the system keeps going).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NichePause:
    """One pause record for the dashboard / operator inspection.

    Mirrors the niche_pauses row. Frozen so consumers (auto-approver,
    publisher) can't accidentally mutate state mid-evaluation.
    Same safety rationale as the Tenant / User / ComplianceDecision
    / HealthSignal DTOs from earlier PRs.
    """

    niche_id: str
    paused_until: datetime | None
    reason: str
    paused_by: str | None
    created_at: datetime


def _connect():
    """Lazy-import + connect helper. None on missing DSN / connection
    error. Callers fail-OPEN (read) or fail-CLOSED (write) per
    their contract."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[niche_pause] DATABASE_URL not set; pause state disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[niche_pause] connection failed: %s", exc)
        return None


def _row_to_pause(row: dict) -> NichePause:
    """Translate a niche_pauses row to the NichePause DTO."""
    return NichePause(
        niche_id=row["niche_id"],
        paused_until=row.get("paused_until"),
        reason=row["reason"],
        paused_by=row.get("paused_by"),
        created_at=row["created_at"],
    )


def is_paused(niche_id: str) -> bool:
    """Return True when the niche has an active pause.

    'Active' means: a row exists in niche_pauses AND either
    paused_until is NULL (indefinite) OR paused_until is in the
    future. An expired auto-pause row is treated as unpaused (the
    row is left for a future sweeper to GC).

    Fail-OPEN: returns False on DB error / missing DSN. The auto-
    approver and publisher proceed under "no pause known" rather
    than halting on a transient DB hiccup.
    """
    if not niche_id:
        return False
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            row = conn.execute(
                """
                SELECT paused_until FROM niche_pauses
                WHERE niche_id = %s
                  AND (paused_until IS NULL OR paused_until > NOW())
                LIMIT 1
                """,
                (niche_id,),
            ).fetchone()
        return row is not None
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[niche_pause] is_paused(%r) failed: %s", niche_id, exc)
        return False


def get_pause(niche_id: str) -> NichePause | None:
    """Return the NichePause record for the niche, or None.

    Returns None for:
      * Empty niche_id
      * No active pause (paused_until expired OR no row exists)
      * DB error / missing DSN (fail-OPEN)

    Used by the dashboard to render the pause card ("Paused: 12h ago
    by alex for 'copyright strike'").
    """
    if not niche_id:
        return None
    conn_cm = _connect()
    if conn_cm is None:
        return None
    try:
        with conn_cm as conn:
            row = conn.execute(
                """
                SELECT niche_id, paused_until, reason, paused_by, created_at
                FROM niche_pauses
                WHERE niche_id = %s
                  AND (paused_until IS NULL OR paused_until > NOW())
                LIMIT 1
                """,
                (niche_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_pause(row)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[niche_pause] get_pause(%r) failed: %s", niche_id, exc)
        return None


def list_active_pauses() -> list[NichePause]:
    """All currently-active pauses across all niches.

    Sorted by created_at DESC (newest pauses first — typically the
    most operator-relevant). Empty list on DB error.

    Used by the admin dashboard 'currently paused niches' card.
    """
    conn_cm = _connect()
    if conn_cm is None:
        return []
    try:
        with conn_cm as conn:
            rows = conn.execute(
                """
                SELECT niche_id, paused_until, reason, paused_by, created_at
                FROM niche_pauses
                WHERE paused_until IS NULL OR paused_until > NOW()
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [_row_to_pause(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[niche_pause] list_active_pauses failed: %s", exc)
        return []


def pause(
    niche_id: str,
    reason: str,
    *,
    paused_until: datetime | None = None,
    paused_by: str | None = None,
) -> bool:
    """Pause publishing for the niche. Idempotent INSERT-or-REPLACE.

    Returns True on successful write. Returns False on:
      * Empty niche_id (caller misuse — log + WARN)
      * Empty reason (caller misuse — every pause MUST have a reason
        for the audit log; this is enforced server-side, not just
        in the dashboard form)
      * DB unavailable / connection error
      * INSERT failure

    Fail-CLOSED: a write failure SURFACES to the operator. Silently
    dropping a pause is the worst-case outcome (operator thinks they
    stopped publishing, the system keeps going).
    """
    if not niche_id:
        logger.warning("[niche_pause] pause: empty niche_id")
        return False
    if not (reason or "").strip():
        logger.warning("[niche_pause] pause(%r): empty reason; refusing", niche_id)
        return False
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            # ON CONFLICT updates paused_until + reason + paused_by;
            # created_at stays at the FIRST pause's timestamp so
            # "paused since" UI shows the original start, not the
            # last edit. If the operator wants a fresh timer, they
            # unpause + repause.
            conn.execute(
                """
                INSERT INTO niche_pauses (niche_id, paused_until, reason, paused_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (niche_id) DO UPDATE
                SET paused_until = EXCLUDED.paused_until,
                    reason = EXCLUDED.reason,
                    paused_by = EXCLUDED.paused_by
                """,
                (niche_id, paused_until, reason.strip(), paused_by),
            )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-closed: return False
        logger.warning("[niche_pause] pause(%r) failed: %s", niche_id, exc)
        return False


def unpause(niche_id: str) -> bool:
    """Resume publishing for the niche. Idempotent — returns True
    even when no row existed (operator clicking 'resume' on an
    already-resumed niche shouldn't error).

    Fail-CLOSED: a write failure returns False. The operator's
    'resume' click failing surfaces; silently leaving a pause in
    place would be confusing ('I clicked resume, why isn't it
    publishing?').
    """
    if not niche_id:
        return False
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            conn.execute(
                "DELETE FROM niche_pauses WHERE niche_id = %s",
                (niche_id,),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[niche_pause] unpause(%r) failed: %s", niche_id, exc)
        return False
