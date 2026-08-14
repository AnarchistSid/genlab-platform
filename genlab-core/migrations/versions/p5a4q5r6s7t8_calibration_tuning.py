"""calibration_tuning_suggestions (Phase 5.A)

Revision ID: p5a4q5r6s7t8
Revises: p4e3p4q5r6s7
Create Date: 2026-08-14 23:15:00.000000+00:00

Weekly auto-tune suggestions for auto_publish.min_confidence per
niche. Each row = one weekly analysis + suggestion + apply-decision.

  * ``confusion`` JSONB — {tp, tn, fp, fn} from the last N weeks
    of calibration data.
  * ``current_min_confidence`` — from the niche's publishing.yaml
    at analysis time (so historical rows preserve the diff even
    if the operator later edits).
  * ``suggested_delta`` — signed float. Positive = raise threshold
    (gate too permissive, FP > FN). Negative = lower (gate too
    strict).
  * ``applied`` — TRUE when the runner auto-applied because the
    delta was within the safe [-0.05, +0.05] range. FALSE when
    the delta was too large + operator review is required.
  * ``rationale`` — short human-readable why-string for the operator.

Rule #22 pin: the reasoning MUST look at the full confusion matrix,
not just agreement %. The 2026-07-17 lesson (moved from ai_creators
92% real to gaming 53% because comparison used wrong operator_action
literal) is what motivated this task.
"""
from alembic import op

revision = "p5a4q5r6s7t8"
down_revision = "p4e3p4q5r6s7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS calibration_tuning_suggestions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            week_of DATE NOT NULL,
            confusion JSONB NOT NULL,
            sample_size INTEGER NOT NULL,
            current_min_confidence DOUBLE PRECISION NOT NULL,
            suggested_delta DOUBLE PRECISION NOT NULL,
            suggested_min_confidence DOUBLE PRECISION NOT NULL,
            applied BOOLEAN NOT NULL DEFAULT FALSE,
            rationale TEXT NOT NULL,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (niche_id, week_of)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calibration_tuning_niche_week
        ON calibration_tuning_suggestions (niche_id, week_of DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_calibration_tuning_niche_week")
    op.execute("DROP TABLE IF EXISTS calibration_tuning_suggestions")
