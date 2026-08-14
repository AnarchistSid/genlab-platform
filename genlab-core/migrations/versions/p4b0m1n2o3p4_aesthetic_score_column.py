"""aesthetic_score column on content_quality_scores (Phase 4.B session 3)

Revision ID: p4b0m1n2o3p4
Revises: p4b9l0m1n2o3
Create Date: 2026-08-14 20:45:00.000000+00:00

Adds ``aesthetic_score DOUBLE PRECISION`` to ``content_quality_scores``.
Populated by the pre-publish scorer that applies the active
per-niche logreg model to a fresh render's 15 composition features.

Nullable — until a niche has an active model (session-2 retrainer
gate: AUC > 0.60), the column stays NULL and downstream consumers
fall through the same fail-open path as elsewhere.
"""
from alembic import op

revision = "p4b0m1n2o3p4"
down_revision = "p4b9l0m1n2o3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE content_quality_scores
        ADD COLUMN IF NOT EXISTS aesthetic_score DOUBLE PRECISION
        """
    )
    op.execute(
        """
        ALTER TABLE content_quality_scores
        ADD COLUMN IF NOT EXISTS aesthetic_model_version INTEGER
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE content_quality_scores DROP COLUMN IF EXISTS aesthetic_model_version"
    )
    op.execute(
        "ALTER TABLE content_quality_scores DROP COLUMN IF EXISTS aesthetic_score"
    )
