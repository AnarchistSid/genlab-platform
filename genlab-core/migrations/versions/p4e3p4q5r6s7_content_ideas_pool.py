"""content_ideas_pool (Phase 4.E session 1)

Revision ID: p4e3p4q5r6s7
Revises: p4d2o3p4q5r6
Create Date: 2026-08-14 22:55:00.000000+00:00

Pool of LLM-generated content ideas the writer can draw from when
trending-video sources return low-signal. Fills the gap between
"our bandit knows what styles work" and "here's a novel story
concept the bandit couldn't have proposed."

Each row is one ideated concept:

  * ``title`` / ``hook_seed`` — LLM-written concept + candidate hook
  * ``rationale`` — WHY the LLM proposed it (trend + competitor +
    persona alignment reasoning)
  * ``source_signals`` JSONB — snapshot of the inputs that seeded
    the LLM: {trend_topics, competitor_hooks, top_styles,
    persona_hash}. Lets the analyzer trace: "did trend-heavy
    ideas outperform competitor-heavy ideas?"
  * ``consumed_at`` — nullable; set when writer picks this idea.
  * ``consumed_by_blueprint_id`` — FK-style reference back for
    attribution.
  * ``status`` — pending / consumed / expired (>30d without use).

Session 2 writer wire: SELECT ... WHERE consumed_at IS NULL AND
niche_id=? ORDER BY score DESC LIMIT 1 → mark consumed.
Session 3: analyzer joins consumed_by_blueprint_id →
pending_feedback.reward_48h to measure pool-vs-trending lift.
"""
from alembic import op

revision = "p4e3p4q5r6s7"
down_revision = "p4d2o3p4q5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS content_ideas_pool (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            title TEXT NOT NULL,
            hook_seed TEXT,
            rationale TEXT,
            source_signals JSONB NOT NULL DEFAULT '{}'::jsonb,
            score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            status TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending', 'consumed', 'expired')),
            consumed_at TIMESTAMPTZ,
            consumed_by_blueprint_id UUID,
            batch_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_content_ideas_niche_status_score
        ON content_ideas_pool (niche_id, status, score DESC)
        WHERE status = 'pending'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_content_ideas_batch
        ON content_ideas_pool (batch_id, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_content_ideas_batch")
    op.execute("DROP INDEX IF EXISTS ix_content_ideas_niche_status_score")
    op.execute("DROP TABLE IF EXISTS content_ideas_pool")
