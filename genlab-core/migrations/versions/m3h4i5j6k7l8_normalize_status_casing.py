"""normalize pending_engagement/pending_feedback status casing to lowercase (R-63)

Both columns' code writers use lowercase — pending_engagement.status is written
as 'pending'/'skipped'/'replied'/'failed'/'pending_review'/'rate_limited', and
pending_feedback.collection_status as 'pending'/'awaiting_6h'/'awaiting_24h'/
'awaiting_48h'/'awaiting_168h'/'complete'/'early_stopped'. But migration
d4e5f6a7b8c9 set both column DEFAULTs to UPPERCASE 'PENDING', so rows created via
the default landed as 'PENDING' (and legacy code left 'RETRYING'/'COLLECTED_48H')
while explicit writes were lowercase — a mixed-casing column.

Two real consequences beyond the cosmetic mismatch:
  * idx_pe_scheduled_at was partial `WHERE status = 'PENDING'`, so it matched
    ZERO of the lowercase rows — a dead index (cf. idx_stories_score).
  * the metric collector filters collection_status with lowercase windows, so the
    legacy uppercase pending_feedback rows were invisible to it — silently stuck.

Standardizes on lowercase (the code convention):
  * lowercases existing data in both columns (idempotent — unsticks the legacy rows),
  * sets both column DEFAULTs to 'pending',
  * rebuilds idx_pe_scheduled_at with `WHERE status = 'pending'` so it works.

Revision ID: m3h4i5j6k7l8
Revises: l2g3h4i5j6k7
Create Date: 2026-05-25 00:00:00.000000+00:00
"""

from alembic import op

revision = "m3h4i5j6k7l8"
down_revision = "l2g3h4i5j6k7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    -- See all rows regardless of the RLS escape variant while normalizing.
    SELECT set_config('app.niche_id', 'all', true);

    UPDATE pending_engagement
        SET status = lower(status)
        WHERE status <> lower(status);
    UPDATE pending_feedback
        SET collection_status = lower(collection_status)
        WHERE collection_status <> lower(collection_status);

    ALTER TABLE pending_engagement ALTER COLUMN status SET DEFAULT 'pending';
    ALTER TABLE pending_feedback ALTER COLUMN collection_status SET DEFAULT 'pending';

    -- Rebuild the partial index against the lowercase value so it is not dead.
    -- Guarded: some deployments' pending_engagement predates the scheduled_at
    -- column (it was hand-created before migration d4e5f6a7b8c9), and there is
    -- no idx_pe_scheduled_at to fix there — skip rather than error.
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'pending_engagement' AND column_name = 'scheduled_at'
        ) THEN
            DROP INDEX IF EXISTS idx_pe_scheduled_at;
            CREATE INDEX IF NOT EXISTS idx_pe_scheduled_at
                ON pending_engagement(scheduled_at)
                WHERE status = 'pending';
        END IF;
    END $$;
    """)


def downgrade() -> None:
    # Restores the prior uppercase DEFAULT + index predicate. Data casing is not
    # reverted (lower-casing is not reversible and is the correct state).
    op.execute("""
    ALTER TABLE pending_engagement ALTER COLUMN status SET DEFAULT 'PENDING';
    ALTER TABLE pending_feedback ALTER COLUMN collection_status SET DEFAULT 'PENDING';

    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'pending_engagement' AND column_name = 'scheduled_at'
        ) THEN
            DROP INDEX IF EXISTS idx_pe_scheduled_at;
            CREATE INDEX IF NOT EXISTS idx_pe_scheduled_at
                ON pending_engagement(scheduled_at)
                WHERE status = 'PENDING';
        END IF;
    END $$;
    """)
