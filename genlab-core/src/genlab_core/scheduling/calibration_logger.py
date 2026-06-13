"""Auto-approval calibration logger.

AUTO #1b (2026-06-13): Captures the (gate verdict, operator action)
pairing every time an operator clicks approve/reject/revise/skip.

This data is the unblocker for AUTO #2 (enforcement). Without it we
can't measure whether the AutoApprovalGate's decisions match what
operators actually do, and therefore can't justify flipping the
opt-in switch in publishing.yaml.

Design choices:

- **Synchronous + best-effort fail-open**: Failure to write the
  calibration row must NEVER block the operator's review action.
  All exceptions are caught and logged at WARNING; the operator's
  approval still goes through.
- **Single row per (blueprint_id, operator_action)**: We don't
  dedupe in the writer — if the same blueprint is reviewed twice
  (rare; happens if Esc + reopen), we get two rows. The stats
  endpoint can window/dedupe by blueprint_id DESC if needed.
- **Direct psycopg connection, no pool**: Writes are ~5/day across
  all niches. A pool's overhead exceeds its value at this volume.
- **Postgres-only**: Calibration data has no SharePoint equivalent.
  If DATABASE_URL is unset, log() is a no-op (warning logged once).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from genlab_core.scheduling.auto_approval_gate import AutoApprovalDecision

logger = logging.getLogger(__name__)

# Valid operator actions. Must match the canonical past-tense forms
# emitted by review_server._execute_review_action.
VALID_OPERATOR_ACTIONS = frozenset({"approved", "rejected", "revised", "skipped"})


@dataclass(frozen=True)
class CalibrationStats:
    """Per-niche agreement-rate summary for the AUTO #2 readiness check."""

    niche_id: str
    sample_count: int
    agreement_count: int
    # Confusion-matrix cells. "gate said X, operator said Y"
    true_positives: int   # gate=approve, op=approved
    true_negatives: int   # gate=reject,  op in {rejected, revised, skipped}
    false_positives: int  # gate=approve, op in {rejected, revised, skipped}
    false_negatives: int  # gate=reject,  op=approved

    @property
    def agreement_rate(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return self.agreement_count / self.sample_count

    @property
    def ready_for_enforcement(self) -> bool:
        """Heuristic: AUTO #2 should remain off until we have ≥30
        samples AND agreement ≥90%. These thresholds are conservative;
        per-niche overrides may be needed."""
        return self.sample_count >= 30 and self.agreement_rate >= 0.90


def log(
    *,
    blueprint_id: str,
    niche_id: str,
    decision: AutoApprovalDecision | None,
    operator_action: str,
) -> bool:
    """Write one calibration row. Best-effort, never raises.

    Returns True if the row was written, False on any failure
    (including missing DATABASE_URL — see module docstring).
    """
    if not blueprint_id or not niche_id:
        logger.warning(
            "[calibration] skipping log — empty blueprint_id or niche_id "
            "(bp=%r niche=%r)",
            blueprint_id,
            niche_id,
        )
        return False
    if operator_action not in VALID_OPERATOR_ACTIONS:
        logger.warning(
            "[calibration] skipping log — invalid operator_action %r "
            "(expected one of %s)",
            operator_action,
            sorted(VALID_OPERATOR_ACTIONS),
        )
        return False

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        # Logged at debug, not warning, because non-prod environments
        # (CI, tests, local dev without DB) shouldn't see noise.
        logger.debug("[calibration] DATABASE_URL not set, skipping write")
        return False

    # Lazy import: psycopg shouldn't be required when calibration is unused.
    try:
        import psycopg
    except ImportError:
        logger.warning("[calibration] psycopg not installed, skipping write")
        return False

    gate_approved = decision.approved if decision is not None else None
    gate_confidence = decision.confidence if decision is not None else None
    passed = json.dumps(decision.passed_checks) if decision is not None else "[]"
    failed = json.dumps(decision.failed_checks) if decision is not None else "[]"

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auto_approval_calibration
                        (blueprint_id, niche_id,
                         gate_approved, gate_confidence,
                         gate_passed_checks, gate_failed_checks,
                         operator_action)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    """,
                    (
                        str(blueprint_id),
                        str(niche_id),
                        gate_approved,
                        gate_confidence,
                        passed,
                        failed,
                        operator_action,
                    ),
                )
            conn.commit()
        logger.info(
            "[calibration] logged bp=%s niche=%s gate=%s op=%s",
            blueprint_id,
            niche_id,
            "approve" if gate_approved else ("reject" if gate_approved is False else "err"),
            operator_action,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — must NEVER raise to caller
        logger.warning(
            "[calibration] write failed for bp=%s: %s",
            blueprint_id,
            exc,
        )
        return False


def stats(*, niche_id: str, window_days: int = 7) -> CalibrationStats:
    """Compute agreement-rate stats for a niche over a rolling window.

    Returns zeros + ready_for_enforcement=False on any error (cold
    start, no DB, table missing). Callers should not gate enforcement
    on "ready_for_enforcement" alone — operator confirmation required.
    """
    empty = CalibrationStats(
        niche_id=niche_id,
        sample_count=0,
        agreement_count=0,
        true_positives=0,
        true_negatives=0,
        false_positives=0,
        false_negatives=0,
    )
    if not niche_id:
        return empty
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return empty
    try:
        import psycopg
    except ImportError:
        return empty
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE gate_approved IS NOT NULL) AS sample_count,
                        -- TP: gate=approve, op=approved
                        COUNT(*) FILTER (
                            WHERE gate_approved = true
                              AND operator_action = 'approved'
                        ) AS true_positives,
                        -- TN: gate=reject, op in (rejected, revised, skipped)
                        COUNT(*) FILTER (
                            WHERE gate_approved = false
                              AND operator_action IN ('rejected','revised','skipped')
                        ) AS true_negatives,
                        -- FP: gate=approve, op in (rejected, revised, skipped)
                        COUNT(*) FILTER (
                            WHERE gate_approved = true
                              AND operator_action IN ('rejected','revised','skipped')
                        ) AS false_positives,
                        -- FN: gate=reject, op=approved
                        COUNT(*) FILTER (
                            WHERE gate_approved = false
                              AND operator_action = 'approved'
                        ) AS false_negatives
                    FROM auto_approval_calibration
                    WHERE niche_id = %s
                      AND decided_at >= NOW() - (%s || ' days')::interval
                    """,
                    (niche_id, str(window_days)),
                )
                row = cur.fetchone() or (0, 0, 0, 0, 0)
                sample_count = int(row[0] or 0)
                tp = int(row[1] or 0)
                tn = int(row[2] or 0)
                fp = int(row[3] or 0)
                fn = int(row[4] or 0)
                agreement = tp + tn
                return CalibrationStats(
                    niche_id=niche_id,
                    sample_count=sample_count,
                    agreement_count=agreement,
                    true_positives=tp,
                    true_negatives=tn,
                    false_positives=fp,
                    false_negatives=fn,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] stats query failed for %s: %s", niche_id, exc)
        return empty
