"""drift_signals — persist bandit-arm drift detection results (L7, #602)

Revision ID: g3c4d5e6f7g8
Revises: f2b3c4d5e6f7
Create Date: 2026-07-08 16:35:00.000000+00:00

## Background

`genlab_core.learning.drift_detector.detect_all_drift` currently
prints signals to stdout when the CLI runs, but nothing persists.
Operators lose the historical view — "did gaming's style bandit
drift last week?" requires re-running the detector against the
snapshot dir, and even then only current data is visible.

The L7 audit finding: without persistence, we can't build a trend
line ("regressions per week for gaming") and can't fire alerts on
sustained regression across multiple detector runs.

## Schema

Sibling to `ensemble_votes` (L5, migration `f2b3c4d5e6f7`) — same
denormalized single-table pattern. `computed_at` is the value from
DriftSignal (when the drift was detected); `detected_at` is when we
wrote the row. Usually within seconds of each other, but separating
them lets us tell "this signal was surfaced by an old detector run
we re-ingested" from "this is fresh".

    drift_signals(
      id             BIGSERIAL PK
      arm_id         TEXT NOT NULL
      niche_id       TEXT NOT NULL
      recent_rate    REAL NOT NULL
      baseline_rate  REAL NOT NULL
      z_score        REAL NOT NULL
      recent_obs     INT NOT NULL
      baseline_obs   INT NOT NULL
      is_regression  BOOLEAN NOT NULL
      computed_at    TIMESTAMPTZ NOT NULL
      detected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )

## Indexes

* `(niche_id, computed_at DESC)` — per-niche recent-signals endpoint
* `(arm_id, computed_at DESC)` — future "history of one arm" query
* Partial index on `WHERE is_regression = true` — most operator
  queries only care about regressions; the partial index keeps the
  hot query cheap.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# ── Alembic identifiers ─────────────────────────────────────────────
revision = "g3c4d5e6f7g8"
down_revision = "f2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drift_signals",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("arm_id", sa.Text(), nullable=False),
        sa.Column("niche_id", sa.Text(), nullable=False),
        sa.Column("recent_rate", sa.REAL(), nullable=False),
        sa.Column("baseline_rate", sa.REAL(), nullable=False),
        sa.Column("z_score", sa.REAL(), nullable=False),
        sa.Column("recent_obs", sa.Integer(), nullable=False),
        sa.Column("baseline_obs", sa.Integer(), nullable=False),
        # Denormalized from `z_score < 0 AND |z_score| >= threshold`.
        # Stored so the partial index below can be `WHERE is_regression`.
        sa.Column("is_regression", sa.Boolean(), nullable=False),
        # From the DriftSignal itself — when the module computed the
        # signal. Passed in as ISO string; we convert to TIMESTAMPTZ.
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        # When THIS row was written. `now()` = server-side, so the
        # writer doesn't have to send a timestamp.
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_drift_signals_niche_time",
        "drift_signals",
        ["niche_id", sa.text("computed_at DESC")],
    )
    op.create_index(
        "idx_drift_signals_arm_time",
        "drift_signals",
        ["arm_id", sa.text("computed_at DESC")],
    )
    # Partial index — regression queries dominate. Postgres can use
    # this even for `WHERE is_regression AND niche_id = 'gaming'` if
    # the planner decides it's selective enough.
    op.create_index(
        "idx_drift_signals_regressions",
        "drift_signals",
        [sa.text("computed_at DESC")],
        postgresql_where=sa.text("is_regression = true"),
    )


def downgrade() -> None:
    op.drop_index("idx_drift_signals_regressions", table_name="drift_signals")
    op.drop_index("idx_drift_signals_arm_time", table_name="drift_signals")
    op.drop_index("idx_drift_signals_niche_time", table_name="drift_signals")
    op.drop_table("drift_signals")
