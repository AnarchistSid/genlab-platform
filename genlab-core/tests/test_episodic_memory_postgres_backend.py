"""Pin: PostgresBackend for episodic_memory (Lever R1 production backend).

PR #441 shipped the EpisodicBackend Protocol + InMemoryBackend
(test-only). THIS test file pins the PostgresBackend that lands the
production storage path — durable persistence to the
``episodic_events`` table (migration ``y5t6u7v8w9x0``).

The backend satisfies the runtime_checkable Protocol via duck typing
and degrades fail-quiet when DATABASE_URL is unset or Postgres is
unreachable. THIS is the test scaffold for that contract; integration
tests (real Postgres round-trips) live alongside the migration tests.

These pins lock in:

  Protocol compliance (2):
  1. PostgresBackend satisfies EpisodicBackend via isinstance check
  2. PostgresBackend can be constructed with default args + niche_id

  Fail-quiet (4):
  3. record() with no pool → no-op (no crash, returns None silently)
  4. query() with no pool → returns []
  5. record() with pool but DB raises → silent no-op
  6. query() with pool but DB raises → returns []

  Query routing (2):
  7. query(niche_id=X) → SQL includes WHERE niche_id = $1
  8. query() with no filter → SQL omits the WHERE

  Filter delegation (1):
  9. PostgresBackend.query applies filter_events on returned rows,
     so event_type / since / until / limit semantics match InMemoryBackend
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.learning.episodic_memory import (
    EVENT_PUBLISH,
    EpisodicBackend,
    EpisodicEvent,
    PostgresBackend,
    new_event,
)


def _patched_pool_returning(rows):
    """Build a fake pool where query() returns the supplied rows."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.execute = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda self: cur
    conn.cursor.return_value.__exit__ = lambda *a: None
    pool = MagicMock()
    pool.connection.return_value.__enter__ = lambda self: conn
    pool.connection.return_value.__exit__ = lambda *a: None
    return pool, cur


class TestProtocolCompliance:
    def test_satisfies_episodic_backend_via_isinstance(self):
        """Pin: PostgresBackend duck-types the Protocol — no explicit
        subclass needed. Same pattern as InMemoryBackend."""
        backend = PostgresBackend()
        assert isinstance(backend, EpisodicBackend)

    def test_constructs_with_default_and_explicit_niche_id(self):
        """Pin: backend constructor is cheap + tolerant. niche_id
        kwarg is reserved for future RLS scoping; harmless when unset."""
        b1 = PostgresBackend()
        b2 = PostgresBackend(niche_id="gaming")
        assert isinstance(b1, EpisodicBackend)
        assert isinstance(b2, EpisodicBackend)


class TestFailQuiet:
    def test_record_no_pool_is_noop(self, monkeypatch):
        """Pin: DATABASE_URL unset / pool init failed → record returns
        None silently. Agent emit calls never block on storage."""
        with patch(
            "genlab_core.learning.episodic_memory._get_pg_pool",
            return_value=None,
        ):
            backend = PostgresBackend()
            event = new_event(event_type=EVENT_PUBLISH, niche_id="gaming")
            result = backend.record(event)
            assert result is None  # no-op

    def test_query_no_pool_returns_empty(self):
        with patch(
            "genlab_core.learning.episodic_memory._get_pg_pool",
            return_value=None,
        ):
            backend = PostgresBackend()
            assert backend.query() == []
            assert backend.query(niche_id="gaming") == []

    def test_record_db_exception_is_silent(self):
        """Pin: any storage exception (network blip, lock timeout,
        constraint violation) is caught and logged at debug. Caller
        sees None, NOT a propagated exception."""
        pool = MagicMock()
        pool.connection.side_effect = RuntimeError("DB outage")
        with patch(
            "genlab_core.learning.episodic_memory._get_pg_pool",
            return_value=pool,
        ):
            backend = PostgresBackend()
            event = new_event(event_type=EVENT_PUBLISH, niche_id="gaming")
            backend.record(event)  # MUST NOT raise

    def test_query_db_exception_returns_empty(self):
        pool = MagicMock()
        pool.connection.side_effect = RuntimeError("DB outage")
        with patch(
            "genlab_core.learning.episodic_memory._get_pg_pool",
            return_value=pool,
        ):
            backend = PostgresBackend()
            assert backend.query(niche_id="gaming") == []


class TestQueryRouting:
    def test_niche_filter_pushed_to_sql(self):
        """Pin: when niche_id is given, the WHERE clause is in SQL
        (pre-filter before Python). Important for large tables."""
        pool, cur = _patched_pool_returning([])
        with patch(
            "genlab_core.learning.episodic_memory._get_pg_pool",
            return_value=pool,
        ):
            backend = PostgresBackend()
            backend.query(niche_id="gaming")

        # The execute call should have included niche_id WHERE clause
        sql_text, params = cur.execute.call_args[0]
        assert "WHERE niche_id" in sql_text
        assert params == ("gaming",)

    def test_no_filter_omits_where_clause(self):
        """Pin: query() with no niche filter → no WHERE in SQL,
        just an ORDER BY / LIMIT."""
        pool, cur = _patched_pool_returning([])
        with patch(
            "genlab_core.learning.episodic_memory._get_pg_pool",
            return_value=pool,
        ):
            backend = PostgresBackend()
            backend.query()

        sql_text = cur.execute.call_args[0][0]
        assert "WHERE" not in sql_text
        assert "ORDER BY" in sql_text


class TestFilterDelegation:
    def test_event_type_filter_applied_in_python(self):
        """Pin: SQL pulls a broad window (LIMIT 5000); event_type +
        since/until/limit are filtered in Python via filter_events.
        This keeps SQL simple AND the filter semantics consistent with
        InMemoryBackend (no SQL/Python skew)."""
        from datetime import UTC, datetime

        # Two rows in the DB: one publish, one operator_review
        rows = [
            (
                EVENT_PUBLISH,
                "gaming",
                "bp_1",
                datetime(2026, 6, 22, tzinfo=UTC),
                {},
            ),
            (
                "operator_review",
                "gaming",
                "bp_2",
                datetime(2026, 6, 22, tzinfo=UTC),
                {"action": "approved"},
            ),
        ]
        pool, _ = _patched_pool_returning(rows)
        with patch(
            "genlab_core.learning.episodic_memory._get_pg_pool",
            return_value=pool,
        ):
            backend = PostgresBackend()
            # Filter to just publishes via Python-side filter
            result = backend.query(niche_id="gaming", event_type=EVENT_PUBLISH)

        assert len(result) == 1
        assert result[0].event_type == EVENT_PUBLISH
        assert isinstance(result[0], EpisodicEvent)
