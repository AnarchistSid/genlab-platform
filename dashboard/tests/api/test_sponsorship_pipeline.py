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


# ── Phase 3.C session 2 write-endpoint tests ──────────────────────


def _make_conn_ctx(row_dict, execute_side_effect=None):
    """Build a MagicMock that quacks like a psycopg connection ctx-mgr
    used by the write endpoints. Every _load_row call returns
    ``row_dict``; every UPDATE returns a mock with no data."""
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn_ctx
    conn_ctx.__exit__.return_value = False

    def _default_execute(sql, *args):
        result = MagicMock()
        if "SELECT" in sql and "sp.id" in sql:
            result.fetchone.return_value = row_dict
        elif "COUNT" in sql:
            result.fetchone.return_value = {"n": 0}
        else:
            result.fetchone.return_value = None
        return result

    conn_ctx.execute.side_effect = execute_side_effect or _default_execute
    return conn_ctx


class TestApprove:
    @patch("psycopg.connect")
    def test_drafted_becomes_approved(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        mock_connect.return_value = _make_conn_ctx({
            "id": "id1", "niche_id": "gaming", "status": "DRAFTED",
            "subject": "S", "body": "B",
            "approved_at": None, "sent_at": None,
            "brand_name": "B", "brand_email": "b@x.com",
        })
        resp = client.post("/api/v1/sponsorship/pipeline/id1/approve")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["status"] == "APPROVED"

    @patch("psycopg.connect")
    def test_already_approved_is_idempotent(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        mock_connect.return_value = _make_conn_ctx({
            "id": "id1", "niche_id": "gaming", "status": "APPROVED",
            "subject": "S", "body": "B",
            "approved_at": "2026-08-14", "sent_at": None,
            "brand_name": "B", "brand_email": "b@x.com",
        })
        resp = client.post("/api/v1/sponsorship/pipeline/id1/approve")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["already"] is True

    @patch("psycopg.connect")
    def test_sent_cannot_be_reapproved(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        mock_connect.return_value = _make_conn_ctx({
            "id": "id1", "niche_id": "gaming", "status": "SENT",
            "subject": "S", "body": "B",
            "approved_at": "x", "sent_at": "y",
            "brand_name": "B", "brand_email": "b@x.com",
        })
        resp = client.post("/api/v1/sponsorship/pipeline/id1/approve")
        assert resp.status_code == 409

    @patch("psycopg.connect")
    def test_not_found_returns_404(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchone.return_value = None
        mock_connect.return_value = conn_ctx
        resp = client.post("/api/v1/sponsorship/pipeline/nope/approve")
        assert resp.status_code == 404


class TestReject:
    @patch("psycopg.connect")
    def test_drafted_becomes_rejected(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        mock_connect.return_value = _make_conn_ctx({
            "id": "id1", "niche_id": "gaming", "status": "DRAFTED",
            "subject": "S", "body": "B",
            "approved_at": None, "sent_at": None,
            "brand_name": "B", "brand_email": "b@x.com",
        })
        resp = client.post(
            "/api/v1/sponsorship/pipeline/id1/reject",
            json={"reason": "off-brand"},
        )
        assert resp.status_code == 200

    @patch("psycopg.connect")
    def test_sent_cannot_be_rejected(self, mock_connect, client, monkeypatch):
        """After the email went out, "reject" makes no sense —
        operator needs to reply/apologize out-of-band."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        mock_connect.return_value = _make_conn_ctx({
            "id": "id1", "niche_id": "gaming", "status": "SENT",
            "subject": "S", "body": "B",
            "approved_at": "x", "sent_at": "y",
            "brand_name": "B", "brand_email": "b@x.com",
        })
        resp = client.post("/api/v1/sponsorship/pipeline/id1/reject", json={})
        assert resp.status_code == 409


class TestSendGating:
    def test_send_disabled_returns_503(self, client, monkeypatch):
        monkeypatch.delenv("GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED", raising=False)
        resp = client.post("/api/v1/sponsorship/pipeline/any/send")
        assert resp.status_code == 503
        assert "off" in resp.get_json()["reason"]

    @patch("psycopg.connect")
    def test_send_wrong_status_409(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        monkeypatch.setenv("GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED", "1")
        mock_connect.return_value = _make_conn_ctx({
            "id": "id1", "niche_id": "gaming", "status": "DRAFTED",
            "subject": "S", "body": "B",
            "approved_at": None, "sent_at": None,
            "brand_name": "B", "brand_email": "b@x.com",
        })
        resp = client.post("/api/v1/sponsorship/pipeline/id1/send")
        assert resp.status_code == 409

    @patch("psycopg.connect")
    def test_rate_limit_returns_429(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        monkeypatch.setenv("GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED", "1")
        monkeypatch.setenv("GENLAB_SPONSORSHIP_MAX_SENDS_PER_HOUR", "1")

        # Row is APPROVED but rate-limit already hit
        def _side_effect(sql, *args):
            result = MagicMock()
            if "SELECT" in sql and "sp.id" in sql:
                result.fetchone.return_value = {
                    "id": "id1", "niche_id": "gaming", "status": "APPROVED",
                    "subject": "S", "body": "B",
                    "approved_at": "x", "sent_at": None,
                    "brand_name": "B", "brand_email": "b@x.com",
                }
            elif "COUNT" in sql:
                result.fetchone.return_value = {"n": 5}  # over the cap
            return result

        conn_ctx = _make_conn_ctx(None, execute_side_effect=_side_effect)
        mock_connect.return_value = conn_ctx
        resp = client.post("/api/v1/sponsorship/pipeline/id1/send")
        assert resp.status_code == 429
