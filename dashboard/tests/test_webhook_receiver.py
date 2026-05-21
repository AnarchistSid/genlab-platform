"""Tests for Meta webhook receiver."""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.api.webhook_receiver as webhook_module
import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)
    # Disable HMAC signature check so POST tests don't need a real app secret
    monkeypatch.setattr(webhook_module, "_APP_SECRET", "")


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestMetaWebhook:
    def test_verify_valid_token(self, client):
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
        payload = {
            "object": "instagram",
            "entry": [{"id": "123", "changes": [{"field": "mentions", "value": {}}]}],
        }
        resp = client.post(
            "/api/v1/webhooks/meta",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Response is wrapped: {"status": "success", "data": {"status": "received"}}
        inner = data.get("data", data)
        assert inner["status"] == "received"

    def test_receive_invalid_json(self, client):
        resp = client.post(
            "/api/v1/webhooks/meta",
            data="not json{{{",
            content_type="application/json",
        )
        # Should still handle gracefully -- Flask's get_json(force=True) may parse or fail
        assert resp.status_code in (200, 400)
