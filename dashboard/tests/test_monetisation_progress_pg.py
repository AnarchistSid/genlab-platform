"""Tests for :mod:`server.core.monetisation_progress_pg`.

Audit ref: R-38.

Mocks psycopg.connect — no live DB needed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.core import monetisation_progress_pg


def _make_mock_conn(rows: list[dict]) -> MagicMock:
    """Build a psycopg connection mock that returns ``rows`` on fetchall."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


# ---------------------------------------------------------------------------
# fetch_progress — happy path
# ---------------------------------------------------------------------------


class TestFetchProgress:
    def test_returns_flat_dicts(self) -> None:
        rows = [
            {
                "niche_id": "gaming",
                "platform": "instagram",
                "metric_name": "followers",
                "current_value": 12000.0,
                "target_value": 10000.0,
                "pct_complete": 1.2,
                "delta_7d": 500.0,
                "days_to_threshold_est": None,
                "is_threshold_met": True,
                "data_source": "api",
                "as_of_date": "2026-06-09",
                "error_log": None,
            }
        ]
        with patch("psycopg.connect", return_value=_make_mock_conn(rows)):
            result = monetisation_progress_pg.fetch_progress()

        assert len(result) == 1
        # Flat dict, no {"fields": ...} wrapping.
        assert result[0]["niche_id"] == "gaming"
        assert result[0]["is_threshold_met"] is True
        assert "fields" not in result[0]

    def test_returns_empty_list_when_table_empty(self) -> None:
        with patch("psycopg.connect", return_value=_make_mock_conn([])):
            assert monetisation_progress_pg.fetch_progress() == []

    def test_returns_empty_list_on_connection_failure(self) -> None:
        # Postgres unreachable — the dashboard must NOT 500.
        with patch("psycopg.connect", side_effect=Exception("conn refused")):
            assert monetisation_progress_pg.fetch_progress() == []

    def test_uses_dsn_env_when_no_arg(self, monkeypatch) -> None:
        captured = {}

        def fake_connect(dsn, **kw):
            captured["dsn"] = dsn
            return _make_mock_conn([])

        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/genlab")
        with patch("psycopg.connect", side_effect=fake_connect):
            monetisation_progress_pg.fetch_progress()
        assert captured["dsn"] == "postgresql://u:p@h/genlab"

    def test_dsn_arg_overrides_env(self, monkeypatch) -> None:
        captured = {}

        def fake_connect(dsn, **kw):
            captured["dsn"] = dsn
            return _make_mock_conn([])

        monkeypatch.setenv("DATABASE_URL", "env-dsn")
        with patch("psycopg.connect", side_effect=fake_connect):
            monetisation_progress_pg.fetch_progress(dsn="explicit-dsn")
        assert captured["dsn"] == "explicit-dsn"

    def test_default_fallback_when_no_env_no_arg(self, monkeypatch) -> None:
        captured = {}

        def fake_connect(dsn, **kw):
            captured["dsn"] = dsn
            return _make_mock_conn([])

        monkeypatch.delenv("DATABASE_URL", raising=False)
        with patch("psycopg.connect", side_effect=fake_connect):
            monetisation_progress_pg.fetch_progress()
        assert captured["dsn"] == "dbname=genlab"


# ---------------------------------------------------------------------------
# niche_id filtering + RLS context
# ---------------------------------------------------------------------------


class TestNicheFilter:
    def test_unfiltered_query_has_no_where_clause(self) -> None:
        conn = _make_mock_conn([])
        with patch("psycopg.connect", return_value=conn):
            monetisation_progress_pg.fetch_progress()

        # Post-Tier-4 (PR #309, 2026-06-17): the pg_connect shim
        # issues a SET app.niche_id before any caller SQL, so the
        # SELECT against monetisationprogress is no longer at index 0.
        # Find it by content instead — keeps the pin robust if the
        # shim adds future setup calls.
        cursor = conn.cursor.return_value
        select_calls = [
            c for c in cursor.execute.call_args_list if "monetisationprogress" in c[0][0]
        ]
        assert select_calls, "expected a SELECT against monetisationprogress"
        sql = select_calls[0][0][0]
        assert "WHERE" not in sql.upper()

    def test_niche_filter_adds_where_clause_and_sets_rls(self) -> None:
        conn = _make_mock_conn([])
        with patch("psycopg.connect", return_value=conn):
            monetisation_progress_pg.fetch_progress(niche_id="gaming")

        cursor = conn.cursor.return_value
        calls = cursor.execute.call_args_list
        # Two execute calls:
        #   * SET app.niche_id = 'gaming'  (from pg_connect shim)
        #   * SELECT ... WHERE niche_id = %s
        # Previously the manual ``SELECT set_config`` after pg_connect
        # was a third call; PR #309 removed it as redundant.
        set_calls = [c for c in calls if "set_config" in c[0][0]]
        select_calls = [c for c in calls if "monetisationprogress" in c[0][0]]
        assert set_calls, "expected pg_connect's SET app.niche_id"
        assert set_calls[0][0][1] == ("gaming",)
        assert select_calls, "expected the SELECT against monetisationprogress"
        assert "WHERE niche_id = %s" in select_calls[0][0][0]
        assert select_calls[0][0][1] == ("gaming",)


# ---------------------------------------------------------------------------
# _coerce_row — numeric/bool normalization
# ---------------------------------------------------------------------------


class TestCoerceRow:
    def test_rounds_floats_to_six_decimals(self) -> None:
        row = {
            "current_value": 0.0127000000000001,
            "pct_complete": 0.123456789012,
            "target_value": 10000.0,
            "delta_7d": None,
        }
        out = monetisation_progress_pg._coerce_row(row)
        assert out["current_value"] == 0.0127
        assert out["pct_complete"] == 0.123457
        assert out["target_value"] == 10000.0
        assert out["delta_7d"] is None  # None passes through

    def test_normalizes_none_threshold_met_to_false(self) -> None:
        row = {"is_threshold_met": None}
        assert monetisation_progress_pg._coerce_row(row)["is_threshold_met"] is False

    def test_preserves_true_threshold_met(self) -> None:
        row = {"is_threshold_met": True}
        assert monetisation_progress_pg._coerce_row(row)["is_threshold_met"] is True

    def test_doesnt_crash_on_non_numeric_in_numeric_column(self) -> None:
        # Defensive — a future schema change shouldn't break the dashboard.
        row = {"current_value": "not a number"}
        out = monetisation_progress_pg._coerce_row(row)
        assert out["current_value"] == "not a number"  # left in place
