"""config_updates: persist auto-tuned YAML changes for dashboard surfacing

Revision ID: j0e1f2g3h4i5
Revises: i9d0e1f2g3h4
Create Date: 2026-05-20 23:00:00.000000+00:00

Background
----------
``run_config_update.py`` runs weekly and calls ``ConfigUpdater.run`` to
translate bandit posteriors into YAML changes (posting schedule slots,
hook-type ratios). Each change is logged via ``logger.info`` and then
discarded — there has never been a persistence layer.

The dashboard's Learning > Config Updates tab carries a literal
``Update History (Future)`` placeholder acknowledging this gap. Without
a persisted history, no operator can audit what the auto-tuner has
actually done over time.

This migration adds a small append-only audit log keyed by
(niche_id, applied_at), with the relevant before/after values for each
change. ``run_config_update.py`` is updated in the same commit to write
to it; the dashboard endpoint and UI render it as the new table.

Append-only by intent — there's no UPDATE/DELETE path. Operators who
need to roll back a YAML change do so by editing the file directly;
the history row stays for audit.
"""
from alembic import op
import sqlalchemy as sa

revision = "j0e1f2g3h4i5"
down_revision = "i9d0e1f2g3h4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE config_updates (
        id           uuid                     PRIMARY KEY DEFAULT gen_random_uuid(),
        niche_id     text                     NOT NULL,
        file_path    text                     NOT NULL,
        field        text                     NOT NULL,
        old_value    text,
        new_value    text,
        reason       text,
        n_records    integer,
        applied_at   timestamptz              NOT NULL DEFAULT now(),
        dry_run      boolean                  NOT NULL DEFAULT false,
        extra        jsonb                    NOT NULL DEFAULT '{}'::jsonb
    )
    """)
    op.execute(
        "CREATE INDEX idx_config_updates_niche_applied "
        "ON config_updates (niche_id, applied_at DESC)"
    )
    # Same niche-isolation pattern as the rest of the prod schema. Same
    # 'all' / '' / NULL escape so the admin dashboard (which doesn't
    # set app.niche_id) can read the full history.
    op.execute("ALTER TABLE config_updates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE config_updates FORCE ROW LEVEL SECURITY")
    op.execute("""
    CREATE POLICY niche_isolation ON config_updates
    USING (
        niche_id = current_setting('app.niche_id', true)
        OR current_setting('app.niche_id', true) IN ('', 'all')
        OR current_setting('app.niche_id', true) IS NULL
    )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS niche_isolation ON config_updates")
    op.execute("DROP INDEX IF EXISTS idx_config_updates_niche_applied")
    op.execute("DROP TABLE IF EXISTS config_updates")
