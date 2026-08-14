"""Postgres implementations of MetricSnapshotProvider + VerificationRecordStore.

Phase 1.A of the Genius Program Roadmap. Backs the abstract protocols
in outcome_verifier.py with prod SQL. Kept separate so unit tests can
inject mock impls without touching psycopg.

## MetricSnapshotProvider

Metric resolution by `metric_name` prefix:

  * ``arm_reward:{niche}:{arm_id}`` — mean reward from bandit_arms
    row (Beta(α, β) posterior mean = α / (α + β)) for the given arm
  * ``platform_reward:{niche}:{platform}`` — 7d avg of
    pending_feedback.reward_48h for that platform
  * ``bandit_coverage:{niche}`` — fraction of niche's bandit_arms
    with n_plays >= 1 (measures exploration coverage)

Returns None for any lookup failure (unknown prefix, empty result,
DB error) — the Verifier interprets None as "unchanged" (no
rollback signal).

## VerificationRecordStore

Standard insert/update/list_pending on the table shipped by
migration `p0d1a2b3c4d5`. Uses pg_connect (tenant_context) for RLS
awareness — inserts use the record's niche_id, list_pending uses
'all' to cross-niche scan.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from genlab_core.scheduling.outcome_verifier import (
    VerificationRecord,
    Verdict,
)

logger = logging.getLogger(__name__)


class PostgresMetricSnapshotProvider:
    """Reads metric snapshots from prod tables. Fail-open (returns None
    on any error) so the verifier never crashes on a bad row."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")

    def snapshot(self, niche_id: str, metric_name: str) -> float | None:
        if not self._dsn:
            return None
        try:
            if metric_name.startswith("arm_reward:"):
                return self._arm_reward(niche_id, metric_name)
            if metric_name.startswith("platform_reward:"):
                return self._platform_reward(niche_id, metric_name)
            if metric_name.startswith("bandit_coverage:"):
                return self._bandit_coverage(niche_id)
            logger.debug(
                "[outcome_verifier] unknown metric prefix: %s", metric_name,
            )
            return None
        except Exception as exc:
            logger.warning(
                "[outcome_verifier] snapshot(%s, %s) failed: %s",
                niche_id, metric_name, exc,
            )
            return None

    def _arm_reward(self, niche_id: str, metric_name: str) -> float | None:
        """Beta(α, β) posterior mean for the specific arm.

        metric_name shape: ``arm_reward:{niche}:{arm_id}``
        arm_id may contain colons (``hook_type:anime:character_debate``),
        so split at most twice on the prefix and take the rest as arm_id.
        """
        # Strip the "arm_reward:" prefix, then the "{niche}:" prefix
        rest = metric_name[len("arm_reward:"):]
        if not rest.startswith(f"{niche_id}:"):
            return None
        arm_id = rest[len(f"{niche_id}:"):]
        if not arm_id:
            return None

        from genlab_core.storage.tenant_context import pg_connect
        with pg_connect(self._dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT alpha::float, beta::float, n_plays
                    FROM bandit_arms
                    WHERE niche_id = %s AND arm_id = %s
                    """,
                    (niche_id, arm_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        alpha = float(row[0] if not hasattr(row, "get") else row.get("alpha"))
        beta = float(row[1] if not hasattr(row, "get") else row.get("beta"))
        if alpha + beta <= 0:
            return None
        return alpha / (alpha + beta)

    def _platform_reward(self, niche_id: str, metric_name: str) -> float | None:
        """7d avg of pending_feedback.reward_48h for the platform."""
        parts = metric_name.split(":")
        if len(parts) != 3:
            return None
        platform = parts[2]

        from genlab_core.storage.tenant_context import pg_connect
        with pg_connect(self._dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT AVG(reward_48h)::float
                    FROM pending_feedback
                    WHERE niche_id = %s
                      AND platform = %s
                      AND reward_48h IS NOT NULL
                      AND created_at >= NOW() - INTERVAL '7 days'
                    """,
                    (niche_id, platform),
                )
                row = cur.fetchone()
        if not row:
            return None
        val = row[0] if not hasattr(row, "get") else row.get("avg")
        return float(val) if val is not None else None

    def _bandit_coverage(self, niche_id: str) -> float | None:
        """Fraction of arms with n_plays >= 1."""
        from genlab_core.storage.tenant_context import pg_connect
        with pg_connect(self._dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE n_plays >= 1)::float AS active,
                      COUNT(*)::float AS total
                    FROM bandit_arms
                    WHERE niche_id = %s
                    """,
                    (niche_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        active = float(row[0] if not hasattr(row, "get") else row.get("active"))
        total = float(row[1] if not hasattr(row, "get") else row.get("total"))
        if total <= 0:
            return None
        return active / total


class PostgresVerificationRecordStore:
    """Insert / update / list_pending against strategist_outcome_verification."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")

    def insert(self, record: VerificationRecord) -> None:
        if not self._dsn:
            return
        try:
            from genlab_core.storage.tenant_context import pg_connect
            with pg_connect(
                self._dsn, niche_id=record.niche_id, connect_timeout=5,
            ) as conn:
                conn.execute(
                    """
                    INSERT INTO strategist_outcome_verification (
                      proposal_id, proposal_type, proposal_target,
                      niche_id, applied_at, metric_name,
                      baseline_value, t_plus_48h_value,
                      verdict, rollback_recommended
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (proposal_id) DO NOTHING
                    """,
                    (
                        record.proposal_id, record.proposal_type,
                        record.proposal_target, record.niche_id,
                        record.applied_at, record.metric_name,
                        record.baseline_value, record.t_plus_48h_value,
                        record.verdict.value, record.rollback_recommended,
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[outcome_verifier] insert failed for %s: %s",
                record.proposal_id, exc,
            )

    def update_verdict(
        self, proposal_id: str, t_plus_48h_value: float | None,
        verdict: Verdict, rollback_recommended: bool,
    ) -> None:
        if not self._dsn:
            return
        try:
            from genlab_core.storage.tenant_context import pg_connect
            with pg_connect(self._dsn, niche_id="all", connect_timeout=5) as conn:
                conn.execute(
                    """
                    UPDATE strategist_outcome_verification
                    SET t_plus_48h_value = %s,
                        verdict = %s,
                        rollback_recommended = %s,
                        updated_at = NOW()
                    WHERE proposal_id = %s
                    """,
                    (
                        t_plus_48h_value, verdict.value,
                        rollback_recommended, proposal_id,
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning(
                "[outcome_verifier] update_verdict failed for %s: %s",
                proposal_id, exc,
            )

    def list_pending(self, older_than: datetime) -> list[VerificationRecord]:
        if not self._dsn:
            return []
        try:
            from genlab_core.storage.tenant_context import pg_connect
            with pg_connect(self._dsn, niche_id="all", connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT proposal_id, proposal_type, proposal_target,
                               niche_id, applied_at, metric_name,
                               baseline_value, t_plus_48h_value,
                               verdict, rollback_recommended, operator_notes
                        FROM strategist_outcome_verification
                        WHERE verdict = 'pending'
                          AND applied_at < %s
                        ORDER BY applied_at ASC
                        """,
                        (older_than,),
                    )
                    rows = cur.fetchall()
        except Exception as exc:
            logger.warning("[outcome_verifier] list_pending failed: %s", exc)
            return []
        records = []
        for r in rows or []:
            if hasattr(r, "get"):
                d = r
            else:
                d = dict(zip(
                    ["proposal_id", "proposal_type", "proposal_target",
                     "niche_id", "applied_at", "metric_name",
                     "baseline_value", "t_plus_48h_value",
                     "verdict", "rollback_recommended", "operator_notes"],
                    r,
                ))
            records.append(VerificationRecord(
                proposal_id=d["proposal_id"],
                proposal_type=d["proposal_type"],
                proposal_target=d["proposal_target"],
                niche_id=d["niche_id"],
                applied_at=d["applied_at"] if d["applied_at"].tzinfo
                    else d["applied_at"].replace(tzinfo=UTC),
                metric_name=d["metric_name"],
                baseline_value=d["baseline_value"],
                t_plus_48h_value=d["t_plus_48h_value"],
                verdict=Verdict(d["verdict"]),
                rollback_recommended=d["rollback_recommended"],
                operator_notes=d["operator_notes"] or "",
            ))
        return records


__all__ = [
    "PostgresMetricSnapshotProvider",
    "PostgresVerificationRecordStore",
]
