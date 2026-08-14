"""slo_forecasts (Phase 2.C of the Genius Program)

Revision ID: p2c3d4e5f6g7
Revises: p2b3a4b5c6d7
Create Date: 2026-08-14 12:50:00.000000+00:00

Stores current-state forecasts for tracked SLO check_names.
Overwritten hourly by run_slo_forecast.py — one row per
(check_name, niche_id).
"""
from alembic import op

revision = "p2c3d4e5f6g7"
down_revision = "p2b3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS slo_forecasts (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          check_name            TEXT NOT NULL,
          niche_id              TEXT NOT NULL DEFAULT '',
          current_rate          DOUBLE PRECISION NOT NULL,
          forecast_rate         DOUBLE PRECISION NOT NULL,
          trend_pct             DOUBLE PRECISION NOT NULL,
          verdict               TEXT NOT NULL,
          ttb_hours             DOUBLE PRECISION,
          computed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CONSTRAINT slo_forecasts_check_niche_unique
            UNIQUE (check_name, niche_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_slo_forecasts_verdict
        ON slo_forecasts (verdict, computed_at DESC)
        WHERE verdict IN ('forecast_warning', 'forecast_critical', 'watch')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS slo_forecasts CASCADE")
