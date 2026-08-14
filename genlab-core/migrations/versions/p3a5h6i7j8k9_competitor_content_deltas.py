"""competitor_content_deltas (Phase 3.A of the Genius Program)

Revision ID: p3a5h6i7j8k9
Revises: p2g4h5i6j7k8
Create Date: 2026-08-14 14:15:00.000000+00:00

Records the reach / engagement gap between top-tier competitor
uploads and our own posts within the same niche + day window.
Fed by ``scripts/compute_competitor_deltas.py`` (runs daily).

Downstream consumers (both flag-gated, ship after operator validates
data quality for ≥1 week):

  * ``competitor_context`` field on strategist state — highest-delta
    competitor hook that outperformed ours ≥5x
  * Mission Control ``CompetitorDeltasCard`` — per-niche top 5
"""
from alembic import op

revision = "p3a5h6i7j8k9"
down_revision = "p2g4h5i6j7k8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS competitor_content_deltas (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            competitor_channel_id TEXT NOT NULL,
            competitor_channel_label TEXT,
            competitor_video_id TEXT NOT NULL,
            competitor_title TEXT,
            competitor_published_at TIMESTAMPTZ,
            competitor_view_count BIGINT,
            competitor_like_count BIGINT,
            competitor_comment_count BIGINT,
            our_reference_blueprint_id UUID,
            our_reference_view_count BIGINT,
            our_reference_published_at TIMESTAMPTZ,
            delta_views BIGINT,
            delta_ratio DOUBLE PRECISION,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (competitor_video_id, our_reference_blueprint_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_competitor_content_deltas_niche_computed
        ON competitor_content_deltas (niche_id, computed_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_competitor_content_deltas_delta_ratio
        ON competitor_content_deltas (niche_id, delta_ratio DESC NULLS LAST)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_competitor_content_deltas_delta_ratio")
    op.execute("DROP INDEX IF EXISTS ix_competitor_content_deltas_niche_computed")
    op.execute("DROP TABLE IF EXISTS competitor_content_deltas")
