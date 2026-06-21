"""add review_duration_ms column to auto_approval_calibration

Revision ID: w3r4s5t6u7v8
Revises: v2q3r4s5t6u7
Create Date: 2026-06-21 22:30:00.000000+00:00

Adds ``review_duration_ms INTEGER NULL`` to ``auto_approval_calibration``
so the calibration logger can record how long the operator spent on
each review (Lever E2 of the autonomy roadmap, 2026-06-21).

Dwell time distinguishes fast confident approves (~1-3s) from slow
ambiguous decisions (~10-60s+). With this signal:

- Calibration samples can be weighted by confidence (fast = strong
  signal, slow = weak signal — operator was unsure)
- The active-learning queue sort (Lever F) can prioritize cases where
  operator dwell time was high → similar borderline cases will benefit
  from re-labelling
- Per-operator workload analytics become possible (avg dwell × N
  reviews/day → operator-hour estimates)

NULL is the natural cold-start value: rows written before this column
existed, or by callers that haven't been updated to pass the duration
(e.g. backfill paths), default to NULL. Aggregation queries should
``AVG(review_duration_ms) FILTER (WHERE review_duration_ms IS NOT NULL)``
to ignore the cold-start population.
"""

from alembic import op

revision = "w3r4s5t6u7v8"
down_revision = "v2q3r4s5t6u7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE auto_approval_calibration "
        "ADD COLUMN IF NOT EXISTS review_duration_ms INTEGER NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE auto_approval_calibration DROP COLUMN IF EXISTS review_duration_ms")
