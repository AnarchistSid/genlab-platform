"""aesthetic_model_versions (Phase 4.B session 2)

Revision ID: p4b9l0m1n2o3
Revises: p4b8k9l0m1n2
Create Date: 2026-08-14 20:40:00.000000+00:00

Trained-model registry for the aesthetic quality classifier. Each
row is one monthly training run:

  * ``niche_id`` — models are per-niche (color palettes + composition
    conventions differ enough that a single global model would blur
    the signal).
  * ``version`` — monotonic per-niche (v1, v2, ...) with the newest
    row of highest ``auc`` marked ``is_active=TRUE``.
  * ``coefficients`` JSONB — feature_name → weight, matching
    the 15-feature schema in ``AestheticFeatures``.
  * ``intercept`` — logistic-regression intercept.
  * ``auc`` — held-out AUC score. Only rows with AUC > 0.60 are
    written (roadmap gate).
  * ``n_train`` / ``n_test`` — sample sizes for provenance.

Session 3 (pre-publish scorer) reads
``SELECT * FROM aesthetic_model_versions WHERE niche_id = %s AND
is_active = TRUE ORDER BY trained_at DESC LIMIT 1``.
"""
from alembic import op

revision = "p4b9l0m1n2o3"
down_revision = "p4b8k9l0m1n2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS aesthetic_model_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            coefficients JSONB NOT NULL,
            intercept DOUBLE PRECISION NOT NULL,
            auc DOUBLE PRECISION NOT NULL,
            n_train INTEGER NOT NULL,
            n_test INTEGER NOT NULL,
            trained_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE (niche_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_aesthetic_model_versions_active
        ON aesthetic_model_versions (niche_id, is_active, trained_at DESC)
        WHERE is_active = TRUE
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_aesthetic_model_versions_active")
    op.execute("DROP TABLE IF EXISTS aesthetic_model_versions")
