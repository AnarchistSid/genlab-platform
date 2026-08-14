"""credential_rotation_state (Phase 2.E of the Genius Program)

Revision ID: p2e3f4g5h6i7
Revises: p2c3d4e5f6g7
Create Date: 2026-08-14 13:35:00.000000+00:00

Rule #33 SaaS blocker. Records rotation status for each secret in
inventory so operator can see at a glance which credentials are
overdue.

## What ships in v1

  * The TABLE + operator-runnable status script
  * NOT actual auto-rotation — that's per-service surgery (Meta
    tokens use permanent EAA page tokens; YT Data API keys need
    Google Cloud Console access; DB role rotation needs coordinated
    downtime plan)

Autonomous rotation is a follow-up per-service — this table gives
each rotator a place to record its state.
"""
from alembic import op

revision = "p2e3f4g5h6i7"
down_revision = "p2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS credential_rotation_state (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          credential_name       TEXT NOT NULL UNIQUE,
          service               TEXT NOT NULL,
          rotation_interval_days INTEGER NOT NULL DEFAULT 90,
          last_rotated_at       TIMESTAMPTZ,
          next_rotation_due_at  TIMESTAMPTZ,
          rotation_source       TEXT NOT NULL DEFAULT 'manual',
          notes                 TEXT NOT NULL DEFAULT '',
          created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_credential_rotation_due
        ON credential_rotation_state (next_rotation_due_at ASC)
        WHERE next_rotation_due_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS credential_rotation_state CASCADE")
