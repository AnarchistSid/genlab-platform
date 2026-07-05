"""late_reward_deltas — Intervention 1 audit table (was lazily created)

Revision ID: z7v8w9x0y1z2
Revises: y6t7u8v9w0x1
Create Date: 2026-07-02 17:00:00.000000+00:00

Promotes ``late_reward_deltas`` from lazy-runtime creation to a
first-class alembic-managed table.

## Background

``late_reward.py`` (Intervention 1, ships as ``recompute_late_rewards.py``
daily systemd timer) has always been persisting delta rows via a
``CREATE TABLE IF NOT EXISTS`` on every insert — see
``late_reward.py:_persist_delta_row``. That pattern bypasses alembic's
version control and creates two class of subtle failure:

1. If the schema ever needs to change, the runtime CREATE would still
   produce the OLD shape until the code was updated everywhere. An
   alembic-managed migration is the single source of truth for column
   shape.

2. Schema-inventory tests (like ``test_promoted_columns_drift.py``)
   don't see lazy-created tables. Any drift in column names between
   what code writes and what other code reads would be invisible.

## What this migration does

Creates the table with the exact schema the lazy CREATE was using,
plus three indexes the lazy version didn't have:

* ``idx_late_reward_deltas_niche_measured`` — ``(niche_id, measured_at
  DESC)``. Supports ``ORDER BY measured_at DESC WHERE niche_id = X``
  queries that Strategist and Mission Control cards do.

* ``idx_late_reward_deltas_arm`` — ``(niche_id, arm_id)``. Supports
  per-arm rollups: "which arms show most late-tail lift?"

* ``idx_late_reward_deltas_significant`` — partial index
  ``WHERE abs(delta_pct) > 0.20``. The bandit-push path only reads
  significant-lift rows; a partial index keeps that path fast even
  as the audit table grows.

The runtime CREATE in ``late_reward.py`` becomes redundant after
this migration lands but is retained as belt-and-suspenders — if
someone runs the systemd timer before the migration completes on a
fresh env, the runtime CREATE still populates the table.

## Downgrade

Destructive — DROP TABLE wipes all late-window audit history. This
data is telemetry-only (never fed back into bandit posteriors
without the ``GENLAB_MULTI_WINDOW_REWARD_ENABLED`` flag; and even
then, the pushed alpha/beta are on ``bandit_arms``, not here). Loss
on downgrade is acceptable.
"""

from alembic import op

revision = "z7v8w9x0y1z2"
down_revision = "y6t7u8v9w0x1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS late_reward_deltas (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          blueprint_id  UUID NOT NULL,
          niche_id      TEXT NOT NULL,
          arm_id        TEXT,
          platform      TEXT NOT NULL,
          reward_48h    DOUBLE PRECISION NOT NULL,
          reward_late   DOUBLE PRECISION NOT NULL,
          window_days   INTEGER NOT NULL DEFAULT 7,
          delta         DOUBLE PRECISION NOT NULL,
          delta_pct     DOUBLE PRECISION NOT NULL,
          measured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CONSTRAINT unique_bp_window UNIQUE (blueprint_id, platform, window_days)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_late_reward_deltas_niche_measured
        ON late_reward_deltas (niche_id, measured_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_late_reward_deltas_arm
        ON late_reward_deltas (niche_id, arm_id)
        WHERE arm_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_late_reward_deltas_significant
        ON late_reward_deltas (niche_id, measured_at DESC)
        WHERE abs(delta_pct) > 0.20
        """
    )


def downgrade() -> None:
    # Destructive on rollback — telemetry history lost. See module docstring.
    op.execute("DROP TABLE IF EXISTS late_reward_deltas")
