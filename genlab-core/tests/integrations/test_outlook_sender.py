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


def _make_sender(_requests, from_upn="sender@example.com"):
    return OutlookMailSender(
        from_upn=from_upn,
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        _requests_module=_requests,
    )


def _mock_requests(status_code, text="", *, token_ok=True):
    """Two-call mock: first .post = token exchange (200 with
    access_token), second .post = sendMail (parameterized status).

    Set token_ok=False to simulate token-exchange failure."""
    fake = MagicMock()
    token_resp = MagicMock()
    if token_ok:
        token_resp.status_code = 200
        token_resp.json.return_value = {
            "access_token": "fake-bearer-token",
            "expires_in": 3600,
        }
    else:
        token_resp.status_code = 401
        token_resp.text = "invalid client"
        token_resp.json.return_value = {}
    send_resp = MagicMock()
    send_resp.status_code = status_code
    send_resp.text = text
    fake.post.side_effect = [token_resp, send_resp]
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
        # 2nd post is the sendMail call (1st was token exchange)
        send_call = req.post.call_args_list[1]
        payload = send_call.kwargs["json"]
        assert payload["message"]["body"]["contentType"] == "Text"

    def test_save_to_sent_items_true(self):
        req = _mock_requests(202)
        s = _make_sender(req)
        s.send("b@x.com", "S", "B")
        send_call = req.post.call_args_list[1]
        assert send_call.kwargs["json"]["saveToSentItems"] is True

    def test_url_uses_sender_upn(self):
        req = _mock_requests(202)
        s = _make_sender(req, from_upn="me@genlab.com")
        s.send("b@x.com", "S", "B")
        send_call = req.post.call_args_list[1]
        assert "users/me@genlab.com/sendMail" in send_call.args[0]


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


class TestTokenCaching:
    def test_token_exchanged_once_across_multiple_sends(self):
        """Token cache: 1 token exchange + N send POSTs for N sends,
        not 2N. Batch-send hits the cache on every send after the
        first."""
        fake = MagicMock()
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {
            "access_token": "cached-token",
            "expires_in": 3600,
        }
        send_resp = MagicMock()
        send_resp.status_code = 202
        send_resp.text = ""
        # 1 token + 3 sends = 4 total POSTs
        fake.post.side_effect = [token_resp, send_resp, send_resp, send_resp]

        s = OutlookMailSender(
            from_upn="me@x.com",
            tenant_id="t", client_id="c", client_secret="s",
            _requests_module=fake,
        )
        s.send("a@x.com", "S", "B")
        s.send("b@x.com", "S", "B")
        s.send("c@x.com", "S", "B")
        assert fake.post.call_count == 4  # 1 token + 3 sends

    def test_token_exchange_401_is_auth_failed(self):
        req = _mock_requests(202, token_ok=False)
        s = _make_sender(req)
        with pytest.raises(SendError) as exc:
            s.send("b@x.com", "S", "B")
        assert exc.value.reason == AUTH_FAILED
