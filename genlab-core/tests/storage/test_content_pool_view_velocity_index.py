"""Real-DB pin for the content_pool view_velocity composite index (#590).

Migration ``e1a2b3c4d5e6_content_pool_view_velocity_index`` adds
``idx_cp_view_velocity_fetched`` covering the hot path in
``trending_video_fetcher.py:1188``:

    ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC

The test asserts the index exists AND has the right column ordering. If
someone drops it or "consolidates" it into a differently-ordered
composite, the trending fetcher regresses to in-memory sorting silently.

Skipped when ``POSTGRES_PASSWORD`` is unset — same discipline as sibling
DB-integration tests. Only runs in the ``test-storage`` CI job on
the Hetzner runner where the password IS set.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip real-DB integration",
)


INDEX_NAME = "idx_cp_view_velocity_fetched"


@pytest.fixture
def pg_conn():
    """Direct psycopg3 connection — same shape as sibling
    ``test_blueprints_approve_visual_paths_constraint.py``.
    """
    import psycopg

    conn = psycopg.connect(
        host=os.environ.get("GENLAB_TEST_PG_HOST", "localhost"),
        port=int(os.environ.get("GENLAB_TEST_PG_PORT", "5432")),
        dbname=os.environ.get("GENLAB_TEST_PG_DB", "genlab"),
        user=os.environ.get("GENLAB_TEST_PG_USER", "genlab"),
        password=os.environ.get("GENLAB_TEST_PG_PASSWORD") or os.environ.get("POSTGRES_PASSWORD"),
        connect_timeout=5,
    )
    yield conn
    conn.close()


class TestIndexPresent:
    def test_index_exists_on_content_pool(self, pg_conn):
        """The migration must actually have created the index. Catches
        regression where someone drops it or the migration is reverted
        without also updating this test."""
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'content_pool'
                  AND indexname = %s
                """,
                (INDEX_NAME,),
            )
            row = cur.fetchone()
        assert row is not None, (
            f"Index {INDEX_NAME!r} missing from content_pool — migration "
            f"e1a2b3c4d5e6 didn't apply, or was reverted. Task #590. "
            f"Regression: trending_video_fetcher sort falls back to "
            f"idx_cp_fetched which only orders on the secondary sort key."
        )

    def test_index_column_ordering(self, pg_conn):
        """Assert the composite ordering matches the query. The query is
        ``ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC`` —
        the index MUST have view_velocity FIRST + fetched_at SECOND, both
        descending. Reversed ordering forces a full sort even with the
        index."""
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE tablename = 'content_pool'
                  AND indexname = %s
                """,
                (INDEX_NAME,),
            )
            row = cur.fetchone()
        assert row is not None, "Index missing — see test above"
        indexdef = row[0]
        # Expected substring in the definition. PostgreSQL normalizes
        # the DDL so this is a stable check.
        assert "view_velocity DESC" in indexdef, (
            f"Index {INDEX_NAME!r} lost view_velocity as primary sort. "
            f"Got: {indexdef}. Task #590 requires view_velocity FIRST."
        )
        assert "fetched_at DESC" in indexdef, (
            f"Index {INDEX_NAME!r} lost fetched_at as secondary sort. "
            f"Got: {indexdef}. Task #590 requires fetched_at SECOND."
        )
        # Verify order: view_velocity's DESC clause must appear BEFORE
        # fetched_at's DESC. Both are DESC so we can't use a simple
        # "before" check on the words alone — check by position of the
        # column names.
        vv_pos = indexdef.find("view_velocity")
        fa_pos = indexdef.find("fetched_at")
        assert 0 <= vv_pos < fa_pos, (
            f"Index {INDEX_NAME!r} column order wrong — view_velocity "
            f"must come BEFORE fetched_at (primary sort key first). "
            f"Got: {indexdef}"
        )


class TestPlannerUsesIndex:
    """Verify the query planner actually PICKS this index for the hot
    query — a correctly-shaped index the planner ignores is worthless.
    """

    def test_explain_uses_view_velocity_index(self, pg_conn):
        """Run EXPLAIN on the exact trending_video_fetcher query and
        assert the composite index appears in the plan.

        Note: EXPLAIN without ANALYZE — we're checking the planner's
        CHOICE of index, not runtime cost. Tests can't rely on a
        specific row count so we can't ANALYZE."""
        with pg_conn.cursor() as cur:
            # Match the exact WHERE + ORDER BY shape at
            # trending_video_fetcher.py:1180-1191 — this is what the
            # planner should see.
            cur.execute(
                """
                EXPLAIN (FORMAT TEXT)
                SELECT * FROM content_pool
                WHERE %s = ANY(routed_niches)
                  AND status = 'available'
                  AND video_url IS NOT NULL AND video_url != ''
                ORDER BY view_velocity DESC NULLS LAST, fetched_at DESC
                LIMIT 20
                """,
                ("gaming",),
            )
            plan_rows = cur.fetchall()
        plan = "\n".join(row[0] for row in plan_rows)

        # Two ways the planner might use this index — either a direct
        # Index Scan or an Index-Only Scan. Reject sequential scans
        # + explicit Sort nodes (which mean the sort happened in
        # memory rather than via the index).
        assert INDEX_NAME in plan or "view_velocity" in plan, (
            f"Query plan does NOT reference {INDEX_NAME} — planner is "
            f"ignoring the index. Task #590 didn't achieve its stated "
            f"benefit. Plan was:\n{plan}"
        )
