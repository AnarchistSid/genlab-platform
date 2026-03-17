"""create stories and assets tables with RLS

Revision ID: a1b2c3d4e5f6
Revises: 6034a2c87755
Create Date: 2026-03-17 14:00:00.000000

Phase 2: Stories + Assets tables with Row Level Security.
Stories holds the fetched-story backlog; Assets holds media references per story.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "6034a2c87755"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create stories and assets tables with indexes and RLS policies."""

    # ── Stories ──────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS stories (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        story_id TEXT UNIQUE NOT NULL,
        title TEXT,
        url TEXT,
        source_name TEXT,
        source_type TEXT,
        status TEXT NOT NULL DEFAULT 'NEW',
        published_at TIMESTAMPTZ,
        score FLOAT DEFAULT 0.0,
        video_url TEXT,
        video_id TEXT,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_stories_niche_status
        ON stories(niche_id, status);
    CREATE INDEX IF NOT EXISTS idx_stories_story_id
        ON stories(story_id);
    CREATE INDEX IF NOT EXISTS idx_stories_score
        ON stories(score DESC)
        WHERE status = 'NEW';

    ALTER TABLE stories ENABLE ROW LEVEL SECURITY;
    ALTER TABLE stories FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON stories;
    CREATE POLICY niche_isolation ON stories
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON stories TO genlab;
    """)

    # ── Assets ───────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS assets (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        asset_id TEXT UNIQUE NOT NULL,
        story_id TEXT,
        url TEXT,
        asset_type TEXT,
        status TEXT NOT NULL DEFAULT 'NEW',
        source_type TEXT,
        file_path TEXT,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_assets_niche_status
        ON assets(niche_id, status);
    CREATE INDEX IF NOT EXISTS idx_assets_asset_id
        ON assets(asset_id);
    CREATE INDEX IF NOT EXISTS idx_assets_story_id
        ON assets(story_id);

    ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
    ALTER TABLE assets FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON assets;
    CREATE POLICY niche_isolation ON assets
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON assets TO genlab;
    """)


def downgrade() -> None:
    """Drop stories and assets tables."""
    op.execute("DROP TABLE IF EXISTS assets CASCADE;")
    op.execute("DROP TABLE IF EXISTS stories CASCADE;")
