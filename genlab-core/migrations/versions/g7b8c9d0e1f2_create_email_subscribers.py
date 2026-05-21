"""create email_subscribers table

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-23 12:00:00.000000+00:00
"""

from alembic import op

revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS email_subscribers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email TEXT NOT NULL,
            channel_slug TEXT NOT NULL,
            niche_id TEXT NOT NULL DEFAULT '',
            source TEXT DEFAULT 'link_in_bio',
            subscribed_at TIMESTAMPTZ DEFAULT now(),
            unsubscribed_at TIMESTAMPTZ,
            is_active BOOLEAN DEFAULT true,
            UNIQUE(email, channel_slug)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_es_niche
        ON email_subscribers(niche_id) WHERE is_active = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_es_niche")
    op.execute("DROP TABLE IF EXISTS email_subscribers")
