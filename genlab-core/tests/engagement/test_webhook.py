"""Tests for the Meta webhook receiver (engagement/webhook.py)."""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from genlab_core.engagement.webhook import app


@pytest.fixture()
def client():
    return TestClient(app)


# -- GET verification tests --------------------------------------------------


def test_verify_webhook_correct_token(client, monkeypatch):
    """Correct verify token returns hub.challenge as integer."""
    monkeypatch.setattr("genlab_core.engagement.webhook._VERIFY_TOKEN", "test_secret")
    resp = client.get(
        "/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "12345",
            "hub.verify_token": "test_secret",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == 12345


def test_verify_webhook_wrong_token_returns_403(client, monkeypatch):
    """Wrong verify token returns 403."""
    monkeypatch.setattr("genlab_core.engagement.webhook._VERIFY_TOKEN", "correct")
    resp = client.get(
        "/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "999",
            "hub.verify_token": "wrong",
        },
    )
    assert resp.status_code == 403


def test_verify_webhook_missing_mode_returns_403(client, monkeypatch):
    """Missing hub.mode returns 403."""
    monkeypatch.setattr("genlab_core.engagement.webhook._VERIFY_TOKEN", "secret")
    resp = client.get(
        "/webhooks/meta",
        params={"hub.verify_token": "secret", "hub.challenge": "1"},
    )
    assert resp.status_code == 403


# -- POST event tests --------------------------------------------------------

def _make_comment_payload(comment_id: str, text: str, media_id: str = "media_123") -> dict:
    """Build a Meta webhook payload with one comment change."""
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "17841448019867838",
                "time": 1700000000,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": comment_id,
                            "text": text,
                            "media": {"id": media_id},
                        },
                    }
                ],
            }
        ],
    }


def _sign(body: bytes, secret: str) -> str:
    """Compute X-Hub-Signature-256 header value."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_post_comment_dispatches_normal(client, monkeypatch):
    """A regular comment dispatches to reply_to_comment_normal."""
    monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "")
    payload = _make_comment_payload("c_001", "Great clip!")
    body = json.dumps(payload).encode()

    with patch("genlab_core.engagement.tasks.reply_to_comment_normal") as mock_normal:
        resp = client.post("/webhooks/meta", content=body)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    mock_normal.send.assert_called_once()
    event = mock_normal.send.call_args[0][0]
    assert event["comment_id"] == "c_001"
    assert event["platform"] == "instagram"


def test_post_question_dispatches_high(client, monkeypatch):
    """A comment with '?' dispatches to reply_to_comment_high."""
    monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "")
    payload = _make_comment_payload("c_002", "What settings do you use?")
    body = json.dumps(payload).encode()

    with patch("genlab_core.engagement.tasks.reply_to_comment_high") as mock_high:
        resp = client.post("/webhooks/meta", content=body)

    assert resp.status_code == 200
    mock_high.send.assert_called_once()
    event = mock_high.send.call_args[0][0]
    assert event["comment_id"] == "c_002"


def test_post_non_json_body_returns_ok(client, monkeypatch):
    """Non-JSON body returns 200 (fail-open to prevent Meta retry floods)."""
    monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "")
    resp = client.post("/webhooks/meta", content=b"not-json-at-all")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_post_invalid_signature_returns_403(client, monkeypatch):
    """Invalid HMAC signature returns 403."""
    monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "real_secret")
    payload = _make_comment_payload("c_003", "Hello")
    body = json.dumps(payload).encode()

    resp = client.post(
        "/webhooks/meta",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=badhash"},
    )
    assert resp.status_code == 403


def test_post_valid_signature_accepted(client, monkeypatch):
    """Valid HMAC signature is accepted."""
    secret = "my_app_secret"
    monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", secret)
    payload = _make_comment_payload("c_004", "Nice!")
    body = json.dumps(payload).encode()
    sig = _sign(body, secret)

    with patch("genlab_core.engagement.tasks.reply_to_comment_normal"):
        resp = client.post(
            "/webhooks/meta",
            content=body,
            headers={"X-Hub-Signature-256": sig},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_post_no_comment_field_ignored(client, monkeypatch):
    """A change with field != 'comments' is silently ignored."""
    monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "")
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": "page_id",
                "changes": [{"field": "live_videos", "value": {}}],
            }
        ],
    }
    body = json.dumps(payload).encode()

    with patch("genlab_core.engagement.tasks.reply_to_comment_normal") as mock_normal, \
         patch("genlab_core.engagement.tasks.reply_to_comment_high") as mock_high:
        resp = client.post("/webhooks/meta", content=body)

    assert resp.status_code == 200
    mock_normal.send.assert_not_called()
    mock_high.send.assert_not_called()


# -- Health endpoint ----------------------------------------------------------

def test_health_endpoint(client):
    """GET /health returns 200 with status ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
