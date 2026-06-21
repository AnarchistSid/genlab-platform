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
from datetime import datetime  # noqa: F401 — used in type-only annotation

from genlab_core.scheduling.auto_approval_gate import AutoApprovalDecision

logger = logging.getLogger(__name__)

# Valid operator actions. Must match the canonical past-tense forms
# emitted by review_server._execute_review_action.
VALID_OPERATOR_ACTIONS = frozenset({"approved", "rejected", "revised", "skipped"})

# Source-tag values that indicate the action was NOT a fresh operator
# decision. When an operator clicks reject on a blueprint that an
# earlier auto_approver run already set to approved, the calibration
# row would be gate-vs-gate (the gate evaluated, the worker acted on
# the gate's verdict, then the gate re-evaluates here) — circular and
# misleading. Filter at the writer so the confusion matrix stays
# clean. (S1 fix, 2026-06-15.)
#
# Mirrors ``auto_approver.AUTO_APPROVAL_SOURCE_TAG``. Adding a new
# auto-source (e.g. ``auto_approver_v2``) means adding it here too —
# kept as a literal frozenset rather than an import to avoid the
# circular dependency calibration_logger → auto_approver.
_NON_OPERATOR_SOURCE_TAGS: frozenset[str] = frozenset(
    {
        "auto_approver_v1",
    }
)


