"""Pin Phase 3.C session 2 OutlookMailSender:

  * Fails LOUD when GENLAB_OUTREACH_FROM_UPN is unset (never
    silently sends "from the tenant default")
  * Fails LOUD when Azure creds unset (same guard as BacklogClient)
  * 202 from Graph = SendResult(ok=True)
  * 401 → SendError(AUTH_FAILED)
  * 403 → SendError(AUTH_FAILED) with 'Mail.Send' hint
  * 429 → SendError(RATE_LIMITED)
  * 400 → SendError(INVALID_RECIPIENT)
  * 404 → SendError(INVALID_RECIPIENT) (sender UPN doesn't exist)
  * 500 → SendError(UNKNOWN_ERROR)
  * Empty subject/body → SendError(INVALID_RECIPIENT) — pre-flight
  * Malformed email → SendError(INVALID_RECIPIENT) — pre-flight
  * Body sent as Text contentType (never HTML — template safety)
  * saveToSentItems=True so operator can audit from Outlook
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.integrations.outlook_sender import (
    OutlookMailSender, SendError, SendResult,
    AUTH_FAILED, INVALID_RECIPIENT, RATE_LIMITED, UNKNOWN_ERROR,
)


class _FakeToken:
    token = "fake-bearer-token"


class _FakeCred:
    def get_token(self, scope):
        return _FakeToken()


def _make_sender(_requests, from_upn="sender@example.com"):
    return OutlookMailSender(
        from_upn=from_upn,
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        _requests_module=_requests,
        _credential_factory=lambda t, c, s: _FakeCred(),
    )


def _mock_requests(status_code, text=""):
    fake = MagicMock()
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    fake.post.return_value = resp
    return fake


class TestConstruction:
    def test_from_upn_required_missing_env(self, monkeypatch):
        monkeypatch.delenv("GENLAB_OUTREACH_FROM_UPN", raising=False)
        monkeypatch.setenv("AZURE_TENANT_ID", "t")
        monkeypatch.setenv("AZURE_CLIENT_ID", "c")
        monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")
        with pytest.raises(ValueError, match="GENLAB_OUTREACH_FROM_UPN"):
            OutlookMailSender()

    def test_azure_creds_required(self, monkeypatch):
        monkeypatch.setenv("GENLAB_OUTREACH_FROM_UPN", "s@x.com")
        monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
        monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
        monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
        with pytest.raises(ValueError, match="AZURE_"):
            OutlookMailSender()


class TestSendHappyPath:
    def test_202_returns_ok(self):
        req = _mock_requests(202)
        s = _make_sender(req)
        result = s.send("b@x.com", "Hi", "Body")
        assert result == SendResult(ok=True)

    def test_body_sent_as_text_not_html(self):
        req = _mock_requests(202)
        s = _make_sender(req)
        s.send("b@x.com", "S", "B")
        payload = req.post.call_args.kwargs["json"]
        assert payload["message"]["body"]["contentType"] == "Text"

    def test_save_to_sent_items_true(self):
        req = _mock_requests(202)
        s = _make_sender(req)
        s.send("b@x.com", "S", "B")
        payload = req.post.call_args.kwargs["json"]
        assert payload["saveToSentItems"] is True

    def test_url_uses_sender_upn(self):
        req = _mock_requests(202)
        s = _make_sender(req, from_upn="me@genlab.com")
        s.send("b@x.com", "S", "B")
        url = req.post.call_args.args[0]
        assert "users/me@genlab.com/sendMail" in url


class TestPreflightGuards:
    def test_missing_at_in_recipient_rejected(self):
        s = _make_sender(_mock_requests(202))
        with pytest.raises(SendError) as exc:
            s.send("not-an-email", "S", "B")
        assert exc.value.reason == INVALID_RECIPIENT

    def test_empty_subject_rejected(self):
        s = _make_sender(_mock_requests(202))
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "", "B")
        assert exc.value.reason == INVALID_RECIPIENT

    def test_empty_body_rejected(self):
        s = _make_sender(_mock_requests(202))
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "S", "")
        assert exc.value.reason == INVALID_RECIPIENT


class TestErrorClassification:
    """The class-of-error matters — the caller routes by reason
    (401/403 = leave for retry; 400/404 = mark REJECTED; 429 = wait)."""

    def test_401_is_auth_failed(self):
        s = _make_sender(_mock_requests(401, "Unauthorized"))
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "S", "B")
        assert exc.value.reason == AUTH_FAILED

    def test_403_is_auth_failed_with_hint(self):
        s = _make_sender(_mock_requests(403, "Forbidden"))
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "S", "B")
        assert exc.value.reason == AUTH_FAILED
        assert "Mail.Send" in exc.value.detail

    def test_429_is_rate_limited(self):
        s = _make_sender(_mock_requests(429, "Too Many Requests"))
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "S", "B")
        assert exc.value.reason == RATE_LIMITED

    def test_400_is_invalid_recipient(self):
        s = _make_sender(_mock_requests(400, "Bad Request"))
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "S", "B")
        assert exc.value.reason == INVALID_RECIPIENT

    def test_404_is_invalid_recipient(self):
        """404 typically means the sender UPN doesn't exist."""
        s = _make_sender(_mock_requests(404, "Not Found"))
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "S", "B")
        assert exc.value.reason == INVALID_RECIPIENT

    def test_500_is_unknown_error(self):
        s = _make_sender(_mock_requests(500, "Server Error"))
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "S", "B")
        assert exc.value.reason == UNKNOWN_ERROR


class TestCredentialCaching:
    def test_credential_built_once_reused_across_sends(self):
        req = _mock_requests(202)
        factory_calls = []

        def _factory(t, c, s):
            factory_calls.append(1)
            return _FakeCred()

        s = OutlookMailSender(
            from_upn="me@x.com",
            tenant_id="t", client_id="c", client_secret="s",
            _requests_module=req,
            _credential_factory=_factory,
        )
        s.send("a@x.com", "S", "B")
        s.send("b@x.com", "S", "B")
        s.send("c@x.com", "S", "B")
        # Cred built ONCE and cached — azure.identity handles token refresh
        assert len(factory_calls) == 1
