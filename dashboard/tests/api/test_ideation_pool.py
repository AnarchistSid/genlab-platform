"""Pin Phase 4.E session 3 ideation-pool endpoint:

  * Cold-start returns {"data": null}
  * DB error returns null (fail-open)
  * Response shape matches IdeationPoolSummary
  * flag_enabled + rollout_pct reflect env
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from server.api.ideation_pool import bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


class TestColdStart:
    def test_no_dsn(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/ideation-pool/summary")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_query_error_returns_null(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable:1/x")
        resp = client.get("/api/v1/ideation-pool/summary")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None


class TestSummary:
    @patch("psycopg.connect")
    def test_flag_off_default_pct_0(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        monkeypatch.delenv("GENLAB_IDEATION_POOL_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", raising=False)
        row = {
            "niche_id": "gaming", "pending": 18, "consumed": 3,
            "expired": 0, "total": 21,
            "latest_batch_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
        }
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [row]
        mock_connect.return_value = conn_ctx

        body = client.get("/api/v1/ideation-pool/summary").get_json()
        assert body["data"] is not None
        assert body["data"]["flag_enabled"] is False
        assert body["data"]["rollout_pct"] == 0
        assert body["data"]["per_niche"][0]["pending"] == 18
        assert body["data"]["per_niche"][0]["consumed"] == 3

    @patch("psycopg.connect")
    def test_flag_on_pct_reflected(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "25")
        row = {
            "niche_id": "gaming", "pending": 1, "consumed": 0,
            "expired": 0, "total": 1, "latest_batch_at": None,
        }
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [row]
        mock_connect.return_value = conn_ctx

        body = client.get("/api/v1/ideation-pool/summary").get_json()
        assert body["data"]["flag_enabled"] is True
        assert body["data"]["rollout_pct"] == 25

    @patch("psycopg.connect")
    def test_rollout_pct_clamped(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        monkeypatch.setenv("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "500")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [
            {"niche_id": "g", "pending": 1, "consumed": 0,
             "expired": 0, "total": 1, "latest_batch_at": None},
        ]
        mock_connect.return_value = conn_ctx
        body = client.get("/api/v1/ideation-pool/summary").get_json()
        assert body["data"]["rollout_pct"] == 100

    @patch("psycopg.connect")
    def test_empty_returns_null(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = []
        mock_connect.return_value = conn_ctx
        assert client.get("/api/v1/ideation-pool/summary").get_json()["data"] is None
