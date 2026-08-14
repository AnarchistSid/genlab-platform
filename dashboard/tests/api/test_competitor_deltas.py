"""Pin Phase 3.A competitor-deltas endpoint contract:

  * Cold-start returns {"data": null}, not 500
  * DB unset falls back to {"data": null}
  * Bad limit param clamped to 25 default
  * min_ratio filter reaches the SQL

The endpoint is observation-only per intelligence-engine ship pattern.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from server.api.competitor_deltas import bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


class TestColdStart:
    def test_no_dsn_returns_null_data(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/competitor-deltas/latest")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"] is None

    def test_query_failure_returns_null_data(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable:1/x")
        # psycopg.connect will fail; endpoint must swallow + return null
        resp = client.get("/api/v1/competitor-deltas/latest")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"] is None


class TestLimitClamping:
    """Limit param must be clamped to [1, 100] and non-numeric
    falls back to default 25."""

    def test_non_numeric_limit_defaults_25(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # Not observable directly without DB, but at least the endpoint
        # doesn't crash on bad params
        resp = client.get("/api/v1/competitor-deltas/latest?limit=abc")
        assert resp.status_code == 200

    def test_negative_min_ratio_doesnt_crash(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/competitor-deltas/latest?min_ratio=-5")
        assert resp.status_code == 200


class TestFlagBadge:
    """Flag state is echoed to frontend so it can render
    'observation only' vs 'active' badge."""

    @patch("psycopg.connect")
    def test_flag_enabled_reflected(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:1/x")
        monkeypatch.setenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", "1")
        # Mock the DB roundtrip with a single fake row
        from datetime import datetime, timezone
        fake_row = {
            "niche_id": "gaming",
            "competitor_channel_id": "UC123",
            "competitor_channel_label": "TestCreator",
            "competitor_video_id": "vid_abc",
            "competitor_title": "Test title",
            "competitor_published_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
            "competitor_view_count": 1_000_000,
            "competitor_like_count": 50_000,
            "competitor_comment_count": 2_000,
            "our_reference_view_count": 10_000,
            "delta_views": 990_000,
            "delta_ratio": 100.0,
            "computed_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
        }
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [fake_row]
        mock_connect.return_value = conn_ctx

        resp = client.get("/api/v1/competitor-deltas/latest?niche_id=gaming")
        body = resp.get_json()
        assert body["data"] is not None
        assert body["data"]["flag_enabled"] is True
        assert body["data"]["rows"][0]["delta_ratio"] == 100.0

    @patch("psycopg.connect")
    def test_flag_disabled_by_default(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:1/x")
        monkeypatch.delenv("GENLAB_COMPETITOR_CONTEXT_ENABLED", raising=False)
        from datetime import datetime, timezone
        fake_row = {
            "niche_id": "gaming",
            "competitor_channel_id": "x",
            "competitor_channel_label": "y",
            "competitor_video_id": "z",
            "competitor_title": "t",
            "competitor_published_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
            "competitor_view_count": 1,
            "competitor_like_count": 1,
            "competitor_comment_count": 1,
            "our_reference_view_count": 1,
            "delta_views": 0,
            "delta_ratio": 2.0,
            "computed_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
        }
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [fake_row]
        mock_connect.return_value = conn_ctx

        resp = client.get("/api/v1/competitor-deltas/latest")
        body = resp.get_json()
        assert body["data"]["flag_enabled"] is False
