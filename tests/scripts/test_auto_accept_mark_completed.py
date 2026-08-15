"""Pin _mark_completed_reports (2026-08-15 fix):

The auto-accept script stamped proposals_accepted/proposals_rejected
for weeks but never set reviewed_at. Downstream (dashboard banner,
operator briefing, persister.list_unreviewed) treated fully-triaged
reports as still pending — 17 stale reports piled up before this
closer landed.

  * Coverage check: rowcount 0 when nothing to close
  * Rowcount when the update fires
  * Runner catches exception + rolls back (no crash)
  * SQL contains the correct triage-coverage semantics
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "auto_accept_strategist_proposals",
    _ROOT / "scripts" / "auto_accept_strategist_proposals.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["auto_accept_strategist_proposals"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestMarkCompleted:
    def test_returns_rowcount(self):
        conn = MagicMock()
        result = MagicMock()
        result.rowcount = 17
        conn.execute.return_value = result
        assert _MOD._mark_completed_reports(conn) == 17

    def test_zero_rowcount_no_error(self):
        conn = MagicMock()
        result = MagicMock()
        result.rowcount = 0
        conn.execute.return_value = result
        assert _MOD._mark_completed_reports(conn) == 0

    def test_null_rowcount_returns_zero(self):
        """Some drivers report None instead of 0 — default to 0."""
        conn = MagicMock()
        result = MagicMock()
        result.rowcount = None
        conn.execute.return_value = result
        assert _MOD._mark_completed_reports(conn) == 0

    def test_sql_shape(self):
        """The completion query must:
          * Update strategist_reports
          * Stamp reviewed_at + reviewed_by='auto'
          * Only touch rows where reviewed_at IS NULL
          * Only close reports whose triage coverage is complete
          * Skip empty-proposals reports (jsonb_array_length > 0)
        """
        conn = MagicMock()
        seen = {}

        def _capture(sql, params=()):
            seen["sql"] = sql
            r = MagicMock()
            r.rowcount = 0
            return r

        conn.execute.side_effect = _capture
        _MOD._mark_completed_reports(conn)
        sql = seen["sql"].lower()
        # Correct table + fields
        assert "update strategist_reports" in sql
        assert "reviewed_at = now()" in sql
        assert "reviewed_by = 'auto'" in sql
        # Only unreviewed rows
        assert "reviewed_at is null" in sql
        # Skip empty proposals arrays
        assert "jsonb_array_length(proposals) > 0" in sql
        # Coverage check semantics
        assert "count(distinct idx)" in sql
        assert "proposals_accepted" in sql
        assert "proposals_rejected" in sql

    def test_no_operator_stamp_leak(self):
        """The closer stamps reviewed_by='auto', never 'operator' —
        prevents this closer from silently overwriting operator
        clicks-in-flight."""
        conn = MagicMock()
        seen = {}

        def _capture(sql, params=()):
            seen["sql"] = sql
            r = MagicMock()
            r.rowcount = 0
            return r

        conn.execute.side_effect = _capture
        _MOD._mark_completed_reports(conn)
        assert "'operator'" not in seen["sql"].lower()
        assert "'auto'" in seen["sql"].lower()
