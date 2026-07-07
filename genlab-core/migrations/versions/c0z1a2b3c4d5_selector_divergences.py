"""L3 PR 12a: Selector divergence persistence table

Ships the queryable-store for the divergences L3 PR 5's
`_log_selector_divergence` currently only writes to journalctl. This
unblocks L3 PR 12b (auto-flip enforcement per-niche) which needs an
analyzable data source to compute per-niche agreement rate.

## Why a separate table (not extra JSONB on affiliate_matches)

* Divergence records fire per-MATCH-ATTEMPT, not per-blueprint. A
  single caption can match multiple candidate slots (rare but
  possible). Denormalizing onto blueprints would N-plexify the
  storage.
* The volume is bounded: ≤ 1 divergence write per blueprint = ≤ 5
  writes/day at current scale. A dedicated append-only table with
  a partial index on (niche_id, created_at DESC) is cheaper than
  JSONB unnesting for the analyzer query.
* Explicit table is easier to `TRUNCATE` when the operator wants
  to reset the calibration window after a big catalog change.

## Fields

* `niche_id` — one of the 5 niches (RLS enforceable if we ever
  need per-tenant isolation)
* `matcher_pick` — product slug the keyword matcher chose
  (goes into the caption)
* `selector_pick` — product slug the bandit's ProductSelector
  would have chosen
* `selector_score` — the bandit's confidence in its pick
* `agreed` — computed at write-time so the analyzer aggregate
  is index-friendly (`SUM(agreed::int)` beats `SUM(CASE...)`)
* `created_at` — for the sliding-window agreement stats

## Fail-open at read side

L3 PR 5's `_log_selector_divergence` writes journalctl UNCONDITIONALLY.
The DB write is a best-effort ADD on top of that — if DATABASE_URL
is unset or the table doesn't exist yet, the divergence still
appears in journalctl, the matcher still returns its pick, and
nothing breaks.

This matches the AUTO #1 calibration_logger pattern (SR-C, 2026-06-13)
that's proven safe across ~2 months of operator reviews.

Revision ID: c0z1a2b3c4d5
Revises: b9y0z1a2b3c4
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c0z1a2b3c4d5"
down_revision = "b9y0z1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "selector_divergences",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("niche_id", sa.Text(), nullable=False),
        sa.Column("matcher_pick", sa.Text(), nullable=False),
        sa.Column("selector_pick", sa.Text(), nullable=False),
        sa.Column("selector_score", sa.Float(), nullable=True),
        # Computed at write time — analyzer just SUMs this column.
        sa.Column("agreed", sa.Boolean(), nullable=False),
        # Optional metadata — surfaces which blueprint triggered the
        # divergence for debug drill-down. Nullable because the wire
        # doesn't currently pass blueprint_id through (would be nice
        # to add in a follow-up, but not required for the stats path).
        sa.Column(
            "blueprint_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Query index — the analyzer's hot path is:
    #   SELECT niche_id, COUNT(*), SUM(agreed::int)
    #   FROM selector_divergences
    #   WHERE niche_id = %s AND created_at > NOW() - INTERVAL '7 days'
    #   GROUP BY niche_id
    #
    # A composite (niche_id, created_at DESC) matches exactly.
    op.create_index(
        "idx_selector_divergences_niche_time",
        "selector_divergences",
        ["niche_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_selector_divergences_niche_time",
        table_name="selector_divergences",
    )
    op.drop_table("selector_divergences")
