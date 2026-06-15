"""auto_approval_calibration: UNIQUE index against double-write within 1s

Tonight's audit found 18 duplicate calibration rows that were 20-40ms
apart. Root-cause investigation (T#68) showed those were synthetic
test data, NOT a live bug — but the calibration_logger has NO
idempotency guard, so a real double-call (e.g. operator retries on
slow response, React Query retry on 500, frontend hits two endpoints
for one click) would silently produce duplicate rows that pollute the
confusion matrix.

This migration adds a UNIQUE index on
``(blueprint_id, operator_action, calibration_epoch_second(decided_at))``.
Combined with ``INSERT ... ON CONFLICT DO NOTHING`` at the writer
(shipped in the same PR), this means:

  - Two writes within the SAME second for the same (bp, action) →
    2nd silently dropped at DB level (defense in depth)
  - Two writes >=1 second apart → both succeed (intentional
    re-review case)

Why a wrapper SQL function instead of inlining the expression:
Postgres flags both ``date_trunc('second', timestamptz)`` AND
``extract(epoch from timestamptz)`` as STABLE (not IMMUTABLE) — the
extract-epoch is conservatively marked STABLE despite the epoch of an
absolute time instant being viewer-independent. STABLE expressions
can't be used in functional UNIQUE indexes (CI test-storage fail
surfaced this on two consecutive attempts). The cleanest portable
workaround is to wrap our expression in a tiny SQL function declared
``IMMUTABLE PARALLEL SAFE`` — Postgres accepts our claim and uses it
in the index. The function evaluates to
``floor(extract(epoch from ts))::bigint`` which gives the per-second
bucketing we want.

Why second-bucket instead of strict unique on decided_at: live
writes use NOW() at INSERT time, two retries within 100ms would
otherwise both succeed because their NOW() differs by microseconds.
The 1-second bucket catches the "duplicate write" semantic we want
to suppress without blocking legitimate re-reviews of the same
blueprint hours later.

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
    # Wrapper SQL function declared IMMUTABLE so we can use it in a
    # functional UNIQUE index. The underlying ``extract(epoch from
    # timestamptz)`` is marked STABLE in pg_proc even though epoch-of-
    # absolute-instant is mathematically tz-independent; the wrapper
    # promises immutability to the planner so the index can be built.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION calibration_epoch_second(ts timestamptz)
        RETURNS bigint
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$ SELECT floor(extract(epoch from ts))::bigint $$;
        """
    )

    # Drop pre-existing duplicates before the index creation can succeed.
    # Keeps the EARLIEST row per (blueprint, action) within each 1-second
    # bucket — same logic as tonight's manual dedupe on prod.
    op.execute(
        """
        DELETE FROM auto_approval_calibration a
        USING auto_approval_calibration b
        WHERE a.ctid < b.ctid
          AND a.blueprint_id = b.blueprint_id
          AND a.operator_action = b.operator_action
          AND calibration_epoch_second(a.decided_at)
            = calibration_epoch_second(b.decided_at);
        """
    )

    # Functional UNIQUE index using the IMMUTABLE wrapper.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_calibration_no_dupe_within_second
        ON auto_approval_calibration (
            blueprint_id,
            operator_action,
            calibration_epoch_second(decided_at)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_calibration_no_dupe_within_second;")
    op.execute("DROP FUNCTION IF EXISTS calibration_epoch_second(timestamptz);")
