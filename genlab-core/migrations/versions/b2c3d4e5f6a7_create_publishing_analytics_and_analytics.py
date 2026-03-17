"""create publishing_analytics and analytics tables with RLS

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-17 14:10:00.000000

Phase 3: Publishing_Analytics + Analytics tables with Row Level Security.
Publishing_Analytics tracks per-platform publish records.
Analytics stores engagement data across collection windows.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create publishing_analytics and analytics tables with indexes and RLS."""

    # ── Publishing Analytics ─────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS publishing_analytics (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        post_id TEXT,
        platform TEXT NOT NULL,
        published_at TIMESTAMPTZ,
        status TEXT NOT NULL DEFAULT 'PENDING',
        views BIGINT DEFAULT 0,
        likes BIGINT DEFAULT 0,
        comments BIGINT DEFAULT 0,
        shares BIGINT DEFAULT 0,
        saves BIGINT DEFAULT 0,
        metrics_fetched BOOLEAN DEFAULT false,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_pa_niche_platform
        ON publishing_analytics(niche_id, platform);
    CREATE INDEX IF NOT EXISTS idx_pa_post_id
        ON publishing_analytics(post_id);
    CREATE INDEX IF NOT EXISTS idx_pa_published_at
        ON publishing_analytics(published_at DESC)
        WHERE published_at IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_pa_status
        ON publishing_analytics(status);

    ALTER TABLE publishing_analytics ENABLE ROW LEVEL SECURITY;
    ALTER TABLE publishing_analytics FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON publishing_analytics;
    CREATE POLICY niche_isolation ON publishing_analytics
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON publishing_analytics TO genlab;
    """)

    # ── Analytics ────────────────────────────────────────────────────
    # Note: "window" and "value" are PostgreSQL reserved words — quoted as identifiers.
    op.execute("""
    CREATE TABLE IF NOT EXISTS analytics (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        post_id TEXT,
        platform TEXT,
        metric_type TEXT,
        "value" FLOAT DEFAULT 0.0,
        collected_at TIMESTAMPTZ,
        "window" TEXT,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_analytics_niche_platform
        ON analytics(niche_id, platform);
    CREATE INDEX IF NOT EXISTS idx_analytics_post_id
        ON analytics(post_id);
    CREATE INDEX IF NOT EXISTS idx_analytics_collected_at
        ON analytics(collected_at DESC)
        WHERE collected_at IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_analytics_window
        ON analytics("window");

    ALTER TABLE analytics ENABLE ROW LEVEL SECURITY;
    ALTER TABLE analytics FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON analytics;
    CREATE POLICY niche_isolation ON analytics
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON analytics TO genlab;
    """)


def downgrade() -> None:
    """Drop publishing_analytics and analytics tables."""
    op.execute("DROP TABLE IF EXISTS analytics CASCADE;")
    op.execute("DROP TABLE IF EXISTS publishing_analytics CASCADE;")
