"""Regression test for PR #398 — /alerts/publishing collapsed the
YouTube-quota + today-summary queries into ONE COUNT(*) FILTER query.

Pre-fix: 2 separate SELECT queries on publishing_analytics, both
filtered by the same CURRENT_DATE window:
  * yt_today: WHERE platform='youtube' AND status='SUCCESS' ...
  * today:    SELECT COUNT FILTER (status=ok), COUNT FILTER (status=fail)
              ... WHERE created_at >= CURRENT_DATE ...

Post-fix: 1 query with 3 FILTER clauses on the same outer WHERE.

Pinned invariants:
  * Both ``today_summary`` and ``youtube_quota`` alerts still surfaced
  * Algorithmic invariant: exactly 1 SELECT on publishing_analytics
    WHERE-clauses CURRENT_DATE — not 2.
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


def _mk_conn(captured_sql: list, today_metrics: dict):
    """Mock pg_connect → conn that captures SQL + returns canned rows.

    Only the today-window query against publishing_analytics matters for
    this test — other queries (failed-publish per niche, partial publish,
    stale visual ready, 24h failure rate) return empty defaults so the
    endpoint completes without errors.
    """
    conn = MagicMock()

    def execute(sql, params=None):
        captured_sql.append(sql)
        cursor = MagicMock()
        upper = sql.upper()
        # The new combined today-window query
        if (
            "FROM PUBLISHING_ANALYTICS" in upper
            and "CURRENT_DATE" in upper
            and "COUNT(*) FILTER" in upper
        ):
            cursor.fetchone.return_value = today_metrics
            return cursor
        # 24h failure-rate query (also has COUNT FILTER but uses '24 hours')
        if "FROM PUBLISHING_ANALYTICS" in upper and "24 HOURS" in upper:
            cursor.fetchone.return_value = {"ok": 0, "fail": 0}
            return cursor
        # Blueprints queries — return empty
        if "FROM BLUEPRINTS" in upper:
            if "GROUP BY" in upper:
                cursor.fetchall.return_value = []
            else:
                cursor.fetchone.return_value = {"cnt": 0}
            return cursor
        # pg_connect's SET app.niche_id
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        return cursor

    conn.execute.side_effect = execute
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = None
    return conn


class TestAlertsPublishingTodayCollapse:
    def test_one_today_window_query_emits_both_alerts(self, client, monkeypatch):
        """Both ``today_summary`` and ``youtube_quota`` alerts must still
        surface AFTER collapsing the 2 queries that powered them into 1.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        captured: list[str] = []
        today_metrics = {"ok": 4, "fail": 1, "yt_today": 2}
        with patch(
            "server.api.alerts.pg_connect",
            return_value=_mk_conn(captured, today_metrics),
        ):
            resp = client.get("/api/v1/alerts/publishing")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        # Both alerts present with the correct values
        info_types = {a["type"]: a for a in data["info"]}
        assert "youtube_quota" in info_types
        assert info_types["youtube_quota"]["uploads_today"] == 2
        assert "today_summary" in info_types
        assert info_types["today_summary"]["published"] == 4
        assert info_types["today_summary"]["failed"] == 1

    def test_only_one_query_on_today_window(self, client, monkeypatch):
        """Algorithmic invariant: exactly 1 SELECT on publishing_analytics
        for the CURRENT_DATE window — not 2.

        If a future refactor re-splits the today-summary and youtube-quota
        queries, this counter catches the regression.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        captured: list[str] = []
        today_metrics = {"ok": 0, "fail": 0, "yt_today": 0}
        with patch(
            "server.api.alerts.pg_connect",
            return_value=_mk_conn(captured, today_metrics),
        ):
            client.get("/api/v1/alerts/publishing")
        # Count SELECTs on publishing_analytics that constrain by
        # CURRENT_DATE (not the 24h-failure-rate query, which uses
        # '24 hours' interval).
        today_window_queries = [
            s
            for s in captured
            if "publishing_analytics" in s.lower() and "current_date" in s.lower()
        ]
        assert len(today_window_queries) == 1, (
            f"Expected exactly 1 today-window query on publishing_analytics "
            f"(post-PR #398), got {len(today_window_queries)} — "
            f"separate yt_today + today queries regression?"
        )
