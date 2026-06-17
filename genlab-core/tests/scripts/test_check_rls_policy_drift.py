"""Tests for the RLS drift detector.

The actual DB scan is mocked end-to-end (no live psql); we pin:
  * Drift classification across the 3 classes
  * Known-non-canonical-policy allowlist (affiliate_clicks)
  * "No drift" case returns 0 + writes no alert
  * Alert message includes class names + sample tables
  * --dry-run skips alert write
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_rls_policy_drift.py"
spec = importlib.util.spec_from_file_location("check_rls_policy_drift", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["check_rls_policy_drift"] = mod
spec.loader.exec_module(mod)


def _fake_conn(policy_rows: list[tuple[str, str, bool]]):
    """Build a mock conn whose pg_policy SELECT returns the given rows.

    Each row is (table_name, policy_name, has_with_check).
    """
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = policy_rows
    cur.execute = MagicMock()

    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ── Drift classification ──────────────────────────────────────────────


class TestScan:
    def _everything_present(self):
        """A pg_policy row set where every expected table has the
        canonical niche_isolation policy with WITH CHECK."""
        rows = []
        for tbl in mod._EXPECTED_RLS_TABLES:
            if tbl == "affiliate_clicks":
                # SR-F'd legitimate non-canonical
                rows.append((tbl, "affiliate_clicks_niche_policy", True))
            else:
                rows.append((tbl, mod.CANONICAL_POLICY_NAME, True))
        return rows

    def test_no_drift_returns_empty_lists(self):
        conn, _ = _fake_conn(self._everything_present())
        drift = mod.scan(conn)
        assert drift == {
            "missing_policy": [],
            "missing_with_check": [],
            "unexpected_policy_name": [],
        }

    def test_missing_policy_detected(self):
        """A table in _EXPECTED_RLS_TABLES that has no policy at all
        appears in missing_policy."""
        rows = [r for r in self._everything_present() if r[0] != "blueprints"]
        conn, _ = _fake_conn(rows)
        drift = mod.scan(conn)
        assert "blueprints" in drift["missing_policy"]
        assert drift["missing_with_check"] == []
        assert drift["unexpected_policy_name"] == []

    def test_missing_with_check_detected(self):
        """Policy exists but WITH CHECK is False → missing_with_check."""
        rows = [
            (tbl, "niche_isolation", False if tbl == "stories" else True)
            for tbl in mod._EXPECTED_RLS_TABLES
            if tbl != "affiliate_clicks"
        ] + [("affiliate_clicks", "affiliate_clicks_niche_policy", True)]
        conn, _ = _fake_conn(rows)
        drift = mod.scan(conn)
        assert any("stories" in entry for entry in drift["missing_with_check"])
        assert drift["missing_policy"] == []

    def test_unexpected_policy_name_detected(self):
        """A table with a non-canonical name that's NOT in the
        allowlist → unexpected_policy_name."""
        rows = [r for r in self._everything_present() if r[0] != "blueprints"] + [
            ("blueprints", "weird_other_name", True)
        ]
        conn, _ = _fake_conn(rows)
        drift = mod.scan(conn)
        assert any("blueprints" in entry for entry in drift["unexpected_policy_name"])
        assert drift["missing_policy"] == []

    def test_known_non_canonical_is_not_drift(self):
        """affiliate_clicks_niche_policy is in the allowlist — it
        must NOT appear in unexpected_policy_name when present."""
        conn, _ = _fake_conn(self._everything_present())
        drift = mod.scan(conn)
        # affiliate_clicks is in the allowlist with that exact policy name
        assert not any("affiliate_clicks" in entry for entry in drift["unexpected_policy_name"])

    def test_extra_unlisted_tables_ignored(self):
        """A table NOT in _EXPECTED_RLS_TABLES doesn't contribute to
        drift — we don't have an opinion about it."""
        rows = self._everything_present() + [("not_in_list_table", "some_policy", True)]
        conn, _ = _fake_conn(rows)
        drift = mod.scan(conn)
        # not_in_list_table doesn't appear anywhere
        assert all("not_in_list_table" not in s for s in drift["missing_policy"])
        assert all("not_in_list_table" not in s for s in drift["unexpected_policy_name"])


