"""bandit_validation: daily rank-correlation snapshots per niche

Captures one row per (niche, day) of the bandit's predictive validity
test: spearman + pearson rank correlation between the bandit's
per-source posterior mean and the post's 48h viral_score. The
``check_three_day_ineffective`` consumer reads the last 3 days to
trigger a WARN if every day shows ``interpretation='bandit ineffective'``.

Why a separate table (not a JSONB column)
=========================================

* Dashboard charts need straight SQL — one row per (niche, day) lets
  the operator-facing line-chart query be
  ``SELECT computed_at, spearman FROM bandit_validation WHERE niche_id=%s``
  with no JSON traversal.
* Idempotency: the cron runs daily; if it fires twice (e.g. systemd
  retry on transient DB hiccup), we want two snapshots, not one
  overwriting the other — debugging "what did the bandit look like
  Tuesday morning AND Tuesday afternoon" needs both rows preserved.
* Small footprint: 5 niches × 365 days × ~80 bytes = ~145 KB/year.

Why no UNIQUE constraint on (niche_id, DATE(computed_at))
=========================================================

The harness intentionally allows multiple snapshots per day. Manual
runs (operator triaging "did the bandit recover this morning?") and
the cron's retry-on-failure both produce extra rows. The dashboard
query uses ``ORDER BY computed_at DESC LIMIT 1`` to take the freshest
per-day snapshot when displaying point-in-time values.

Indexes
=======

* ``idx_bv_niche_computed`` on ``(niche_id, computed_at DESC)`` — hot
  path for the dashboard line chart + the 3-day check.
* No index on interpretation alone — selectivity is too low (only 4
  enum-like values), and every query that filters interpretation also
  filters niche_id, so the composite index above suffices.

RLS
===

Standard niche_isolation policy — matches the rest of the
per-niche tables (publishing_analytics, analytics, bandit_arms).

Revision ID: w4r5s6t7u8v9
Revises: v3q4r5s6t7u8
Create Date: 2026-06-30 18:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w4r5s6t7u8v9"
down_revision: str | Sequence[str] | None = "v3q4r5s6t7u8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the bandit_validation table + index + RLS."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bandit_validation (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            computed_at TIMESTAMPTZ DEFAULT now() NOT NULL,
            n_posts INTEGER NOT NULL DEFAULT 0,
            spearman REAL NOT NULL DEFAULT 0.0,
            pearson REAL NOT NULL DEFAULT 0.0,
            top_quartile_lift REAL NOT NULL DEFAULT 1.0,
            interpretation TEXT NOT NULL DEFAULT 'low signal'
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bv_niche_computed
        ON bandit_validation (niche_id, computed_at DESC)
        """
    )

    op.execute("ALTER TABLE bandit_validation ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE bandit_validation FORCE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS niche_isolation ON bandit_validation")
    op.execute(
        """
        CREATE POLICY niche_isolation ON bandit_validation
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

    op.execute("GRANT ALL ON bandit_validation TO genlab")


def downgrade() -> None:
    """Drop the bandit_validation table + index."""
    op.execute("DROP INDEX IF EXISTS idx_bv_niche_computed")
    op.execute("DROP TABLE IF EXISTS bandit_validation CASCADE")
