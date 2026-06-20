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
                "id": "17841400000000001",
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
    # 2026-06-20 hardening: existing tests assume signature-skip is OK.
    # The new default refuses unsigned events — explicitly opt into dev
    # mode for these tests so the existing behavior is preserved.
    monkeypatch.setattr("genlab_core.engagement.webhook._REQUIRE_SIGNATURE", False)
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
    # 2026-06-20 hardening: existing tests assume signature-skip is OK.
    # The new default refuses unsigned events — explicitly opt into dev
    # mode for these tests so the existing behavior is preserved.
    monkeypatch.setattr("genlab_core.engagement.webhook._REQUIRE_SIGNATURE", False)
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
    # 2026-06-20 hardening: existing tests assume signature-skip is OK.
    # The new default refuses unsigned events — explicitly opt into dev
    # mode for these tests so the existing behavior is preserved.
    monkeypatch.setattr("genlab_core.engagement.webhook._REQUIRE_SIGNATURE", False)
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
    # 2026-06-20 hardening: existing tests assume signature-skip is OK.
    # The new default refuses unsigned events — explicitly opt into dev
    # mode for these tests so the existing behavior is preserved.
    monkeypatch.setattr("genlab_core.engagement.webhook._REQUIRE_SIGNATURE", False)
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

    with (
        patch("genlab_core.engagement.tasks.reply_to_comment_normal") as mock_normal,
        patch("genlab_core.engagement.tasks.reply_to_comment_high") as mock_high,
    ):
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


# -- R-60: media_id is validated before it can reach a query formula ----------


class TestResolveNicheInjectionGuard:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        from genlab_core.engagement import webhook

        webhook._niche_cache.clear()
        monkeypatch.delenv("NICHE_ID", raising=False)
        yield
        webhook._niche_cache.clear()

    def test_malicious_media_id_never_builds_a_query(self):
        """A non-numeric media_id must short-circuit to the default niche and
        NEVER construct a BacklogClient / query formula (the injection vector)."""
        from genlab_core.engagement.webhook import _resolve_niche

        with patch("genlab_core.http.backlog_client.BacklogClient") as mock_bc:
            niche = _resolve_niche("x') OR fields/niche_id eq 'gaming")
        assert niche == "ai_creators"
        mock_bc.assert_not_called()

    def test_empty_media_id_defaults_without_query(self):
        from genlab_core.engagement.webhook import _resolve_niche

        with patch("genlab_core.http.backlog_client.BacklogClient") as mock_bc:
            assert _resolve_niche("") == "ai_creators"
        mock_bc.assert_not_called()

    def test_valid_numeric_media_id_proceeds_to_lookup(self):
        """A well-formed numeric Meta id is allowed through to the backlog
        lookup, and the formula carries exactly that id."""
        from genlab_core.engagement.webhook import _resolve_niche

        with patch("genlab_core.http.backlog_client.BacklogClient") as mock_bc:
            instance = mock_bc.return_value
            instance.publishing_analytics.all.return_value = [{"fields": {"niche_id": "gaming"}}]
            niche = _resolve_niche("17895695668004550")
        assert niche == "gaming"
        instance.publishing_analytics.all.assert_called_once()
        formula = instance.publishing_analytics.all.call_args.kwargs["formula"]
        assert "17895695668004550" in formula

    def test_underscore_composite_id_is_allowed(self):
        """Meta sometimes uses <a>_<b> composite ids — still numeric-safe."""
        from genlab_core.engagement.webhook import _resolve_niche

        with patch("genlab_core.http.backlog_client.BacklogClient") as mock_bc:
            mock_bc.return_value.publishing_analytics.all.return_value = []
            assert _resolve_niche("123_456") == "ai_creators"
        mock_bc.return_value.publishing_analytics.all.assert_called_once()


