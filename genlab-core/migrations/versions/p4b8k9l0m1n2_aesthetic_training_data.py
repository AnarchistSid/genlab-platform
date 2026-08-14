"""aesthetic_training_data (Phase 4.B session 1)

Revision ID: p4b8k9l0m1n2
Revises: p4a7j8k9l0m1
Create Date: 2026-08-14 20:20:00.000000+00:00

Labeled training set for the aesthetic quality model. One row per
(blueprint_id, video_hash) that landed in the top 20% or bottom 20%
of reward_48h for its niche.

  * ``label = 1`` — reward in top 20% (aesthetic positive example)
  * ``label = 0`` — reward in bottom 20% (aesthetic negative example)
  * middle 60% skipped — noisier signal, not useful for training

``features`` is JSONB carrying the 15 hand-crafted composition
features (rule of thirds, edge density, symmetry, color harmony,
brightness distribution).

Session 2 (monthly retrainer) reads this table + fits a logistic
regression, persists model coefficients to
``aesthetic_model_versions``.
Session 3 (pre-publish scorer) applies the latest model to new
renders + persists the score alongside content_quality_scores.
"""
from alembic import op

revision = "p4b8k9l0m1n2"
down_revision = "p4a7j8k9l0m1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS aesthetic_training_data (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            blueprint_id UUID NOT NULL,
            niche_id TEXT NOT NULL,
            video_hash TEXT NOT NULL,
            label INTEGER NOT NULL CHECK (label IN (0, 1)),
            reward_48h DOUBLE PRECISION NOT NULL,
            features JSONB NOT NULL DEFAULT '{}'::jsonb,
            extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (blueprint_id, video_hash)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_aesthetic_training_niche_label
        ON aesthetic_training_data (niche_id, label, extracted_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_aesthetic_training_niche_label")
    op.execute("DROP TABLE IF EXISTS aesthetic_training_data")
