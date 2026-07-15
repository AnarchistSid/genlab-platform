"""Tests for the resolve-grace period + severity-escalation dedup in
write_alerts_to_db.

Closes today's pattern where the dedup logic re-created alerts immediately
after manual resolve (because the underlying condition — e.g. zero-download
runs in the last 3 days — was still true).  The grace period treats
recently-resolved alerts as still-suppressed so manual resolutions stick
long enough for the operator to actually address the underlying issue.

2026-07-14 severity-escalation extension: the SELECT now returns
``(id, severity)`` — a warning → critical escalation IS a write even
when a same-shape lower-severity row exists in the grace window. Mocks
now pass 2-tuples ``(id, "warning"|"critical"|"info"|None)`` in
``fetchone_returns``. Legacy 1-tuple mocks still work (fall back to
rank=0 = write regardless).

These tests verify the SQL clause directly; the smoke tests on Hetzner
(``test_grace_period.py`` and ``test_grace_expires.py``) cover the
end-to-end behavior with a real Postgres.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import Alert, write_alerts_to_db


def _mock_conn(fetchone_returns: list):
    """Build a mocked psycopg.connect() context that returns the given
    fetchone() sequence (one per dedup check).
    """
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.side_effect = fetchone_returns
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=None)
    return conn, cur


class TestResolveGracePeriod:
    def test_dedup_query_includes_grace_clause(self):
        """The SELECT clause must consider recently-resolved alerts as
        still-suppressed via the OR resolved_at > NOW() - interval check.
        """
        # Existing row already at critical → incoming critical dedupes.
        conn, cur = _mock_conn(fetchone_returns=[(123, "critical")])

        with patch("psycopg.connect", return_value=conn):
            written = write_alerts_to_db([Alert("download_failure", "critical", "x", "gaming")])

        assert written == 0  # skipped because dedup hit at same severity
        # Verify the dedup SELECT was issued with the grace clause
        select_calls = [
            c.args
            for c in cur.execute.call_args_list
            if c.args and "SELECT id, severity FROM pipeline_alerts" in c.args[0]
        ]
        assert len(select_calls) == 1
        sql, params = select_calls[0]
        assert "resolved_at IS NULL" in sql
        assert "resolved_at > NOW()" in sql
        assert "::interval" in sql
        # The grace value is the third bound parameter
        assert len(params) == 3
        assert params[0] == "download_failure"
        assert params[1] == "gaming"
        # Default grace
        assert params[2] == "1 hour"

    def test_grace_value_configurable_via_env(self, monkeypatch):
        """ALERT_RESOLVE_GRACE env var overrides the 1-hour default."""
        monkeypatch.setenv("ALERT_RESOLVE_GRACE", "10 minutes")
        conn, cur = _mock_conn(fetchone_returns=[None])  # no dedup hit

        with patch("psycopg.connect", return_value=conn):
            write_alerts_to_db([Alert("git_drift", "warning", "x")])

        select_calls = [
            c.args
            for c in cur.execute.call_args_list
            if c.args and "SELECT id, severity FROM pipeline_alerts" in c.args[0]
        ]
        assert select_calls[0][1][2] == "10 minutes"

    def test_no_dedup_match_inserts(self):
        """If neither unresolved nor recently-resolved match, the alert inserts."""
        conn, cur = _mock_conn(fetchone_returns=[None])  # no dedup hit

        with patch("psycopg.connect", return_value=conn):
            written = write_alerts_to_db([Alert("warp_down", "critical", "x")])

        assert written == 1
        # Verify an INSERT was issued
        insert_calls = [
            c.args
            for c in cur.execute.call_args_list
            if c.args and c.args[0].startswith("INSERT INTO pipeline_alerts")
        ]
        assert len(insert_calls) == 1

    def test_dedup_hit_skips_insert(self):
        """If the dedup SELECT returns a row AT SAME OR HIGHER severity,
        the INSERT is skipped.

        2026-07-14: 2-tuple ``(id, severity)`` per severity-escalation
        extension. Same critical vs critical → skip.
        """
        conn, cur = _mock_conn(fetchone_returns=[(456, "critical")])

        with patch("psycopg.connect", return_value=conn):
            written = write_alerts_to_db([Alert("qc_collapse", "critical", "x", "sports")])

        assert written == 0
        insert_calls = [
            c.args
            for c in cur.execute.call_args_list
            if c.args and c.args[0].startswith("INSERT INTO pipeline_alerts")
        ]
        assert insert_calls == []

    def test_escalation_writes_over_lower_severity(self):
        """warning → critical MUST NOT dedup — the operator needs to
        see the escalation, not stay on a stale warning banner.

        2026-07-14: severity-escalation pin (audit F1 that was reverted
        in f300cde2 for test-surgery scope). Now landed.
        """
        conn, cur = _mock_conn(fetchone_returns=[(789, "warning")])

        with patch("psycopg.connect", return_value=conn):
            written = write_alerts_to_db([Alert("qc_collapse", "critical", "x", "sports")])

        assert written == 1  # escalation forces write
        insert_calls = [
            c.args
            for c in cur.execute.call_args_list
            if c.args and c.args[0].startswith("INSERT INTO pipeline_alerts")
        ]
        assert len(insert_calls) == 1

    def test_deescalation_still_dedupes(self):
        """critical → warning must NOT rewrite. Operator already sees
        critical; downgrading to warning would be misleading.
        """
        conn, cur = _mock_conn(fetchone_returns=[(999, "critical")])

        with patch("psycopg.connect", return_value=conn):
            written = write_alerts_to_db([Alert("qc_collapse", "warning", "x", "sports")])

        assert written == 0
        insert_calls = [
            c.args
            for c in cur.execute.call_args_list
            if c.args and c.args[0].startswith("INSERT INTO pipeline_alerts")
        ]
        assert insert_calls == []

    def test_multiple_alerts_independent_dedup(self):
        """Each alert is dedup'd independently — one hit doesn't skip others."""
        # First alert hits dedup (same critical), second one has no row.
        conn, cur = _mock_conn(fetchone_returns=[(1, "critical"), None])

        with patch("psycopg.connect", return_value=conn):
            written = write_alerts_to_db(
                [
                    Alert("download_failure", "critical", "a", "gaming"),
                    Alert("warp_down", "critical", "b"),
                ]
            )

        assert written == 1  # only the second inserted

    def test_niche_id_none_uses_is_not_distinct_from(self):
        """System-wide alerts (niche_id=None) need IS NOT DISTINCT FROM
        for correct NULL handling — '=' doesn't match NULL.
        """
        conn, cur = _mock_conn(fetchone_returns=[None])

        with patch("psycopg.connect", return_value=conn):
            write_alerts_to_db([Alert("git_drift", "critical", "x")])

        select_calls = [
            c.args
            for c in cur.execute.call_args_list
            if c.args and "SELECT id, severity FROM pipeline_alerts" in c.args[0]
        ]
        assert "IS NOT DISTINCT FROM" in select_calls[0][0]
