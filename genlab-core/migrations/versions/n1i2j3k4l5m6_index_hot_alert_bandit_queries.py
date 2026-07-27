"""index hot pipeline_alerts + bandit_arms queries (F-0051)

Revision ID: n1i2j3k4l5m6
Revises: m9h0i1j2k3l4
Create Date: 2026-07-27 17:30:00.000000+00:00

F-0051 baseline (audit-time): pipeline_alerts 22k seq scans / 36M rows read;
bandit_arms 9.6k / 3.3M. This session's re-check: pipeline_alerts UP to
30,260 seq scans / 51.7M rows read against 1,867 live rows; bandit_arms
12,463 seq scans / 4.4M rows read against 372 live rows.

Root query patterns driving the reads:

* ``alert_auto_resolver.py`` (6 sites): ``SELECT ... FROM pipeline_alerts
  WHERE check_name = %s AND resolved_at IS NULL ORDER BY created_at ASC``.
  Also ``anthropic_credit_monitor.py:303`` and ``hook_classifier.py:295`` and
  ``token_health.py:165`` — same shape (check_name = X AND resolved_at IS NULL,
  for existence/dedup checks).
* ``bandit_engagement.py:34``, ``late_reward.py:512``, ``strategist_actions.py``:
  ``WHERE niche_id = %s`` combined with ``max(updated_at)`` or ``ORDER BY
  updated_at``. Existing ``idx_ba_niche`` covers the niche_id equality but
  ``ORDER BY updated_at`` still triggers a sort.

Indexes added:

1. ``ix_pipeline_alerts_check_name_unresolved`` — PARTIAL btree
   ``(check_name, created_at)`` WHERE resolved_at IS NULL. Serves all 6+
   dedup/existence queries. Partial keeps the index small (resolved rows
   are the majority; the query only cares about the unresolved slice).
2. ``ix_bandit_arms_niche_updated`` — btree ``(niche_id, updated_at DESC)``.
   Serves the ``max(updated_at) WHERE niche_id`` pattern without a sort.

Both created ``CONCURRENTLY`` so no table lock on live prod. Because
``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction, this
migration uses ``op.get_context().autocommit_block()``.

Rollback: `DROP INDEX CONCURRENTLY IF EXISTS ix_pipeline_alerts_check_name_unresolved;`
         `DROP INDEX CONCURRENTLY IF EXISTS ix_bandit_arms_niche_updated;`
Or `alembic downgrade -1` (same effect via the ``downgrade()`` function).
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n1i2j3k4l5m6"
down_revision: str | Sequence[str] | None = "m9h0i1j2k3l4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY requires no wrapping transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_pipeline_alerts_check_name_unresolved "
            "ON pipeline_alerts (check_name, created_at) "
            "WHERE resolved_at IS NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_bandit_arms_niche_updated "
            "ON bandit_arms (niche_id, updated_at DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_bandit_arms_niche_updated"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_pipeline_alerts_check_name_unresolved"
        )