class TestMandatorySignatureVerification:
    """Pin the 2026-06-20 hardening: when ``META_APP_SECRET`` is missing
    AND the default require-signature is in effect, the webhook MUST
    refuse the request rather than silently accept an unsigned event.

    Pre-fix the check was ``if _APP_SECRET: <verify>`` — an unset
    secret resulted in every POST being accepted without verification.
    An attacker who learned the public URL could inject fake comment
    events: spam the engagement engine, exhaust dramatiq workers,
    force reply-LLM API cost, or trigger replies to non-existent
    comments on operator-owned pages.
    """

    def test_no_secret_default_returns_503(self, client, monkeypatch):
        """``META_APP_SECRET`` missing + ``GENLAB_REQUIRE_WEBHOOK_SIGNATURE``
        default-on → POST is refused with 503."""
        monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "")
        monkeypatch.setattr("genlab_core.engagement.webhook._REQUIRE_SIGNATURE", True)
        resp = client.post("/webhooks/meta", content=b'{"any": "body"}')
        assert resp.status_code == 503, (
            "Pre-fix this would have been 200 (silent accept). The defense is "
            "503 — operator must explicitly configure META_APP_SECRET or "
            "explicitly opt OUT via GENLAB_REQUIRE_WEBHOOK_SIGNATURE=0."
        )
        # Error message must name the env var so operators can fix it
        assert "META_APP_SECRET" in resp.json()["detail"]

    def test_no_secret_with_dev_escape_hatch_returns_200(self, client, monkeypatch):
        """The dev/test escape hatch: ``META_APP_SECRET`` missing AND
        ``GENLAB_REQUIRE_WEBHOOK_SIGNATURE=0`` (parsed as False) →
        POST is accepted with a WARNING log. Pin this so operators
        running localhost dev servers aren't blocked."""
        monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "")
        monkeypatch.setattr("genlab_core.engagement.webhook._REQUIRE_SIGNATURE", False)
        resp = client.post("/webhooks/meta", content=b'{"any": "body"}')
        # 200 because the webhook also fail-opens on non-JSON / unknown
        # event shapes (prevents Meta retry floods). The point is the
        # request is ACCEPTED, not refused with 503.
        assert resp.status_code == 200

    def test_secret_set_still_validates_signature(self, client, monkeypatch):
        """When ``META_APP_SECRET`` IS set, signature validation runs
        regardless of the require-signature env. Bad signature → 403.
        Pin against any regression that accidentally short-circuits
        the verify when the env is provided."""
        monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "real_secret")
        monkeypatch.setattr("genlab_core.engagement.webhook._REQUIRE_SIGNATURE", True)
        resp = client.post(
            "/webhooks/meta",
            content=b'{"any": "body"}',
            headers={"X-Hub-Signature-256": "sha256=wrong"},
        )
        assert resp.status_code == 403

    def test_secret_set_with_dev_flag_still_validates(self, client, monkeypatch):
        """When ``META_APP_SECRET`` IS set, signature validation runs
        even if ``GENLAB_REQUIRE_WEBHOOK_SIGNATURE=0``. The dev flag
        only governs the missing-secret case, not the verification
        itself. Belt + suspenders pin."""
        monkeypatch.setattr("genlab_core.engagement.webhook._APP_SECRET", "real_secret")
        monkeypatch.setattr("genlab_core.engagement.webhook._REQUIRE_SIGNATURE", False)
        resp = client.post(
            "/webhooks/meta",
            content=b'{"any": "body"}',
            headers={"X-Hub-Signature-256": "sha256=wrong"},
        )
        assert resp.status_code == 403, (
            "GENLAB_REQUIRE_WEBHOOK_SIGNATURE=0 must NOT disable signature "
            "VERIFICATION when META_APP_SECRET is set — it only governs "
            "the missing-secret case."
        )

    def test_require_signature_parses_truthy_strings(self, monkeypatch):
        """The env-var parsing accepts the documented falsy values
        (``"0"``, ``"false"``, ``"False"``, ``"FALSE"``, ``"no"``) as
        opt-OUT. Everything else (including unset → default) treats
        as opt-IN to mandatory signature. Pin the parsing so future
        edits don't accidentally widen the opt-out (e.g. adding
        ``"off"`` without thinking through the security impact).
        """
        import importlib

        # Truthy: env unset, "1", "true", and even garbage like "x"
        for v in ["1", "true", "yes", "anything"]:
            monkeypatch.setenv("GENLAB_REQUIRE_WEBHOOK_SIGNATURE", v)
            from genlab_core.engagement import webhook

            importlib.reload(webhook)
            assert webhook._REQUIRE_SIGNATURE is True, (
                f"env={v!r} must be parsed as require-signature=True "
                f"(only the documented falsy values may opt out)"
            )

        # Falsy: documented opt-out values
        for v in ["0", "false", "False", "FALSE", "no"]:
            monkeypatch.setenv("GENLAB_REQUIRE_WEBHOOK_SIGNATURE", v)
            from genlab_core.engagement import webhook

            importlib.reload(webhook)
            assert webhook._REQUIRE_SIGNATURE is False, (
                f"env={v!r} must be parsed as require-signature=False"
            )
