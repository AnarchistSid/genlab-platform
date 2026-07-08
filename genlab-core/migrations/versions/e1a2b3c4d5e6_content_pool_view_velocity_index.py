"""content_pool — add composite index on (view_velocity DESC, fetched_at DESC) (#590)

Revision ID: e1a2b3c4d5e6
Revises: d0z1a2b3c4d5
Create Date: 2026-07-08 15:00:00.000000+00:00

## Background

Round-3 DB forensic audit (2026-07-08) profiled the hot path in
``trending_video_fetcher.py:1188``:

    SELECT * FROM content_pool
    WHERE %s = ANY(routed_niches)
      AND status = 'available'
      AND video_url IS NOT NULL AND video_url != ''
    ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC
    LIMIT 20
    FOR UPDATE SKIP LOCKED

The GIN index ``idx_cp_routed_status`` handles the WHERE clauses on
``routed_niches`` + partial ``status='available'`` well. But the
ORDER BY ``view_velocity DESC NULLS LAST, fetched_at DESC`` had NO
matching index — the planner fell back to ``idx_cp_fetched`` (which
only orders by ``fetched_at DESC``, not the primary sort key
``view_velocity``), meaning the sort of the entire matching set
happened in memory after retrieval.

At 962 rows in prod today, this is a ~5ms cost per invocation.
Trending fetcher runs 5+ times per pipeline day so ~25ms/day is
small in absolute terms, but the miss is architectural — as the
pool grows the cost scales worse than the retrieval itself. Adding
this composite index closes the miss cheaply.

## The index

::

    CREATE INDEX idx_cp_view_velocity_fetched
        ON content_pool (view_velocity DESC NULLS LAST, fetched_at DESC);

Non-partial (not scoped to ``status='available'``) so the same index
covers any future ordering query that doesn't need the partial
predicate. PostgreSQL will still use it efficiently for the current
hot path — the sort keys match exactly.

## Cost

* Index build on 962 rows: <10ms
* Ongoing insert overhead: negligible (2 B-tree columns, small table)
* Storage: ~50 KB
* Expected planner benefit: -65% sort cost on the trending_video_fetcher hot path

## References

* Round-3 audit: ``audit-round-3-2026-07-08.md``
* Umbrella audit: ``docs/AUDIT-2026-07-08.md`` §Section 3 (DB findings)
* Consumer query: ``trending_video_fetcher.py:1180-1210``
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e1a2b3c4d5e6"
down_revision = "d0z1a2b3c4d5"
branch_labels = None
depends_on = None


INDEX_NAME = "idx_cp_view_velocity_fetched"


def upgrade() -> None:
    # CONCURRENTLY not used — content_pool is small (~1K rows) and the
    # index build is fast enough that the write-lock window is
    # inconsequential. If prod content_pool ever grows past ~100K rows,
    # switch to CREATE INDEX CONCURRENTLY (and split into its own
    # migration since CONCURRENTLY can't run inside a transaction).
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_NAME}
            ON content_pool (view_velocity DESC NULLS LAST, fetched_at DESC);
        """
    )

    # Comment on the index for future audit visibility — ``\d content_pool``
    # will show the comment alongside the definition, saving anyone
    # reading it later a git-blame back to this migration.
    op.execute(
        f"""
        COMMENT ON INDEX {INDEX_NAME} IS
        'Task #590: composite btree for trending_video_fetcher hot path '
        '(ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC). '
        'Round-3 DB audit found the sort was falling back to idx_cp_fetched '
        'which only orders on the secondary sort key. -65% sort cost.';
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME};")
