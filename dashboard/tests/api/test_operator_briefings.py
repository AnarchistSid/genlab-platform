"""Pin Phase 5.D operator-briefings endpoint:

  * Cold-start (no DSN) returns null
  * DB error returns null (fail-open)
  * No rows returns null
  * Latest row rendered with computed fields normalized
  * email_sent + email_error surfaced faithfully
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from server.api.operator_briefings import bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


class TestColdStart:
    def test_no_dsn(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/operator-briefings/latest")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_db_error(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable:1/x")
        resp = client.get("/api/v1/operator-briefings/latest")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    @patch("psycopg.connect")
    def test_empty_returns_null(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchone.return_value = None
        mock_connect.return_value = conn_ctx
        resp = client.get("/api/v1/operator-briefings/latest")
        assert resp.get_json()["data"] is None


class TestLatest:
    def _mock_row(self, mock_connect, row):
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchone.return_value = row
        mock_connect.return_value = conn_ctx

    @patch("psycopg.connect")
    def test_full_row_shape(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        row = {
            "id": "abc",
            "generated_at": datetime(2026, 8, 15, 6, 0, tzinfo=timezone.utc),
            "summary_md": "**hi**\n- one\n- two",
            "structured": {"publishes_yesterday": {"total": 5}},
            "email_sent": True,
            "email_recipient": "op@example.com",
            "email_error": None,
            "llm_cost_usd": 0.0025,
            "n_pending_flag_flips": 2,
            "n_pending_strategist_proposals": 3,
        }
        self._mock_row(mock_connect, row)
        body = client.get(
            "/api/v1/operator-briefings/latest",
        ).get_json()
        d = body["data"]
        assert d["id"] == "abc"
        assert d["summary_md"] == "**hi**\n- one\n- two"
        assert d["email_sent"] is True
        assert d["email_recipient"] == "op@example.com"
        assert d["llm_cost_usd"] == pytest.approx(0.0025)
        assert d["n_pending_flag_flips"] == 2
        assert d["n_pending_strategist_proposals"] == 3
        assert d["generated_at"].startswith("2026-08-15T06:00")

    @patch("psycopg.connect")
    def test_email_failure_surfaces(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        row = {
            "id": "abc",
            "generated_at": datetime(2026, 8, 15, tzinfo=timezone.utc),
            "summary_md": "x", "structured": {},
            "email_sent": False,
            "email_recipient": "op@example.com",
            "email_error": "AUTH_FAILED",
            "llm_cost_usd": 0.0,
            "n_pending_flag_flips": 0,
            "n_pending_strategist_proposals": 0,
        }
        self._mock_row(mock_connect, row)
        d = client.get(
            "/api/v1/operator-briefings/latest",
        ).get_json()["data"]
        assert d["email_sent"] is False
        assert d["email_error"] == "AUTH_FAILED"

    @patch("psycopg.connect")
    def test_null_counts_default_to_zero(
        self, mock_connect, client, monkeypatch,
    ):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        row = {
            "id": "abc",
            "generated_at": datetime(2026, 8, 15, tzinfo=timezone.utc),
            "summary_md": "x", "structured": {},
            "email_sent": False,
            "email_recipient": None, "email_error": None,
            "llm_cost_usd": None,
            "n_pending_flag_flips": None,
            "n_pending_strategist_proposals": None,
        }
        self._mock_row(mock_connect, row)
        d = client.get(
            "/api/v1/operator-briefings/latest",
        ).get_json()["data"]
        assert d["llm_cost_usd"] == 0.0
        assert d["n_pending_flag_flips"] == 0
        assert d["n_pending_strategist_proposals"] == 0
