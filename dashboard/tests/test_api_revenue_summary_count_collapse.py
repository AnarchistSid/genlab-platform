"""Regression test for PR #395 — /revenue/summary collapsed 3 COUNT
queries into 1 COUNT(*) FILTER query.

Pre-fix: 3 separate `SELECT COUNT(*) FROM affiliate_clicks WHERE
created_at >= NOW() - INTERVAL 'X days'` queries for today/7d/30d.

Post-fix: 1 query with `COUNT(*) FILTER (WHERE ...)` clauses bucket
matching rows into the 3 windows in a single index scan.

The contract this test pins:
  * /revenue/summary still returns clicks_today/7d/30d in the response
    shape clients depend on
  * SQL pattern check: the new COUNT(*) FILTER text appears AND the
    old 3-separate-COUNT-on-affiliate_clicks pattern does NOT (a
    regression that drops the optimization would either re-introduce
    3 separate queries or change the FILTER syntax — both caught by
    pattern check on the captured SQL).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _patch_pg(captured_sql: list, count_row: dict, group_rows: dict):
    """Mock pg_connect so the test can capture executed SQL + canned-return rows.

    ``captured_sql`` accumulates every SQL string passed to execute().
    ``count_row`` is the dict returned by the SINGLE COUNT-FILTER query.
    ``group_rows`` maps row-pattern key → fetchall return list.
    """
    conn = MagicMock()

    def execute(sql, params=None):
        captured_sql.append(sql)
        cursor = MagicMock()
        upper = sql.upper()
        # The 1 COUNT(*) FILTER query lands on fetchone()
        if "COUNT(*) FILTER" in sql:
            cursor.fetchone.return_value = count_row
            return cursor
        # GROUP BY queries land on fetchall()
        if "GROUP BY PRODUCT_ID" in upper:
            cursor.fetchall.return_value = group_rows.get("product", [])
        elif "GROUP BY NICHE_ID, NETWORK" in upper:
            cursor.fetchall.return_value = group_rows.get("niche_network", [])
        elif "GROUP BY NICHE_ID" in upper:
            cursor.fetchall.return_value = group_rows.get("niche", [])
        elif "GROUP BY NETWORK" in upper:
            cursor.fetchall.return_value = group_rows.get("network", [])
        elif "FROM AFFILIATE_REVENUE" in upper:
            cursor.fetchall.return_value = group_rows.get("affiliate_revenue", [])
        else:
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = None
        return cursor

    conn.execute.side_effect = execute
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = None
    return patch("server.api.revenue.pg_connect", return_value=conn)


class TestRevenueSummaryCountCollapse:
    def test_count_collapse_uses_single_filter_query(self, client, monkeypatch):
        """The 3 click-window counts must come from a SINGLE COUNT(*) FILTER
        query, not 3 separate COUNT queries.

        Pin guards against a future refactor that re-introduces the
        per-window-query anti-pattern.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        captured: list[str] = []
        count_row = {"clicks_today": 5, "clicks_7d": 23, "clicks_30d": 117}
        with _patch_pg(captured, count_row, group_rows={}):
            resp = client.get("/api/v1/revenue/summary")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        # Shape preserved — clicks nested under "clicks" envelope per the
        # endpoint's response format (today / last_7d / last_30d keys).
        assert data["clicks"]["today"] == 5
        assert data["clicks"]["last_7d"] == 23
        assert data["clicks"]["last_30d"] == 117
        # Algorithmic pin: exactly ONE query containing COUNT(*) FILTER
        filter_queries = [s for s in captured if "COUNT(*) FILTER" in s]
        assert len(filter_queries) == 1, (
            f"Expected exactly 1 COUNT(*) FILTER query, got {len(filter_queries)} — "
            f"O(3) regression?"
        )
        # And NOT 3 separate per-window COUNT queries on affiliate_clicks
        per_window_count_queries = [
            s
            for s in captured
            if "SELECT COUNT(*)" in s.upper()
            and "FROM AFFILIATE_CLICKS" in s.upper()
            and "FILTER" not in s
            # Allow group_by queries that select COUNT but those have GROUP BY
            and "GROUP BY" not in s.upper()
        ]
        assert len(per_window_count_queries) == 0, (
            f"Found {len(per_window_count_queries)} per-window COUNT queries that "
            f"should have been collapsed into the FILTER query: {per_window_count_queries}"
        )
