"""portfolio_allocations table (Phase 2.B of the Genius Program)

Revision ID: p2b3a4b5c6d7
Revises: p1c2d3e4f5g6
Create Date: 2026-08-14 12:35:00.000000+00:00

Records portfolio-level bandit decisions per week. One row per
(week_of, niche_id) = 5 rows per weekly run.

## Why

Today's per-niche bandits decide "which arm within niche X to pull."
Nothing decides "how much effort to spend on niche X vs niche Y."
Portfolio bandit fills that gap — 5-arm LinUCB (one per niche)
that recommends budget allocation based on follower growth rate,
engagement percentile, conversion, and cost.

Observation-only in v1: the runner writes the recommended weights,
the consumer (pipeline_runner adaptor) is deferred until operator
eyeballs the numbers for 2+ weeks.

## Shape

  * week_of + niche_id — composite PK
  * recommended_weight — 0..1 fraction (all 5 sum to 1.0 per week)
  * context_features — JSONB snapshot of what the bandit saw
  * ucb_score — LinUCB confidence bound at decision time
  * applied — bool marker; stays FALSE until consumer wire ships
"""
from alembic import op

revision = "p2b3a4b5c6d7"
down_revision = "p1c2d3e4f5g6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_allocations (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          week_of               DATE NOT NULL,
          niche_id              TEXT NOT NULL,
          recommended_weight    DOUBLE PRECISION NOT NULL,
          ucb_score             DOUBLE PRECISION,
          context_features      JSONB NOT NULL DEFAULT '{}'::jsonb,
          applied               BOOLEAN NOT NULL DEFAULT FALSE,
          reward_realized       DOUBLE PRECISION,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CONSTRAINT portfolio_allocations_week_niche_unique
            UNIQUE (week_of, niche_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_portfolio_alloc_recent
        ON portfolio_allocations (week_of DESC)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS portfolio_allocations CASCADE"
    )
