"""pipeline_alerts: add notified_at for idempotent webhook delivery

The cross-source alert notifier (scripts/notify_critical_alerts.py)
needs to track which alerts have been pushed to the operator's
webhook so a re-run doesn't double-send. Adding ``notified_at``
to the table is the cleanest path — atomic UPDATE filter, no
sidecar file to corrupt.

The column is nullable + defaults to NULL — existing rows are
"not yet notified" which is correct behaviour: when the notifier
deploys, it will catch any unresolved CRITICAL backlog on its
first run and mark them notified.

Idempotency contract: the notifier SHOULD use:

    UPDATE pipeline_alerts
    SET notified_at = NOW()
    WHERE id = ANY(%s::uuid[])
      AND notified_at IS NULL

So that concurrent notifier runs only post each alert once.

Revision ID: s9n0o1p2q3r4
Revises: r8m9n0o1p2q3
Create Date: 2026-06-16 22:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "s9n0o1p2q3r4"
down_revision: str | Sequence[str] | None = "r8m9n0o1p2q3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pipeline_alerts
        ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ;
        """
    )
    # Partial index on the notifier's hot query path (unresolved
    # CRITICAL not yet notified). Tiny: prod table is <100 rows/day.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alerts_unnotified_critical
        ON pipeline_alerts (created_at DESC)
        WHERE severity = 'CRITICAL' AND notified_at IS NULL AND resolved_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_alerts_unnotified_critical;")
    op.execute("ALTER TABLE pipeline_alerts DROP COLUMN IF EXISTS notified_at;")
