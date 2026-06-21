"""add feedback_category column to auto_approval_calibration

Revision ID: x4s5t6u7v8w9
Revises: w3r4s5t6u7v8
Create Date: 2026-06-21 23:10:00.000000+00:00

Adds ``feedback_category TEXT NULL`` to ``auto_approval_calibration``
so per-rejection-reason confusion matrices become computable (Lever B
of the autonomy roadmap, 2026-06-21).

Operator-categorical signals (``weak_hook``, ``too_generic``,
``unsupported_claim``, ``bad_fit``, ``too_long``, ``low_value``)
are captured today on ``blueprints.feedback_issue`` for every rejected
or revised blueprint, but no endpoint reads them. Lever B threads
them into the calibration table so the agreement-rate stats can
break down by reason — "gate approved blueprints that operator
rejected with reason X" becomes a per-X count instead of a single
aggregate.

NULL is the natural value for:
- Approved actions (no rejection reason to record)
- Skipped actions (no reason captured)
- Pre-Lever-B rows (column didn't exist when they were written)

Indexed on (niche_id, feedback_category) so the breakdown endpoint
can group efficiently without a sequential scan.
"""

from alembic import op

revision = "x4s5t6u7v8w9"
down_revision = "w3r4s5t6u7v8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE auto_approval_calibration ADD COLUMN IF NOT EXISTS feedback_category TEXT NULL"
    )
    # Composite index for the breakdown endpoint's GROUP BY pattern.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_calibration_niche_category "
        "ON auto_approval_calibration (niche_id, feedback_category) "
        "WHERE feedback_category IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_calibration_niche_category")
    op.execute("ALTER TABLE auto_approval_calibration DROP COLUMN IF EXISTS feedback_category")
