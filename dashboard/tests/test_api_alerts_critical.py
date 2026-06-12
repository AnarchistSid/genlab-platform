"""Tests for the ``/api/v1/alerts/critical`` endpoint.

The endpoint reads unresolved CRITICAL rows from ``pipeline_alerts``
and exposes them so the Mission Control banner can surface them
prominently. The 2026-05-20 WARP outage motivated this surface —
the data was in the DB, the system was firing the correct
remediation in the alert message, but the dashboard wasn't
relaying any of it.

Test strategy
-------------

``conftest.py`` clears ``DATABASE_URL`` so the endpoint hits its
"no DSN configured" graceful-degradation branch. That's enough to
pin:

* HTTP 200 even without a DB connection (dev environments must not
  see 500s from this route)
* Response shape is the documented ``{unresolved_critical: [],
  count: 0}``
* Wrapped in the standard ``{success, data}`` envelope

For the DB-connected paths the tests patch ``psycopg.connect`` so
they don't need a live database. Same pattern as the other
dashboard route tests.
"""

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


def test_critical_alerts_endpoint_exists(client):
    """Route is registered on the alerts blueprint."""
    response = client.get("/api/v1/alerts/critical")
    assert response.status_code in (200, 500)  # 500 if DB error path
    assert response.is_json


def test_critical_alerts_without_database_url_degrades_gracefully(client, monkeypatch):
    """Dev environments without DATABASE_URL must not 500 — the
    banner depends on this endpoint and should silently show "no
    alerts" rather than a network error."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get("/api/v1/alerts/critical")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "success"
    assert body["data"]["count"] == 0
    assert body["data"]["unresolved_critical"] == []


def test_critical_alerts_payload_shape_with_mocked_rows(client, monkeypatch):
    """When a DB is available, the response carries normalised rows
    in the documented shape. Pin so a future refactor of the SELECT
    can't silently change the columns the frontend reads."""
    from datetime import UTC, datetime

    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    rows = [
        {
            "id": "a1",
            "niche_id": "ai_creators",
            "check_name": "warp_down",
            "message": "warp-svc not active. Run: systemctl restart warp-svc",
            "details": {"active_state": "inactive"},
            "auto_fix_applied": "not attempted (would need warp-cli reconfig)",
            "created_at": datetime(2026, 6, 12, 7, 0, 0, tzinfo=UTC),
        }
    ]

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None

    with patch("psycopg.connect", return_value=mock_conn):
        response = client.get("/api/v1/alerts/critical")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "success"
    data = body["data"]
    assert data["count"] == 1
    assert len(data["unresolved_critical"]) == 1
    row = data["unresolved_critical"][0]
    assert row["check_name"] == "warp_down"
    assert row["niche_id"] == "ai_creators"
    # Timestamp normalised to ISO string for the frontend.
    assert row["created_at"] == "2026-06-12T07:00:00+00:00"
    # The remediation text the operator needs survives unchanged.
    assert "systemctl restart warp-svc" in row["message"]
    # Auto-fix outcome preserved for the banner sub-row.
    assert "not attempted" in row["auto_fix_applied"]


def test_critical_alerts_db_error_returns_500_with_envelope(client, monkeypatch):
    """An unexpected exception during the query produces a clean
    500 (not a Flask traceback page) — the operator should see the
    rest of the dashboard even if this one endpoint hiccups."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    with patch("psycopg.connect", side_effect=RuntimeError("connection refused")):
        response = client.get("/api/v1/alerts/critical")

    assert response.status_code == 500
    body = response.get_json()
    assert body["status"] == "error"
    assert "error" in body


def test_critical_alerts_returns_empty_list_when_db_has_no_unresolved_rows(client, monkeypatch):
    """Production happy-path: WARP got fixed → ``resolved_at`` set →
    nothing returned by the query → banner hides itself."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None

    with patch("psycopg.connect", return_value=mock_conn):
        response = client.get("/api/v1/alerts/critical")

    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["count"] == 0
    assert body["data"]["unresolved_critical"] == []
