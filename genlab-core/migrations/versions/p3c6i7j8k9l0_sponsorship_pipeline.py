"""sponsorship_pipeline + sponsorship_brand_targets (Phase 3.C of the Genius Program)

Revision ID: p3c6i7j8k9l0
Revises: p3a5h6i7j8k9
Create Date: 2026-08-14 17:15:00.000000+00:00

Two-table data model for auto-outreach:

  * ``sponsorship_brand_targets`` — per-niche brand catalog. Operator
    seeds this manually with (niche_id, brand_name, brand_email,
    contact_first_name, notes). Auto-outreach reads from here — never
    invents brand emails.

  * ``sponsorship_pipeline`` — one row per (target, outreach attempt).
    Status lifecycle: DRAFTED → APPROVED → SENT → RESPONDED → DEAL.
    DRAFTED = auto-generated, awaiting operator approval. SENT means
    an email actually went out. Operator can also mark as REJECTED
    (draft killed) or STALE (no response after N days).

Session 1 (this migration) creates both tables. Session 1 runner
only writes DRAFTED rows. Session 2 will add the sending wire that
moves DRAFTED → SENT.
"""
from alembic import op

revision = "p3c6i7j8k9l0"
down_revision = "p3a5h6i7j8k9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sponsorship_brand_targets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            brand_name TEXT NOT NULL,
            brand_email TEXT NOT NULL,
            contact_first_name TEXT,
            website_url TEXT,
            notes TEXT,
            added_by TEXT,
            added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            UNIQUE (niche_id, brand_email)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sponsorship_brand_targets_active_niche
        ON sponsorship_brand_targets (niche_id, active)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sponsorship_pipeline (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_id UUID NOT NULL REFERENCES sponsorship_brand_targets(id),
            niche_id TEXT NOT NULL,
            tier_at_generation TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            kit_url TEXT,
            status TEXT NOT NULL DEFAULT 'DRAFTED',
            drafted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            approved_at TIMESTAMPTZ,
            sent_at TIMESTAMPTZ,
            responded_at TIMESTAMPTZ,
            response_snippet TEXT,
            deal_closed_at TIMESTAMPTZ,
            deal_value_usd DOUBLE PRECISION,
            rejected_at TIMESTAMPTZ,
            rejection_reason TEXT,
            extra JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sponsorship_pipeline_niche_status
        ON sponsorship_pipeline (niche_id, status, drafted_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sponsorship_pipeline_target
        ON sponsorship_pipeline (target_id, drafted_at DESC)
        """
    )
    op.execute(
        """
        ALTER TABLE sponsorship_pipeline
        ADD CONSTRAINT ck_sponsorship_pipeline_status
        CHECK (status IN (
            'DRAFTED', 'APPROVED', 'SENT', 'RESPONDED', 'DEAL',
            'REJECTED', 'STALE'
        ))
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sponsorship_pipeline")
    op.execute("DROP TABLE IF EXISTS sponsorship_brand_targets")
