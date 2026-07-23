"""Persist AutoApprovalGate examinations to gate_examinations table.

Every auto-approver fire evaluates one or more blueprints. This
module logs the gate's verdict PLUS the per-check pass/fail detail
regardless of whether the operator ever reviews.

Motivating problem pinned in the migration docstring: on prod
2026-07-23 auto-approver reports ``examined=1 approved=0
rejected=1`` for every enabled niche. The AUTO #2 ratchet has no
tuning target because operator can't see WHICH gate check is
rejecting them. This diagnostic layer fills that gap.

Discipline:
* Fail-open at every DB error path — logging the examination MUST
  NOT block the auto-approval flow. If the log fails, the operator
  loses a diagnostic sample; the ratchet keeps advancing.
* Every call site passes the SAME AutoApprovalDecision the gate
  returned (post-strategy adjustment). ``pre_strategy_confidence``
  can be passed in ``extra`` to distinguish rule-based reject from
  strategy-flipped reject.
* No rate limit — writes are cheap relative to a gate evaluation,
  and the operator's read-side dashboard needs every sample.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Final

logger = logging.getLogger(__name__)


DEFAULT_CALLER_SOURCE: Final[str] = "auto_approver_v1"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def log(
    *,
    blueprint_id: str,
    niche_id: str,
    decision: Any,
    caller_source: str = DEFAULT_CALLER_SOURCE,
    extra: dict[str, Any] | None = None,
    conn: Any = None,
) -> bool:
    """Persist one gate examination.

    Args:
        blueprint_id: The blueprint the gate ran on.
        niche_id: One of the 5 canonical niche IDs.
        decision: An AutoApprovalDecision-shaped object with fields
            ``approved``, ``confidence``, ``passed_checks``,
            ``failed_checks``. Duck-typed via ``getattr`` so tests
            can pass a stub.
        caller_source: Which caller ran the gate. Defaults to
            ``auto_approver_v1``.
        extra: Optional JSON-serializable dict merged into
            ``extra`` column.
        conn: Optional existing psycopg connection. When None,
            opens a new connection via DATABASE_URL. Passing an
            existing conn is useful for tests and for callers who
            already hold one (avoids connection churn during the
            auto-approver's tight loop).

    Returns:
        True on write success, False on any failure. Never raises.
    """
    if not blueprint_id or not niche_id:
        logger.debug(
            "[gate_examination] skip — empty blueprint_id or niche_id "
            "(bp=%r niche=%r)",
            blueprint_id,
            niche_id,
        )
        return False

    try:
        approved = bool(getattr(decision, "approved", False))
        confidence = getattr(decision, "confidence", None)
        # Coerce to float when set; None passes through so the column
        # can be NULL.
        confidence = float(confidence) if confidence is not None else None
        passed_checks = list(getattr(decision, "passed_checks", []) or [])
        failed_checks = list(getattr(decision, "failed_checks", []) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[gate_examination] decision shape unexpected (bp=%s): %s",
            blueprint_id,
            exc,
        )
        return False

    payload_extra = extra or {}
    sql = """
        INSERT INTO gate_examinations
            (blueprint_id, niche_id, approved, confidence,
             passed_checks, failed_checks, caller_source, extra)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)
    """
    params = (
        str(blueprint_id),
        str(niche_id),
        approved,
        confidence,
        json.dumps(passed_checks),
        json.dumps(failed_checks),
        caller_source,
        json.dumps(payload_extra),
    )

    if conn is not None:
        try:
            conn.execute(sql, params)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[gate_examination] insert failed (bp=%s conn-passed): %s",
                blueprint_id,
                exc,
            )
            return False

    dsn = _dsn()
    if not dsn:
        # No DSN in this env — CLI dry-run or tests. Silently no-op
        # (matches calibration_logger's convention).
        logger.debug(
            "[gate_examination] no DATABASE_URL — silently skipped bp=%s",
            blueprint_id,
        )
        return False

    try:
        import psycopg

        with psycopg.connect(dsn) as new_conn:
            new_conn.execute(sql, params)
            new_conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        # Fail-open. Auto-approver continues; operator loses one
        # diagnostic row.
        logger.warning(
            "[gate_examination] insert failed (bp=%s open-conn): %s",
            blueprint_id,
            exc,
        )
        return False


__all__ = ["DEFAULT_CALLER_SOURCE", "log"]
