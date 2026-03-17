"""create blueprints table with RLS

Revision ID: 6034a2c87755
Revises:
Create Date: 2026-03-17 10:11:47.047852

Creates the blueprints table with Row Level Security (RLS) policy
for niche isolation. The RLS policy uses SET LOCAL app.niche_id to
restrict queries to the current niche's data.

Promoted columns match the most-queried fields from the SharePoint
Blueprints list. Remaining fields go in the extra JSONB column.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '6034a2c87755'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create blueprints table with indexes and RLS policy."""
    op.execute("""
    CREATE TABLE IF NOT EXISTS blueprints (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        candidate_id TEXT UNIQUE NOT NULL,
        title TEXT,
        status TEXT NOT NULL DEFAULT 'DRAFTED',
        hook TEXT,
        scheduled_for TIMESTAMPTZ,
        platform_publish_status JSONB DEFAULT '{}',
        video_id TEXT,
        video_url TEXT,
        source_url TEXT,
        priority_score FLOAT DEFAULT 0.0,
        action_taken TEXT,
        reviewed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    -- Performance indexes for common query patterns
    CREATE INDEX IF NOT EXISTS idx_bp_niche_status
        ON blueprints(niche_id, status);
    CREATE INDEX IF NOT EXISTS idx_bp_candidate
        ON blueprints(candidate_id);
    CREATE INDEX IF NOT EXISTS idx_bp_scheduled
        ON blueprints(scheduled_for)
        WHERE scheduled_for IS NOT NULL;

    -- Enable Row Level Security (FORCE ensures it applies to table owner too)
    ALTER TABLE blueprints ENABLE ROW LEVEL SECURITY;
    ALTER TABLE blueprints FORCE ROW LEVEL SECURITY;

    -- RLS policy: niche isolation
    -- Allows access when:
    --   1. niche_id matches the current session's app.niche_id
    --   2. app.niche_id is empty, 'all', or NULL (admin/superuser mode)
    DROP POLICY IF EXISTS niche_isolation ON blueprints;
    CREATE POLICY niche_isolation ON blueprints
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    -- Grant table-level permissions to the genlab role
    GRANT ALL ON blueprints TO genlab;
    """)


def downgrade() -> None:
    """Drop blueprints table and all associated objects."""
    op.execute("DROP TABLE IF EXISTS blueprints CASCADE;")
