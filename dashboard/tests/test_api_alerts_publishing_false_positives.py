"""Regression tests for task #618 (2026-07-09) — two publishing alert
queries in ``dashboard/server/api/alerts.py`` fired false positives:

**stale_visual_ready** — before this fix, the query counted every
VISUAL_READY blueprint older than 7 days, including 28-of-31 that
were legitimately queued for FUTURE publish dates by the nightly
scheduler. Only 4 were truly stuck.

**partial_publish** — before this fix, the query counted every
historical partial-publish blueprint since inception. 373 rows going
back 4 months. 99% were archaeological, not actionable retries.

Both queries got tightened. These pins lock the WHERE-clause shapes
so a future refactor can't silently regress the fix.

Companion to the existing collapse pin at
``test_api_alerts_publishing_today_collapse.py`` which pins a DIFFERENT
optimization on the same endpoint.
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


def _mk_capture_conn(captured_sql: list):
    """Build a mock pg_connect → conn that captures every SQL statement
    executed and returns benign defaults. We're only pinning the SHAPE
    of the queries, not their result-processing."""
    conn = MagicMock()

    def execute(sql, params=None):
        captured_sql.append(sql)
        cursor = MagicMock()
        # publishing_analytics FILTER queries — return zeros so downstream
        # info alerts don't fire
        if "FROM PUBLISHING_ANALYTICS" in sql.upper():
            cursor.fetchone.return_value = {
                "ok": 0,
                "fail": 0,
                "yt_today": 0,
            }
            return cursor
        # blueprints queries — return empty
        if "FROM BLUEPRINTS" in sql.upper():
            if "GROUP BY" in sql.upper():
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
    conn.__exit__.return_value = False
    return conn


# ── partial_publish query pin ─────────────────────────────────────────


def test_partial_publish_query_scopes_to_last_7_days(client):
    """The pre-fix query counted 373 rows going back to 2026-03-21 (4
    months). The fix scopes to last 7 days so only actionable-recent
    partial publishes surface. If a future refactor drops the
    ``updated_at >= NOW() - 7 days`` clause, this pin fires."""
    captured = []
    conn = _mk_capture_conn(captured)

    with patch(
        "server.api.alerts.pg_connect",
        return_value=conn,
    ):
        r = client.get("/api/v1/alerts/publishing")

    assert r.status_code == 200

    # Find the SQL query that mentions platform_publish_status LIKE
    # '%FAILED%' — that's the partial_publish query.
    partial_sqls = [
        s
        for s in captured
        if "PLATFORM_PUBLISH_STATUS" in s.upper() and "LIKE '%FAILED%'" in s.upper()
    ]
    assert len(partial_sqls) == 1, (
        f"Expected exactly 1 partial-publish query, got {len(partial_sqls)}. "
        "If a future refactor duplicated or removed the query, update this pin."
    )
    partial_sql = partial_sqls[0].upper()

    # THE PIN: must scope to recent window
    assert "UPDATED_AT >= NOW() - INTERVAL '7 DAYS'" in partial_sql, (
        "partial_publish query must scope to last 7 days (task #618). "
        "Without the window, the alert counts every historical partial "
        "publish since inception — 373 rows going back to March on the "
        "day of the fix. That's archaeology, not actionable retries."
    )


# ── stale_visual_ready query pin ──────────────────────────────────────


def test_stale_visual_ready_excludes_future_scheduled(client):
    """The pre-fix query counted every VISUAL_READY blueprint older
    than 7 days, INCLUDING 28-of-31 with future scheduled_for dates
    (the nightly scheduler legitimately reaches 14 days ahead). The
    fix excludes future-scheduled — a blueprint scheduled_for tomorrow
    is not stale even if it was created 8 days ago.

    Pin the WHERE clause explicitly."""
    captured = []
    conn = _mk_capture_conn(captured)

    with patch(
        "server.api.alerts.pg_connect",
        return_value=conn,
    ):
        r = client.get("/api/v1/alerts/publishing")

    assert r.status_code == 200

    # The stale query has ``status = 'VISUAL_READY'`` AND
    # ``created_at < NOW() - INTERVAL '7 days'``.
    stale_sqls = [
        s
        for s in captured
        if "STATUS = 'VISUAL_READY'" in s.upper()
        and "CREATED_AT < NOW() - INTERVAL '7 DAYS'" in s.upper()
    ]
    assert len(stale_sqls) == 1, (
        f"Expected exactly 1 stale-VISUAL_READY query, got {len(stale_sqls)}. "
        "If a future refactor duplicated or removed the query, update this pin."
    )
    stale_sql = stale_sqls[0].upper()

    # THE PIN: must exclude future-scheduled blueprints
    assert "SCHEDULED_FOR IS NULL" in stale_sql, (
        "stale_visual_ready query must include ``scheduled_for IS NULL`` "
        "in the OR-clause (task #618). Without it, blueprints legitimately "
        "queued for future dates get flagged as stale."
    )
    assert "SCHEDULED_FOR < NOW()" in stale_sql, (
        "stale_visual_ready query must include ``scheduled_for < NOW()`` "
        "in the OR-clause (task #618). Without it, blueprints stuck in "
        "the past (publisher missed the slot) wouldn't surface."
    )
    # Both conditions must be OR-combined (either shape counts as stuck)
    assert "IS NULL OR" in stale_sql or "OR SCHEDULED_FOR" in stale_sql, (
        "stale_visual_ready must OR-combine the null and past cases — "
        "not AND. AND would require both to be true which is impossible."
    )


# ── Regression pin: prevent false positive re-introduction ────────────


def test_stale_visual_ready_does_not_alert_when_only_future_scheduled(client):
    """Functional pin: mock the DB to return 0 rows matching the
    stricter query. The endpoint should NOT include a stale_visual_ready
    warning. This is what the operator's Mission Control now shows."""
    captured = []
    conn = _mk_capture_conn(captured)

    with patch(
        "server.api.alerts.pg_connect",
        return_value=conn,
    ):
        r = client.get("/api/v1/alerts/publishing")

    assert r.status_code == 200
    data = r.get_json()["data"]
    # Since all mocked counts are 0, no stale/partial alerts fire.
    warning_types = {a["type"] for a in data.get("warning", [])}
    assert "stale_visual_ready" not in warning_types
    assert "partial_publish" not in warning_types


def test_partial_publish_does_not_alert_when_all_stale(client):
    """Same discipline as above but for partial_publish. Zero recent
    partials → no alert. Historical archaeology stays out of the
    Mission Control front page."""
    captured = []
    conn = _mk_capture_conn(captured)

    with patch(
        "server.api.alerts.pg_connect",
        return_value=conn,
    ):
        r = client.get("/api/v1/alerts/publishing")

    assert r.status_code == 200
    data = r.get_json()["data"]
    warning_types = {a["type"] for a in data.get("warning", [])}
    assert "partial_publish" not in warning_types