@dataclass(frozen=True)
class CalibrationStats:
    """Per-niche agreement-rate summary for the AUTO #2 readiness check."""

    niche_id: str
    sample_count: int
    agreement_count: int
    # Confusion-matrix cells. "gate said X, operator said Y"
    true_positives: int  # gate=approve, op=approved
    true_negatives: int  # gate=reject,  op in {rejected, revised, skipped}
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
    decided_at: datetime | None = None,
    action_taken_source: str | None = None,
    review_duration_ms: int | None = None,
) -> bool:
    """Write one calibration row. Best-effort, never raises.

    Returns True if the row was written, False on any failure
    (including missing DATABASE_URL — see module docstring).

    Args:
        decided_at: When the operator made the decision. Defaults to
            ``NOW()`` via the schema's column default — pass an
            explicit timestamp ONLY when backfilling historical rows
            (e.g. AUTO #2 D1.2 backfill from ``dashboard_events``).
            Passing ``None`` keeps the long-standing fail-open default
            so live operator clicks don't need to compute a timestamp.
        action_taken_source: The blueprint's ``action_taken_source``
            BEFORE the current operator click. When this matches an
            entry in ``_NON_OPERATOR_SOURCE_TAGS`` (e.g.
            ``auto_approver_v1``), the row is NOT written — the
            action isn't a fresh operator decision but a correction
            to an earlier auto-approval, which would contaminate the
            confusion matrix as gate-vs-gate. Pass ``None`` (default)
            when the caller doesn't know the source — preserves the
            pre-S1 behavior for callers that haven't been wired yet.
        review_duration_ms: How long the operator spent on this review
            (frontend captures ``submitTime - loadTime`` in milliseconds).
            Lever E2 (2026-06-21) added this signal so we can
            distinguish fast confident decisions from slow ambiguous
            ones. Pass ``None`` when not known — column accepts NULL
            and downstream aggregations filter via
            ``WHERE review_duration_ms IS NOT NULL``. Negative values
            and absurdly-large values (>1 hour) are clamped to NULL
            to defend against clock-skew + tab-left-open noise.
    """
    if not blueprint_id or not niche_id:
        logger.warning(
            "[calibration] skipping log — empty blueprint_id or niche_id (bp=%r niche=%r)",
            blueprint_id,
            niche_id,
        )
        return False
    if operator_action not in VALID_OPERATOR_ACTIONS:
        logger.warning(
            "[calibration] skipping log — invalid operator_action %r (expected one of %s)",
            operator_action,
            sorted(VALID_OPERATOR_ACTIONS),
        )
        return False
    # S1 (2026-06-15): drop gate-vs-gate rows. When the blueprint was
    # previously auto-approved by the worker, the operator's later
    # action isn't a fresh decision — it's a correction. Mixing
    # those into the confusion matrix inflates apparent disagreement
    # and under-counts true operator-vs-gate signal.
    if action_taken_source and action_taken_source in _NON_OPERATOR_SOURCE_TAGS:
        logger.info(
            "[calibration] skipping log — action_taken_source=%r is a non-operator "
            "source (gate-vs-gate guard)",
            action_taken_source,
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
        import psycopg  # noqa: F401 — kept for the except above
    except ImportError:
        logger.warning("[calibration] psycopg not installed, skipping write")
        return False

    # SR-A/C/D Tier-1 migration (2026-06-17): route through pg_connect
    # so the RLS GUC is set from niche_id. Behaviour preserved (Phase-1
    # admin-mode fallback when env flag unset).
    from genlab_core.storage.tenant_context import pg_connect

    gate_approved = decision.approved if decision is not None else None
    gate_confidence = decision.confidence if decision is not None else None
    passed = json.dumps(decision.passed_checks) if decision is not None else "[]"
    failed = json.dumps(decision.failed_checks) if decision is not None else "[]"

    # Lever E2 (2026-06-21): clamp the dwell-time signal. Defend against:
    # - Negative values from clock skew or buggy frontend instrumentation
    # - Absurdly-large values from "tab left open overnight" (>1 hour
    #   = 3,600,000 ms is the cutoff — operator left mid-review)
    # NULL is the natural cold-start value for rows from callers that
    # haven't been wired yet (backfill, dashboards without frontend
    # instrumentation).
    clean_duration_ms: int | None
    if review_duration_ms is None:
        clean_duration_ms = None
    else:
        try:
            n = int(review_duration_ms)
            clean_duration_ms = n if 0 <= n <= 3_600_000 else None
        except (TypeError, ValueError):
            clean_duration_ms = None

    try:
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # Two INSERT variants: the live path lets the column's
                # ``DEFAULT now()`` set decided_at; the backfill path
                # supplies the historical operator-click timestamp.
                # Keeping the live shape untouched preserves the
                # operator-review fast path (NOW() is cheaper than a
                # round-trip to Python's datetime serializer).
                if decided_at is None:
                    # 2026-06-15 audit T#57: ON CONFLICT DO NOTHING
                    # against the new unique index
                    # uq_calibration_no_dupe_within_second. Defense in
                    # depth: a frontend retry / dual-route fire / React
                    # Query 500-retry can't produce duplicate rows.
                    cur.execute(
                        """
                        INSERT INTO auto_approval_calibration
                            (blueprint_id, niche_id,
                             gate_approved, gate_confidence,
                             gate_passed_checks, gate_failed_checks,
                             operator_action, review_duration_ms)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            str(blueprint_id),
                            str(niche_id),
                            gate_approved,
                            gate_confidence,
                            passed,
                            failed,
                            operator_action,
                            clean_duration_ms,
                        ),
                    )
                else:
                    # Backfill path — same ON CONFLICT guard.
                    cur.execute(
                        """
                        INSERT INTO auto_approval_calibration
                            (blueprint_id, niche_id,
                             gate_approved, gate_confidence,
                             gate_passed_checks, gate_failed_checks,
                             operator_action, decided_at,
                             review_duration_ms)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            str(blueprint_id),
                            str(niche_id),
                            gate_approved,
                            gate_confidence,
                            passed,
                            failed,
                            operator_action,
                            decided_at,
                            clean_duration_ms,
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
        import psycopg  # noqa: F401 — kept for the except above
    except ImportError:
        return empty

    # SR-A/C/D Tier-1 migration (2026-06-17): route through pg_connect.
    # ``stats`` is a read path scoped to ``niche_id`` already (the
    # SELECT has WHERE niche_id = %s), but adding the GUC is still
    # right — RLS protects against a malformed query.
    from genlab_core.storage.tenant_context import pg_connect

    try:
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
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


def stats_all_niches(*, window_days: int = 7) -> dict[str, CalibrationStats]:
    """Batch variant of :func:`stats` — returns per-niche stats in ONE query.

    The Mission Control dashboard polls calibration stats for all 5 niches
    every 60s. Without this batch endpoint that's 5 separate HTTP requests
    + 5 separate SQL queries per poll = 300 round-trips/hour just for the
    calibration card. With this batch endpoint it collapses to 1 + 1 = 60/hr.

    Uses the same SQL aggregation but GROUP BY niche_id instead of WHERE
    niche_id = %s. Returns a dict keyed by niche_id. Niches with zero
    calibration samples are NOT returned by the query — caller should
    fill empty :class:`CalibrationStats` for missing niches.

    Returns {} on any error (cold start, no DB, table missing) — matches
    the fail-quiet contract of :func:`stats`. Caller decides how to
    surface "no data".
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return {}
    try:
        import psycopg  # noqa: F401 — kept for the except above
    except ImportError:
        return {}

    from genlab_core.storage.tenant_context import pg_connect

    try:
        # Admin-mode RLS — query spans all niches. The SELECT is read-only
        # aggregation; no per-niche RLS GUC needed.
        with pg_connect(dsn, niche_id="all", connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        niche_id,
                        COUNT(*) FILTER (WHERE gate_approved IS NOT NULL) AS sample_count,
                        COUNT(*) FILTER (
                            WHERE gate_approved = true
                              AND operator_action = 'approved'
                        ) AS true_positives,
                        COUNT(*) FILTER (
                            WHERE gate_approved = false
                              AND operator_action IN ('rejected','revised','skipped')
                        ) AS true_negatives,
                        COUNT(*) FILTER (
                            WHERE gate_approved = true
                              AND operator_action IN ('rejected','revised','skipped')
                        ) AS false_positives,
                        COUNT(*) FILTER (
                            WHERE gate_approved = false
                              AND operator_action = 'approved'
                        ) AS false_negatives
                    FROM auto_approval_calibration
                    WHERE decided_at >= NOW() - (%s || ' days')::interval
                    GROUP BY niche_id
                    """,
                    (str(window_days),),
                )
                rows = cur.fetchall()
        result: dict[str, CalibrationStats] = {}
        for row in rows:
            niche_id = row[0]
            sample_count = int(row[1] or 0)
            tp = int(row[2] or 0)
            tn = int(row[3] or 0)
            fp = int(row[4] or 0)
            fn = int(row[5] or 0)
            result[niche_id] = CalibrationStats(
                niche_id=niche_id,
                sample_count=sample_count,
                agreement_count=tp + tn,
                true_positives=tp,
                true_negatives=tn,
                false_positives=fp,
                false_negatives=fn,
            )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] stats_all_niches query failed: %s", exc)
        return {}
