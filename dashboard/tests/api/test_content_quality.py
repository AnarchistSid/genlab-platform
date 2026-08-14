"""Pin Phase 4.A session 4 content-quality endpoint:

  * Cold-start returns {"data": null}
  * DB error returns {"data": null} (fail-open, never 500)
  * flag_enabled reflects env var
  * Response shape matches ContentQualitySummary type
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from server.api.content_quality import bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


class TestColdStart:
    def test_no_dsn_returns_null(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/content-quality/summary")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_query_error_returns_null(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable:1/x")
        resp = client.get("/api/v1/content-quality/summary")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None


class TestSummary:
    @patch("psycopg.connect")
    def test_returns_per_niche_rows(self, mock_connect, client, monkeypatch):
        from datetime import datetime, timezone
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:1/x")
        monkeypatch.delenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", raising=False,
        )
        row = {
            "niche_id": "gaming",
            "n_scored": 5,
            "avg_joint": 0.42,
            "avg_visual": 0.38,
            "avg_audio": 0.51,
            "min_joint": 0.12,
            "max_joint": 0.78,
            "last_scored_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
        }
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [row]
        mock_connect.return_value = conn_ctx

        resp = client.get("/api/v1/content-quality/summary")
        body = resp.get_json()
        assert body["data"] is not None
        assert body["data"]["flag_enabled"] is False
        assert body["data"]["per_niche"][0]["niche_id"] == "gaming"
        assert body["data"]["per_niche"][0]["n_scored"] == 5
        assert body["data"]["per_niche"][0]["avg_joint"] == 0.42

    @patch("psycopg.connect")
    def test_flag_enabled_reflected(
        self, mock_connect, client, monkeypatch,
    ):
        from datetime import datetime, timezone
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:1/x")
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        row = {
            "niche_id": "gaming", "n_scored": 1,
            "avg_joint": 0.5, "avg_visual": 0.5, "avg_audio": 0.5,
            "min_joint": 0.5, "max_joint": 0.5,
            "last_scored_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
        }
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [row]
        mock_connect.return_value = conn_ctx

        resp = client.get("/api/v1/content-quality/summary")
        assert resp.get_json()["data"]["flag_enabled"] is True

    @patch("psycopg.connect")
    def test_empty_result_returns_null(
        self, mock_connect, client, monkeypatch,
    ):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:1/x")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = []
        mock_connect.return_value = conn_ctx

        resp = client.get("/api/v1/content-quality/summary")
        assert resp.get_json()["data"] is None
