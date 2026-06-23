"""create tier_history table for sponsorship tier-transition detection

Revision ID: a7b8c9d0e1f2
Revises: z6u7v8w9x0y1
Create Date: 2026-06-23 10:30:00.000000+00:00

PR II (2026-06-23): closes the last significant gap in the operator-
leverage loop (PRs #481-#495). When a niche's sponsorship-readiness
tier changes (e.g. ``within_2_months`` → ``eligible_now``), the
operator currently has to spot it manually by comparing today's
dashboard to yesterday's. This migration creates the table that
makes transition-detection a passive surface.

## Schema

  tier_history
    id              SERIAL PRIMARY KEY
    niche_id        TEXT NOT NULL
    tier            TEXT NOT NULL         -- new tier (e.g. "eligible_now")
    prev_tier       TEXT NULL             -- prior tier; NULL for first-ever record
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    nearest_threshold_days INTEGER NULL   -- snapshot of the projection at this transition

  INDEX (niche_id, observed_at DESC)      -- "recent transitions per niche" query
  INDEX (observed_at DESC)                -- "all recent transitions" query

## Why append-only

Each row is a transition event. Reading "current tier per niche"
= SELECT DISTINCT ON (niche_id) ... ORDER BY observed_at DESC.
Reading "recent transitions" = SELECT WHERE observed_at >= cutoff.
No UPDATE / DELETE paths — append-only history is the simplest
shape for a time-series ledger.

## RLS

niche_isolation policy — same shape as other tables in this
project (monetisationprogress, publishing_analytics, etc.).
``app.niche_id`` setting controls visibility; 'all' for cross-
niche reads from the dashboard.

## Concurrent writes

The ``/api/v1/sponsorship/readiness`` endpoint fires every 60s
from each open dashboard tab. Multiple tabs → multiple concurrent
write attempts on the same transition. ON CONFLICT semantics not
needed because each transition is a new row (insert-only) — the
detection-logic ON CONFLICT happens at the prior-state-cache
table level instead (see ``last_seen_tier`` below).

Actually we don't need ``last_seen_tier`` at all — the SELECT
DISTINCT ON pattern reads the current tier directly from
tier_history. Detection logic: compute current tier, compare to
SELECT DISTINCT ON (niche_id) tier FROM tier_history ORDER BY
observed_at DESC. If different → INSERT. Idempotent: multiple
concurrent writes for the same transition create multiple rows,
but the dashboard's "recent transitions in window" query
deduplicates by latest-per-niche.

Even simpler — the duplicate-row risk only matters when N tabs
all observe a transition simultaneously. Once one of them writes,
the others' comparison finds the new tier and skips. Race window
is tiny (one network round-trip), worst case 2-3 duplicate rows
per transition, harmless for the dashboard's display.
"""

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "z6u7v8w9x0y1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS tier_history (
        id BIGSERIAL PRIMARY KEY,
        niche_id TEXT NOT NULL,
        tier TEXT NOT NULL,
        prev_tier TEXT NULL,
        observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        nearest_threshold_days INTEGER NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tier_history_niche_observed
        ON tier_history(niche_id, observed_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tier_history_observed
        ON tier_history(observed_at DESC);

    ALTER TABLE tier_history ENABLE ROW LEVEL SECURITY;
    ALTER TABLE tier_history FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS niche_isolation ON tier_history;
    CREATE POLICY niche_isolation ON tier_history
        USING (
            niche_id = current_setting('app.niche_id', true)
            OR current_setting('app.niche_id', true) IN ('', 'all')
            OR current_setting('app.niche_id', true) IS NULL
        );

    GRANT ALL ON tier_history TO genlab;
    GRANT USAGE, SELECT ON SEQUENCE tier_history_id_seq TO genlab;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tier_history CASCADE")
