"""Pin the Phase 0.B fix: state_collector._channel_metrics reads
follower_count from audience_snapshots (not analytics.metric_type=
'follower_count' which never had rows).

Regression prevention: the strategist's 3+ "implement follower
baseline instrumentation" proposals over July/August 2026 were
symptoms of this read-side bug, not a missing writer. Test pins that
the fix routes to the right table.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.intelligence.state_collector import PostgresStateCollector


class _StubConn:
    """Records executed SQL + returns queued results. Not thread-safe."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self._results: list = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        cur = MagicMock()
        result = self._results.pop(0) if self._results else None
        cur.fetchone.return_value = result
        cur.fetchall.return_value = result if isinstance(result, list) else []
        return cur

    def rollback(self):
        pass

    def queue(self, result):
        self._results.append(result)


class TestChannelMetricsFollowerRead:
    def test_queries_audience_snapshots_not_analytics_for_followers(self):
        conn = _StubConn()
        # audience_snapshots query returns 10032 followers
        conn.queue({"total_followers": 10032.0})
        # engagement analytics query returns empty
        conn.queue({"er7d": None, "wt7d": None, "n7d": None})

        c = PostgresStateCollector(conn)
        out = c._channel_metrics("ai_creators")

        assert out["follower_count"] == 10032.0
        # First query must hit audience_snapshots for follower data
        first_sql = conn.calls[0][0]
        assert "audience_snapshots" in first_sql, (
            "Phase 0.B fix: follower count must come from audience_snapshots, "
            "NOT analytics.metric_type='follower_count' (that path had 0 rows "
            "for months while snapshots were being written)"
        )
        assert "'followers'" in first_sql or "followers" in first_sql

    def test_engagement_still_from_analytics(self):
        conn = _StubConn()
        conn.queue({"total_followers": 100})
        conn.queue({"er7d": 0.05, "wt7d": 12.3, "n7d": 5})

        c = PostgresStateCollector(conn)
        out = c._channel_metrics("gaming")

        assert out["engagement_rate_7d"] == 0.05
        assert out["watch_time_avg_7d"] == 12.3
        assert out["n_publishes_7d"] == 5
        # Second query hits analytics (per-post engagement)
        second_sql = conn.calls[1][0]
        assert "FROM analytics" in second_sql

    def test_sums_across_platforms(self):
        """Follower count is niche-level (sum of FB + IG + YT etc)."""
        conn = _StubConn()
        conn.queue({"total_followers": 10165})  # 10032 FB + 165 IG + ...
        conn.queue({"er7d": None, "wt7d": None, "n7d": 0})

        c = PostgresStateCollector(conn)
        out = c._channel_metrics("ai_creators")

        assert out["follower_count"] == 10165
        # Query should sum via SUM() and use latest per platform
        sql = conn.calls[0][0]
        assert "SUM(" in sql
        assert "DISTINCT ON" in sql or "latest" in sql.lower()

    def test_none_when_no_snapshots(self):
        conn = _StubConn()
        conn.queue({"total_followers": None})
        conn.queue({"er7d": None, "wt7d": None, "n7d": 0})

        c = PostgresStateCollector(conn)
        out = c._channel_metrics("anime")

        assert out["follower_count"] is None
        assert out["n_publishes_7d"] == 0

    def test_db_error_returns_partial_metrics(self):
        """Follower query failure shouldn't null engagement metrics too."""
        conn = _StubConn()
        # First query returns nothing (simulating fail-through in _safe)
        conn.queue(None)
        conn.queue({"er7d": 0.03, "wt7d": None, "n7d": 3})

        c = PostgresStateCollector(conn)
        out = c._channel_metrics("sports")

        assert out["follower_count"] is None
        assert out["engagement_rate_7d"] == 0.03
        assert out["n_publishes_7d"] == 3

    def test_stale_snapshots_excluded(self):
        """Snapshots older than 14 days should not count as current state."""
        conn = _StubConn()
        conn.queue({"total_followers": 500})
        conn.queue({"er7d": None, "wt7d": None, "n7d": 0})

        c = PostgresStateCollector(conn)
        c._channel_metrics("movies")

        sql = conn.calls[0][0]
        assert "14 days" in sql or "INTERVAL" in sql, (
            "Query must exclude stale snapshots so a decommissioned "
            "platform doesn't count toward current followers"
        )
