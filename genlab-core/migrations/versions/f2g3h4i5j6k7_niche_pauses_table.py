"""niche_pauses table — per-niche emergency stop

Revision ID: f2g3h4i5j6k7
Revises: e1f2g3h4i5j6
Create Date: 2026-06-25 17:00:00.000000+00:00

PR #577 (2026-06-25): per-niche emergency-stop primitive. The
operator's panic button — one row per actively-paused niche.

## Why this matters

The compliance moat (Phase A) DETECTS problems (warns on every
publish that violates a check) but doesn't STOP them. The auto-
approver (PR #573) RESPECTS the gate when it blocks, but the gate
is observation-only today (no check blocks yet).

When something goes wrong — a copyright strike lands, an account
gets shadowbanned, the operator notices abuse — they need to
PAUSE publishing for that niche immediately. Today: edit
publishing.yaml + redeploy. With this primitive: one INSERT, the
auto-approver + publisher consult the row before every action.

## Schema

  niche_id      TEXT PK    — one row per actively-paused niche
                              (DELETE = unpause)
  paused_until  TIMESTAMPTZ NULL
                            — auto-resume time; NULL = paused
                              indefinitely (manual unpause required)
  reason        TEXT NOT NULL
                            — operator-supplied reason for the audit
                              log (e.g. "copyright_strike_received",
                              "investigating_engagement_drop")
  paused_by     TEXT NULL   — user / system that issued the pause
                              (matches users.username when manual;
                              'system' when auto-paused by a future
                              compliance enforcement layer)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()

PK on niche_id (not id+UUID) — one active pause per niche is the
right model. Unpause = DELETE the row; re-pause = INSERT a new one.
Lifecycle is simple by design.

## Indexes

  idx_niche_pauses_paused_until — partial index on rows with
    paused_until NOT NULL. Drives the future auto-resume sweeper
    (a job that periodically DELETEs rows whose paused_until has
    passed). Partial index because indefinite-pause rows
    (paused_until NULL) don't participate in that query.

## RLS

Applied via the standard 16-table convention (PR #535 SR-B). Admin
escape preserved.

## Foundation only

This PR ships the schema + the read/write helpers (separate file).
Consumer wires (auto_approver + publisher checking is_paused, dashboard
endpoint to read/write, React UI toggle) are follow-up PRs. The
table sits empty until an operator (or a future auto-pause
trigger) inserts a row.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f2g3h4i5j6k7"
down_revision = "e1f2g3h4i5j6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS niche_pauses (
            niche_id TEXT PRIMARY KEY,
            paused_until TIMESTAMPTZ,
            reason TEXT NOT NULL,
            paused_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_niche_pauses_paused_until
        ON niche_pauses (paused_until)
        WHERE paused_until IS NOT NULL
        """
    )

    # RLS — same 16-table standard convention (PR #535 SR-B). Admin
    # escape preserved. Pauses are per-niche by nature, so RLS works
    # naturally with the niche_id PK.
    op.execute("ALTER TABLE niche_pauses ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY niche_pauses_rls ON niche_pauses
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        )
        WITH CHECK (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS niche_pauses_rls ON niche_pauses")
    op.execute("ALTER TABLE niche_pauses DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS idx_niche_pauses_paused_until")
    op.execute("DROP TABLE IF EXISTS niche_pauses")
