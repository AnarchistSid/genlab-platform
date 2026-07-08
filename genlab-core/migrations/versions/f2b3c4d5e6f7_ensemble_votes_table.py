"""ensemble_votes — persist per-component votes for each decision (L5, #601)

Revision ID: f2b3c4d5e6f7
Revises: e1a2b3c4d5e6
Create Date: 2026-07-08 16:30:00.000000+00:00

## Background

Ensemble decision-making (Intervention 6, 2026-07-01) currently
computes a per-blueprint recommendation from 4-5 components (bandit
posterior, hook_classifier, bayesian_gate, conformal_router, LLM
judge) but **discards the per-component votes**. Only the aggregate
`recommendation` string is surfaced to the operator via the badge on
Focus Review.

The 2026-07-08 audit's L5 finding: without persisting the individual
votes, we can't do post-hoc analysis of WHICH component pushed the
ensemble toward a particular decision, we can't measure inter-
component agreement rates, and we can't calibrate the weights based
on which component predicts operator behavior best.

## Schema

Single table with denormalized aggregate fields so a query for
"decisions where component X disagreed with the ensemble" is one
WHERE clause instead of a self-join:

    ensemble_votes(
      id                        BIGSERIAL PK
      blueprint_id              TEXT NOT NULL
      niche_id                  TEXT NOT NULL
      decided_at                TIMESTAMPTZ NOT NULL DEFAULT now()
      component                 TEXT NOT NULL   -- 'bandit' | 'hook_classifier' | ...
      component_score           REAL            -- NULL when component abstained
      component_weight          REAL NOT NULL
      component_reason          TEXT
      ensemble_score            REAL            -- aggregate at decision time
      ensemble_disagreement     REAL NOT NULL
      ensemble_recommendation   TEXT NOT NULL
      n_voters                  INT NOT NULL
    )

Indexed on `(niche_id, decided_at DESC)` so the observability endpoint
can do "last N days per niche" without a full-table scan, and on
`(component, decided_at DESC)` so "how often did the bandit abstain?"
is fast.

## Fail-open discipline

The write path is best-effort. If Postgres is down or the table is
temporarily missing (e.g. during migration), the caller MUST still
return the decision to the reviewer. Persistence is a telemetry
concern, not a correctness gate.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# ── Alembic identifiers ─────────────────────────────────────────────
revision = "f2b3c4d5e6f7"
down_revision = "e1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ensemble_votes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("blueprint_id", sa.Text(), nullable=False),
        sa.Column("niche_id", sa.Text(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Component name. Deliberately not a CHECK-constrained enum
        # because a future component add would need a migration; a
        # free-text column + application-side validation is friendlier
        # for the observability-first cadence.
        sa.Column("component", sa.Text(), nullable=False),
        # NULL when the component abstained. Downstream aggregation
        # excludes NULLs from mean / variance calcs (SQL AVG/VAR skip
        # NULLs natively, matching the module's aggregation rule).
        sa.Column("component_score", sa.REAL(), nullable=True),
        sa.Column("component_weight", sa.REAL(), nullable=False),
        # Free-text audit trail from the vote. Bounded by the module's
        # own reasons — expected max ~200 chars, but TEXT is cheap.
        sa.Column("component_reason", sa.Text(), nullable=True),
        # Aggregate fields — denormalized so a per-decision query is
        # one filter instead of a group-by + join back onto votes.
        sa.Column("ensemble_score", sa.REAL(), nullable=True),
        sa.Column("ensemble_disagreement", sa.REAL(), nullable=False),
        sa.Column("ensemble_recommendation", sa.Text(), nullable=False),
        sa.Column("n_voters", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_ensemble_votes_niche_time",
        "ensemble_votes",
        ["niche_id", sa.text("decided_at DESC")],
    )
    op.create_index(
        "idx_ensemble_votes_component_time",
        "ensemble_votes",
        ["component", sa.text("decided_at DESC")],
    )
    # Support "all votes for one blueprint" — used by the Focus Review
    # detailed-audit drill-down (future PR). Covering here means the
    # follow-up doesn't need its own migration.
    op.create_index(
        "idx_ensemble_votes_blueprint",
        "ensemble_votes",
        ["blueprint_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_ensemble_votes_blueprint", table_name="ensemble_votes")
    op.drop_index(
        "idx_ensemble_votes_component_time", table_name="ensemble_votes"
    )
    op.drop_index(
        "idx_ensemble_votes_niche_time", table_name="ensemble_votes"
    )
    op.drop_table("ensemble_votes")
