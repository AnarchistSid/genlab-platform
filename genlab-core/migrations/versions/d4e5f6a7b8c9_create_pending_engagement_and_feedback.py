"""create pending_engagement and pending_feedback tables with RLS

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-17 14:30:00.000000

Phase 5: PendingEngagement + PendingFeedback tables with Row Level Security.
PendingEngagement is the engagement worker queue.
PendingFeedback tracks feedback collection tasks across 4 time windows.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create pending_engagement and pending_feedback tables with indexes and RLS."""

    # ── Pending Engagement ───────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS pending_engagement (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        post_id TEXT,
        platform TEXT NOT NULL,
        scheduled_at TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT 'PENDING',
        attempts INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_pe_niche_status
        ON pending_engagement(niche_id, status);
    CREATE INDEX IF NOT EXISTS idx_pe_post_id
        ON pending_engagement(post_id);
    CREATE INDEX IF NOT EXISTS idx_pe_scheduled_at
        ON pending_engagement(scheduled_at)
        WHERE status = 'PENDING';

    ALTER TABLE pending_engagement ENABLE ROW LEVEL SECURITY;
    ALTER TABLE pending_engagement FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON pending_engagement;
    CREATE POLICY niche_isolation ON pending_engagement
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON pending_engagement TO genlab;
    """)

    # ── Pending Feedback ─────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS pending_feedback (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        task_id TEXT UNIQUE NOT NULL,
        post_id TEXT,
        platform TEXT,
        arm_id TEXT,
        bandit_context JSONB DEFAULT '{}',
        collection_status TEXT NOT NULL DEFAULT 'PENDING',
        reward_48h FLOAT,
        publish_time TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_pf_niche_status
        ON pending_feedback(niche_id, collection_status);
    CREATE INDEX IF NOT EXISTS idx_pf_task_id
        ON pending_feedback(task_id);
    CREATE INDEX IF NOT EXISTS idx_pf_post_id
        ON pending_feedback(post_id);
    CREATE INDEX IF NOT EXISTS idx_pf_publish_time
        ON pending_feedback(publish_time DESC)
        WHERE publish_time IS NOT NULL;

    ALTER TABLE pending_feedback ENABLE ROW LEVEL SECURITY;
    ALTER TABLE pending_feedback FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON pending_feedback;
    CREATE POLICY niche_isolation ON pending_feedback
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON pending_feedback TO genlab;
    """)


def downgrade() -> None:
    """Drop pending_engagement and pending_feedback tables."""
    op.execute("DROP TABLE IF EXISTS pending_feedback CASCADE;")
    op.execute("DROP TABLE IF EXISTS pending_engagement CASCADE;")
