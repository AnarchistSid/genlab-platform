"""create templates and sources tables with RLS

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-17 14:40:00.000000

Phase 6: Templates + Sources tables with Row Level Security.
Templates stores niche video templates with duration/category.
Sources stores configured content sources per niche with tier/weight.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create templates and sources tables with indexes and RLS."""

    # ── Templates ────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS templates (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        template_id TEXT UNIQUE NOT NULL,
        name TEXT,
        category TEXT,
        max_duration INTEGER,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_templates_niche_status
        ON templates(niche_id, status);
    CREATE INDEX IF NOT EXISTS idx_templates_template_id
        ON templates(template_id);
    CREATE INDEX IF NOT EXISTS idx_templates_category
        ON templates(category);

    ALTER TABLE templates ENABLE ROW LEVEL SECURITY;
    ALTER TABLE templates FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON templates;
    CREATE POLICY niche_isolation ON templates
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON templates TO genlab;
    """)

    # ── Sources ──────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS sources (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        source_id TEXT,
        name TEXT,
        url TEXT,
        source_type TEXT,
        tier TEXT,
        weight FLOAT DEFAULT 1.0,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        last_fetched TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_sources_niche_status
        ON sources(niche_id, status);
    CREATE INDEX IF NOT EXISTS idx_sources_source_type
        ON sources(source_type);
    CREATE INDEX IF NOT EXISTS idx_sources_last_fetched
        ON sources(last_fetched DESC)
        WHERE last_fetched IS NOT NULL;

    ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
    ALTER TABLE sources FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON sources;
    CREATE POLICY niche_isolation ON sources
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON sources TO genlab;
    """)


def downgrade() -> None:
    """Drop templates and sources tables."""
    op.execute("DROP TABLE IF EXISTS sources CASCADE;")
    op.execute("DROP TABLE IF EXISTS templates CASCADE;")
