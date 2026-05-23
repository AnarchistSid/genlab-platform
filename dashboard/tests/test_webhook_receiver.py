"""Tests for Meta webhook receiver.

R-14: webhook signature verification fails CLOSED — an unverifiable event
(no app secret, no/invalid signature) is rejected, never processed. POST
tests therefore sign their payloads with a known test secret.
"""

import hashlib
import hmac
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.api.webhook_receiver as webhook_module
import server.review_server as review_server_module
from server.review_server import app

_TEST_SECRET = "test_app_secret"


def _signed(body: bytes) -> dict[str, str]:
    """Headers with a valid X-Hub-Signature-256 for `body` (signed with _TEST_SECRET)."""
    sig = "sha256=" + hmac.HMAC(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)
    # R-14: webhooks fail closed, so POST tests must sign — set a known secret.
    monkeypatch.setattr(webhook_module, "_APP_SECRET", _TEST_SECRET)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestMetaWebhook:
    def test_verify_valid_token(self, client):
        with patch.object(webhook_module, "_VERIFY_TOKEN", "test_verify_token_123"):
            resp = client.get(
                "/api/v1/webhooks/meta",
                query_string={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "test_verify_token_123",
                    "hub.challenge": "test_challenge_123",
                },
            )
        assert resp.status_code == 200
        assert resp.data.decode() == "test_challenge_123"

    def test_verify_invalid_token(self, client):
        resp = client.get(
            "/api/v1/webhooks/meta",
            query_string={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "test_challenge",
            },
        )
        assert resp.status_code == 403

    def test_receive_event(self, client):
        body = json.dumps(
            {
                "object": "instagram",
                "entry": [{"id": "123", "changes": [{"field": "mentions", "value": {}}]}],
            }
        ).encode()
        resp = client.post("/api/v1/webhooks/meta", data=body, headers=_signed(body))
        assert resp.status_code == 200
        data = resp.get_json()
        inner = data.get("data", data)
        assert inner["status"] == "received"

    def test_receive_invalid_json(self, client):
        body = b"not json{{{"
        resp = client.post("/api/v1/webhooks/meta", data=body, headers=_signed(body))
        # Past signature verification; JSON handling is graceful.
        assert resp.status_code in (200, 400)

    # ── R-14: fail-closed signature verification ──────────────────────────
    def test_unsigned_event_rejected(self, client):
        """A correctly-configured secret still rejects an UNSIGNED event."""
        body = b'{"object":"instagram","entry":[]}'
        resp = client.post("/api/v1/webhooks/meta", data=body, content_type="application/json")
        assert resp.status_code == 403

    def test_no_secret_rejected(self, client, monkeypatch):
        """Fail CLOSED: no app secret -> unverifiable -> reject (was: accept)."""
        monkeypatch.setattr(webhook_module, "_APP_SECRET", "")
        body = b'{"object":"instagram","entry":[]}'
        resp = client.post("/api/v1/webhooks/meta", data=body, content_type="application/json")
        assert resp.status_code == 403

    def test_bad_signature_rejected(self, client):
        body = b'{"object":"instagram","entry":[]}'
        resp = client.post(
            "/api/v1/webhooks/meta",
            data=body,
            headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 403
