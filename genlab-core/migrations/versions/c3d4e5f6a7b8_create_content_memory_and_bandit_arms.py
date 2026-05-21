"""create content_memory and bandit_arms tables with RLS

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-17 14:20:00.000000

Phase 4: Content_Memory + BanditArms tables with Row Level Security.
Content_Memory stores dedup history (DO NOT PURGE).
BanditArms stores Thompson Sampling / LinUCB state for the learning loop.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create content_memory and bandit_arms tables with indexes and RLS."""

    # ── Content Memory ───────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS content_memory (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        content_hash TEXT UNIQUE NOT NULL,
        title TEXT,
        url TEXT,
        first_seen TIMESTAMPTZ DEFAULT now(),
        last_seen TIMESTAMPTZ DEFAULT now(),
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_cm_niche
        ON content_memory(niche_id);
    CREATE INDEX IF NOT EXISTS idx_cm_content_hash
        ON content_memory(content_hash);
    CREATE INDEX IF NOT EXISTS idx_cm_last_seen
        ON content_memory(last_seen DESC);

    ALTER TABLE content_memory ENABLE ROW LEVEL SECURITY;
    ALTER TABLE content_memory FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON content_memory;
    CREATE POLICY niche_isolation ON content_memory
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON content_memory TO genlab;
    """)

    # ── Bandit Arms ──────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE IF NOT EXISTS bandit_arms (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id TEXT NOT NULL,
        arm_id TEXT UNIQUE NOT NULL,
        alpha FLOAT DEFAULT 1.0,
        beta FLOAT DEFAULT 1.0,
        n_plays INTEGER DEFAULT 0,
        linucb_state JSONB DEFAULT '{}',
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        extra JSONB DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_ba_niche
        ON bandit_arms(niche_id);
    CREATE INDEX IF NOT EXISTS idx_ba_arm_id
        ON bandit_arms(arm_id);

    ALTER TABLE bandit_arms ENABLE ROW LEVEL SECURITY;
    ALTER TABLE bandit_arms FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON bandit_arms;
    CREATE POLICY niche_isolation ON bandit_arms
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON bandit_arms TO genlab;
    """)


def downgrade() -> None:
    """Drop content_memory and bandit_arms tables."""
    op.execute("DROP TABLE IF EXISTS bandit_arms CASCADE;")
    op.execute("DROP TABLE IF EXISTS content_memory CASCADE;")
