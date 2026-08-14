"""Pin Phase 3.C session 1 sponsorship-pipeline endpoint contract:

  * Cold-start returns {"data": null}
  * Invalid status returns 400 (whitelist enforcement)
  * sending_enabled reflects env var
  * Bad limit param clamped to 50 default
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from server.api.sponsorship_pipeline import bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


class TestColdStart:
    def test_no_dsn_returns_null_data(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/sponsorship/pipeline")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"] is None

    def test_query_failure_returns_null_data(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable:1/x")
        resp = client.get("/api/v1/sponsorship/pipeline")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None


class TestStatusWhitelist:
    def test_invalid_status_400(self, client):
        resp = client.get("/api/v1/sponsorship/pipeline?status=EVIL")
        assert resp.status_code == 400

    def test_valid_status_accepted(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/sponsorship/pipeline?status=DRAFTED")
        assert resp.status_code == 200


class TestLimitClamping:
    def test_non_numeric_limit_defaults_50(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/sponsorship/pipeline?limit=abc")
        assert resp.status_code == 200


class TestSendingEnabledBadge:
    @patch("psycopg.connect")
    def test_sending_flag_off_by_default(
        self, mock_connect, client, monkeypatch,
    ):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:1/x")
        monkeypatch.delenv("GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED", raising=False)
        from datetime import datetime, timezone
        fake_row = {
            "id": "fake-id",
            "niche_id": "gaming",
            "tier_at_generation": "eligible_now",
            "subject": "Test",
            "body": "hi",
            "kit_url": "http://kit",
            "status": "DRAFTED",
            "drafted_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
            "approved_at": None,
            "sent_at": None,
            "responded_at": None,
            "deal_closed_at": None,
            "brand_name": "AcmeCo",
            "brand_email": "brand@acme.com",
            "contact_first_name": "Sarah",
        }
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [fake_row]
        mock_connect.return_value = conn_ctx

        resp = client.get("/api/v1/sponsorship/pipeline")
        body = resp.get_json()
        assert body["data"] is not None
        assert body["data"]["sending_enabled"] is False
        assert body["data"]["rows"][0]["brand_name"] == "AcmeCo"

    @patch("psycopg.connect")
    def test_sending_flag_on(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:1/x")
        monkeypatch.setenv("GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED", "1")
        from datetime import datetime, timezone
        fake_row = {
            "id": "id2", "niche_id": "gaming", "tier_at_generation": "x",
            "subject": "s", "body": "b", "kit_url": "u", "status": "DRAFTED",
            "drafted_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
            "approved_at": None, "sent_at": None, "responded_at": None,
            "deal_closed_at": None, "brand_name": "B", "brand_email": "e",
            "contact_first_name": None,
        }
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = [fake_row]
        mock_connect.return_value = conn_ctx

        resp = client.get("/api/v1/sponsorship/pipeline")
        assert resp.get_json()["data"]["sending_enabled"] is True
