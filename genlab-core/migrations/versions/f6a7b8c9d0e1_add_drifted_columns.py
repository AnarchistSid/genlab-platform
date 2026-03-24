"""add drifted columns to blueprints, publishing_analytics, stories

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-23 12:00:00.000000

Captures columns that were added via raw ALTER TABLE during Sprints 62-67
but never recorded in an Alembic migration. Uses ADD COLUMN IF NOT EXISTS
so this migration is safe to run on both fresh and existing databases.

Tables affected:
  - blueprints: 14 columns (hook_text, caption, format, story_id, topic,
    arm_id, affiliate_product, affiliate_url, affiliate_network,
    affiliate_commission_pct, source, summary, error_message, blueprint_id)
  - publishing_analytics: 2 columns (blueprint_id, error_message)
  - stories: 2 columns (source, summary)
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add drifted columns that exist in DB but have no migration."""

    # ── Blueprints (14 columns) ───────────────────────────────────────
    op.execute("""
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS hook_text TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS caption TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS format TEXT DEFAULT 'reel';
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS story_id TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS topic TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS arm_id TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS affiliate_product TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS affiliate_url TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS affiliate_network TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS affiliate_commission_pct REAL;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS source TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS summary TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS error_message TEXT;
    ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS blueprint_id TEXT;
    """)

    # ── Blueprints indexes for new columns ────────────────────────────
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_bp_story_id
        ON blueprints(story_id);
    CREATE INDEX IF NOT EXISTS idx_bp_topic
        ON blueprints(topic);
    CREATE INDEX IF NOT EXISTS idx_bp_affiliate_product
        ON blueprints(affiliate_product)
        WHERE affiliate_product IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_bp_affiliate_network
        ON blueprints(affiliate_network)
        WHERE affiliate_network IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_bp_action_taken
        ON blueprints(status, action_taken)
        WHERE action_taken IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_bp_hook_niche
        ON blueprints(hook, niche_id);
    """)

    # ── Blueprints FK: story_id → stories(story_id) ──────────────────
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'fk_bp_story'
        ) THEN
            ALTER TABLE blueprints
                ADD CONSTRAINT fk_bp_story
                FOREIGN KEY (story_id) REFERENCES stories(story_id)
                ON DELETE SET NULL;
        END IF;
    END $$;
    """)

    # ── Publishing Analytics (2 columns) ──────────────────────────────
    op.execute("""
    ALTER TABLE publishing_analytics
        ADD COLUMN IF NOT EXISTS blueprint_id UUID;
    ALTER TABLE publishing_analytics
        ADD COLUMN IF NOT EXISTS error_message TEXT;
    """)

    # ── Publishing Analytics indexes + FK ─────────────────────────────
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_pa_blueprint_id
        ON publishing_analytics(blueprint_id);
    CREATE INDEX IF NOT EXISTS idx_pa_created_status
        ON publishing_analytics(created_at, status);
    """)

    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'fk_pa_blueprint'
        ) THEN
            ALTER TABLE publishing_analytics
                ADD CONSTRAINT fk_pa_blueprint
                FOREIGN KEY (blueprint_id) REFERENCES blueprints(id)
                ON DELETE SET NULL;
        END IF;
    END $$;
    """)

    # ── Stories (2 columns) ───────────────────────────────────────────
    op.execute("""
    ALTER TABLE stories ADD COLUMN IF NOT EXISTS source TEXT;
    ALTER TABLE stories ADD COLUMN IF NOT EXISTS summary TEXT;
    """)


def downgrade() -> None:
    """Drop the drifted columns (reverse order of upgrade)."""

    # ── Stories ───────────────────────────────────────────────────────
    op.execute("""
    ALTER TABLE stories DROP COLUMN IF EXISTS summary;
    ALTER TABLE stories DROP COLUMN IF EXISTS source;
    """)

    # ── Publishing Analytics ──────────────────────────────────────────
    op.execute("""
    ALTER TABLE publishing_analytics
        DROP CONSTRAINT IF EXISTS fk_pa_blueprint;
    DROP INDEX IF EXISTS idx_pa_created_status;
    DROP INDEX IF EXISTS idx_pa_blueprint_id;
    ALTER TABLE publishing_analytics DROP COLUMN IF EXISTS error_message;
    ALTER TABLE publishing_analytics DROP COLUMN IF EXISTS blueprint_id;
    """)

    # ── Blueprints ────────────────────────────────────────────────────
    op.execute("""
    ALTER TABLE blueprints DROP CONSTRAINT IF EXISTS fk_bp_story;
    DROP INDEX IF EXISTS idx_bp_hook_niche;
    DROP INDEX IF EXISTS idx_bp_action_taken;
    DROP INDEX IF EXISTS idx_bp_affiliate_network;
    DROP INDEX IF EXISTS idx_bp_affiliate_product;
    DROP INDEX IF EXISTS idx_bp_topic;
    DROP INDEX IF EXISTS idx_bp_story_id;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS blueprint_id;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS error_message;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS summary;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS source;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS affiliate_commission_pct;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS affiliate_network;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS affiliate_url;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS affiliate_product;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS arm_id;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS topic;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS story_id;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS format;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS caption;
    ALTER TABLE blueprints DROP COLUMN IF EXISTS hook_text;
    """)
