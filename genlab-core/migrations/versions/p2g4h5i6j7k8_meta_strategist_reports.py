"""meta_strategist_reports (Phase 2.G of the Genius Program)

Revision ID: p2g4h5i6j7k8
Revises: p2e3f4g5h6i7
Create Date: 2026-08-14 13:55:00.000000+00:00

Records weekly meta-strategist verdicts on strategist proposal
quality. Each row = one week's LLM review + recommendations.
"""
from alembic import op

revision = "p2g4h5i6j7k8"
down_revision = "p2e3f4g5h6i7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS meta_strategist_reports (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          week_of               DATE NOT NULL UNIQUE,
          proposals_reviewed    INTEGER NOT NULL DEFAULT 0,
          verdicts_available    INTEGER NOT NULL DEFAULT 0,
          overall_grade         TEXT NOT NULL,
          per_type_grades       JSONB NOT NULL DEFAULT '{}'::jsonb,
          recommendations       JSONB NOT NULL DEFAULT '[]'::jsonb,
          llm_cost_usd          DOUBLE PRECISION,
          run_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS meta_strategist_reports CASCADE")