# ── write_alert message shape ─────────────────────────────────────────


class TestWriteAlert:
    def test_alert_contains_drift_classes_and_tables(self):
        captured: list = []
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.execute = lambda sql, params=None: captured.append((sql, params))
        conn = MagicMock()
        conn.cursor.return_value = cur

        drift = {
            "missing_policy": ["bp_a", "bp_b"],
            "missing_with_check": ["stories.niche_isolation"],
            "unexpected_policy_name": [],
        }
        mod.write_alert(conn, drift)

        inserts = [
            (sql, params)
            for sql, params in captured
            if isinstance(sql, str) and "INSERT INTO pipeline_alerts" in sql
        ]
        assert inserts, f"expected an INSERT INTO pipeline_alerts; got: {captured}"
        sql, params = inserts[0]
        # severity is lowercase (post-PR #301)
        assert "'critical'" in sql
        # Message mentions both drift classes
        message = params[0]
        assert "missing_policy" in message
        assert "missing_with_check" in message
        # And sample table names
        assert "bp_a" in message
        assert "stories.niche_isolation" in message


# ── main() exit codes ─────────────────────────────────────────────────


class TestMain:
    def test_no_db_url_returns_0(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert mod.main([]) == 0

    def test_db_connect_failure_returns_2(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://does-not-resolve/x")
        import unittest.mock

        with unittest.mock.patch.object(mod, "_connect", side_effect=RuntimeError("nope")):
            assert mod.main([]) == 2

    def test_no_drift_returns_0(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        rows = []
        for tbl in mod._EXPECTED_RLS_TABLES:
            if tbl == "affiliate_clicks":
                rows.append((tbl, "affiliate_clicks_niche_policy", True))
            else:
                rows.append((tbl, mod.CANONICAL_POLICY_NAME, True))
        conn, _ = _fake_conn(rows)
        import unittest.mock

        with unittest.mock.patch.object(mod, "_connect", return_value=conn):
            assert mod.main([]) == 0

    def test_drift_returns_1_and_writes_alert(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        rows = [
            (tbl, mod.CANONICAL_POLICY_NAME, True)
            for tbl in mod._EXPECTED_RLS_TABLES
            if tbl not in ("blueprints", "affiliate_clicks")
        ] + [("affiliate_clicks", "affiliate_clicks_niche_policy", True)]
        # blueprints missing
        conn, cur = _fake_conn(rows)
        import unittest.mock

        with unittest.mock.patch.object(mod, "_connect", return_value=conn):
            rc = mod.main([])
        assert rc == 1
        # Verify an INSERT INTO pipeline_alerts was sent
        write_calls = [
            c
            for c in cur.execute.call_args_list
            if c.args and "INSERT INTO pipeline_alerts" in str(c.args[0])
        ]
        assert write_calls, "drift was found but no alert was written"

    def test_dry_run_returns_1_but_no_alert_written(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        rows = [
            (tbl, mod.CANONICAL_POLICY_NAME, True)
            for tbl in mod._EXPECTED_RLS_TABLES
            if tbl not in ("blueprints", "affiliate_clicks")
        ] + [("affiliate_clicks", "affiliate_clicks_niche_policy", True)]
        conn, cur = _fake_conn(rows)
        import unittest.mock

        with unittest.mock.patch.object(mod, "_connect", return_value=conn):
            rc = mod.main(["--dry-run"])
        assert rc == 1
        write_calls = [
            c
            for c in cur.execute.call_args_list
            if c.args and "INSERT INTO pipeline_alerts" in str(c.args[0])
        ]
        assert not write_calls, "--dry-run must NOT write an alert"
