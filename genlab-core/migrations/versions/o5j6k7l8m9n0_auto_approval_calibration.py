"""create auto_approval_calibration table

Captures (gate verdict, operator action) pairs so we can build the
confusion matrix that unblocks AUTO #2 (enforcement). Each review
action by an operator writes one row: what the gate said + what the
operator did. Stats endpoint reads this table to compute the
per-niche agreement rate.

Revision ID: o5j6k7l8m9n0
Revises: n4i5j6k7l8m9
Create Date: 2026-06-13 17:00:00.000000+00:00
"""

from alembic import op

revision = "o5j6k7l8m9n0"
down_revision = "n4i5j6k7l8m9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS auto_approval_calibration (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            blueprint_id TEXT NOT NULL,
            niche_id TEXT NOT NULL,
            -- What the AutoApprovalGate said when the operator opened
            -- the blueprint for review. None means the gate errored
            -- (preserved for debugging) — these rows are excluded
            -- from the agreement-rate calculation.
            gate_approved BOOLEAN,
            gate_confidence REAL,
            gate_passed_checks JSONB DEFAULT '[]'::jsonb,
            gate_failed_checks JSONB DEFAULT '[]'::jsonb,
            -- What the operator actually clicked: approved / rejected /
            -- revised / skipped. The gate's two-class output maps to a
            -- four-class operator action; agreement is computed as
            -- "gate_approved is true iff operator_action == 'approved'".
            operator_action TEXT NOT NULL,
            -- When the operator clicked. Used to window the confusion
            -- matrix (rolling 7-day, rolling 24h).
            decided_at TIMESTAMPTZ DEFAULT now() NOT NULL
        )
    """)
    # Hot path: dashboard endpoint filters by niche_id + decided_at >= now() - 7d
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_aac_niche_decided
        ON auto_approval_calibration(niche_id, decided_at DESC)
    """)
    # Blueprint dedup: if an operator opens the same blueprint twice
    # (rare, but happens with Esc + reopen), we only want one row.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_aac_blueprint
        ON auto_approval_calibration(blueprint_id, decided_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_aac_blueprint")
    op.execute("DROP INDEX IF EXISTS idx_aac_niche_decided")
    op.execute("DROP TABLE IF EXISTS auto_approval_calibration")
