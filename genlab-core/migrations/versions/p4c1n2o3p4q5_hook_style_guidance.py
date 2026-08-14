"""hook_style_guidance (Phase 4.C session 1)

Revision ID: p4c1n2o3p4q5
Revises: p4b0m1n2o3p4
Create Date: 2026-08-14 20:52:00.000000+00:00

Weekly snapshot of the top-3 hook styles per niche. Session 2
wires this into the writer's LLM system prompt so the LLM knows
"question and comparison styles are working this week, plain
statements are underperforming" — lets the LLM either reinforce
the winning pattern or diverge deliberately for exploration.

Refit cadence: weekly Sunday 04:00 UTC (before the Sunday 07:30
strategist run so downstream state readers see the fresh guidance).

Row shape: one row per (niche_id, week_of). ``top_styles`` JSONB
carries a list of {style_name, reward_mean, n_plays, rank}
sorted by rank ascending.
"""
from alembic import op

revision = "p4c1n2o3p4q5"
down_revision = "p4b0m1n2o3p4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hook_style_guidance (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            week_of DATE NOT NULL,
            top_styles JSONB NOT NULL DEFAULT '[]'::jsonb,
            sample_size INTEGER NOT NULL DEFAULT 0,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (niche_id, week_of)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_hook_style_guidance_niche_week
        ON hook_style_guidance (niche_id, week_of DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_hook_style_guidance_niche_week")
    op.execute("DROP TABLE IF EXISTS hook_style_guidance")
