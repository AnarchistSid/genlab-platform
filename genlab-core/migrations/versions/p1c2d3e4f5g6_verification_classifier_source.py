"""strategist_outcome_verification: add classifier_source + classifier_name

Revision ID: p1c2d3e4f5g6
Revises: p0d1a2b3c4d5
Create Date: 2026-08-14 12:15:00.000000+00:00

Phase 1.C of the Genius Program Roadmap. Enables meta-learning:
"which classifier decisions actually help vs hurt when accepted?"

Two new columns:

  * ``classifier_source`` — 'heuristic' | 'llm' | 'manual' | 'unknown'
    (whose auto-accept path put this proposal into proposals_accepted)
  * ``classifier_name`` — 'arm_add' | 'reward_weight' | 'gate_threshold'
    | 'novelty_rate' | 'playbook_update' (proposal type)

`classifier_name` duplicates `proposal_type` on purpose — cleaner API
for meta-learning queries that GROUP BY (source, name).

Backfill on upgrade: any existing rows (from Phase 1.A pending checks)
get classifier_source='unknown' since we can't reconstruct which path
put them there.
"""
from alembic import op

revision = "p1c2d3e4f5g6"
down_revision = "p0d1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategist_outcome_verification
        ADD COLUMN IF NOT EXISTS classifier_source TEXT
            NOT NULL DEFAULT 'unknown',
        ADD COLUMN IF NOT EXISTS classifier_name TEXT
            NOT NULL DEFAULT ''
        """
    )
    # Index for the meta-learning aggregate query
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategist_outcome_meta_learn
        ON strategist_outcome_verification
          (classifier_source, classifier_name, verdict)
        WHERE verdict != 'pending'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS idx_strategist_outcome_meta_learn
        """
    )
    op.execute(
        """
        ALTER TABLE strategist_outcome_verification
        DROP COLUMN IF EXISTS classifier_source,
        DROP COLUMN IF EXISTS classifier_name
        """
    )
