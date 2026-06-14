"""publishing_analytics.metrics_fetched: boolean → timestamp with time zone

The original migration ``b2c3d4e5f6a7`` created the column as
``BOOLEAN DEFAULT false``. But every writer treats it as a timestamp:

  * ``fetch_insights.py:172`` writes ``now.isoformat()`` (an ISO string)
  * The file's own docstring describes it as the "metrics_fetched
    timestamp"
  * Every reader checks truthiness via ``if metrics_fetched:`` (so a
    timestamp works as well as the previous bool — None stays falsy,
    a datetime is truthy)

The mismatch silently broke the reward chain. On prod at the time of
this fix:

  - 378/378 rows for the last 30 days have metrics_fetched=FALSE
  - 94.2% of rows have views=0 (the views column lands via the SAME
    upsert dict that tries to set metrics_fetched to an ISO string;
    the boolean-vs-string coercion likely tanks the whole update at
    the BacklogClient layer)
  - Meanwhile ``analytics.composite`` has 488 healthy rows for the
    same window — the real engagement signal exists, it just hasn't
    been making it back to publishing_analytics

After this migration runs, the existing FALSE/NULL state is reset and
the next FetchInsights cycle will populate real timestamps + views/
likes/comments.

Revision ID: q7l8m9n0o1p2
Revises: p6k7l8m9n0o1
Create Date: 2026-06-14 18:30:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q7l8m9n0o1p2"
down_revision: str | Sequence[str] | None = "p6k7l8m9n0o1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Convert metrics_fetched from boolean to timestamp with time zone.

    The ``USING NULL`` clause is necessary because boolean → timestamp
    has no natural conversion. Since 100% of existing rows are FALSE
    (the writes have been silently dropping for the column's whole
    lifetime), we lose nothing useful — the prior "true/false" signal
    is already meaningless.

    DROP DEFAULT is needed because the prior ``DEFAULT false`` would
    not implicitly cast to a timestamp default; we go to NULL which
    is the natural "not yet fetched" sentinel and the read path's
    ``if metrics_fetched:`` already treats NULL as falsy.
    """
    op.execute("""
        ALTER TABLE publishing_analytics
            ALTER COLUMN metrics_fetched DROP DEFAULT,
            ALTER COLUMN metrics_fetched TYPE TIMESTAMP WITH TIME ZONE USING NULL
    """)


def downgrade() -> None:
    """Revert to boolean. All existing timestamps are dropped (we have
    nothing to map them to in the boolean world)."""
    op.execute("""
        ALTER TABLE publishing_analytics
            ALTER COLUMN metrics_fetched TYPE BOOLEAN USING (metrics_fetched IS NOT NULL),
            ALTER COLUMN metrics_fetched SET DEFAULT false
    """)
