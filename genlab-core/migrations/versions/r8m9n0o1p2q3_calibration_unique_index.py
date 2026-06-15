"""auto_approval_calibration: UNIQUE index against double-write within 1s

Tonight's audit found 18 duplicate calibration rows that were 20-40ms
apart. Root-cause investigation (T#68) showed those were synthetic
test data, NOT a live bug — but the calibration_logger has NO
idempotency guard, so a real double-call (e.g. operator retries on
slow response, React Query retry on 500, frontend hits two endpoints
for one click) would silently produce duplicate rows that pollute the
confusion matrix.

This migration adds a UNIQUE index on
``(blueprint_id, operator_action, date_trunc('second', decided_at))``.
Combined with ``INSERT ... ON CONFLICT DO NOTHING`` at the writer
(shipped in the same PR), this means:

  - Two writes within the SAME second for the same (bp, action) →
    2nd silently dropped at DB level (defense in depth)
  - Two writes >=1 second apart → both succeed (intentional
    re-review case)

Why second-truncation instead of strict unique on decided_at: live
writes use NOW() at INSERT time, two retries within 100ms would
otherwise both succeed because their NOW() differs by microseconds.
Truncating to the second window matches the "duplicate write"
semantic we want to suppress without blocking legitimate re-reviews
of the same blueprint hours later.

Revision ID: r8m9n0o1p2q3
Revises: q7l8m9n0o1p2
Create Date: 2026-06-15 19:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r8m9n0o1p2q3"
down_revision: str | Sequence[str] | None = "q7l8m9n0o1p2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop pre-existing duplicates before the index creation can succeed.
    # The dedupe SQL keeps the EARLIEST row per (blueprint, action) — same
    # logic as tonight's manual dedupe (which already ran on prod).
    op.execute(
        """
        DELETE FROM auto_approval_calibration a
        USING auto_approval_calibration b
        WHERE a.ctid < b.ctid
          AND a.blueprint_id = b.blueprint_id
          AND a.operator_action = b.operator_action
          AND date_trunc('second', a.decided_at) = date_trunc('second', b.decided_at);
        """
    )
    # Functional UNIQUE index on the second-truncated tuple.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_calibration_no_dupe_within_second
        ON auto_approval_calibration (
            blueprint_id,
            operator_action,
            (date_trunc('second', decided_at))
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_calibration_no_dupe_within_second;")
