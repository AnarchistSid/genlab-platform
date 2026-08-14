"""content_quality_scores (Phase 4.A session 3)

Revision ID: p4a7j8k9l0m1
Revises: p3c6i7j8k9l0
Create Date: 2026-08-14 19:45:00.000000+00:00

Per-blueprint multi-modal quality scores. One row per (blueprint_id,
video_hash) pair — the video_hash lets us re-score if the same
blueprint gets a fresh render (e.g., after a first-frame-brightener
autofix).

Downstream consumers (session 4):
  * Bandit reward multiplier — reward *= joint_score at
    metric-collection time
  * Mission Control card showing per-niche quality-score
    distribution (helps operator spot renders drifting low)
"""
from alembic import op

revision = "p4a7j8k9l0m1"
down_revision = "p3c6i7j8k9l0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS content_quality_scores (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            blueprint_id UUID NOT NULL,
            niche_id TEXT NOT NULL,
            video_path TEXT NOT NULL,
            video_hash TEXT NOT NULL,
            -- visual sub-scores (nullable when extractor failed)
            color_palette_dominance DOUBLE PRECISION,
            motion_energy DOUBLE PRECISION,
            cut_frequency DOUBLE PRECISION,
            brand_consistency DOUBLE PRECISION,
            -- audio sub-scores
            audio_energy_variance DOUBLE PRECISION,
            dialogue_density DOUBLE PRECISION,
            music_to_voice_ratio DOUBLE PRECISION,
            -- fusion outputs
            visual_score DOUBLE PRECISION,
            audio_score DOUBLE PRECISION,
            joint_score DOUBLE PRECISION,
            -- diagnostic
            failed_extractors TEXT[],
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (blueprint_id, video_hash)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_content_quality_scores_niche_computed
        ON content_quality_scores (niche_id, computed_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_content_quality_scores_blueprint
        ON content_quality_scores (blueprint_id, computed_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_content_quality_scores_blueprint")
    op.execute("DROP INDEX IF EXISTS ix_content_quality_scores_niche_computed")
    op.execute("DROP TABLE IF EXISTS content_quality_scores")
