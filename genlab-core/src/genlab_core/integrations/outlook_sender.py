"""Outlook mail sender via Microsoft Graph (Phase 3.C session 2).

Wraps ``POST /users/{upn}/sendMail`` with the existing Azure app
credentials (same tenant/client/secret used by ``BacklogClient`` for
SharePoint). Session 2 of the sponsorship-outreach pipeline —
DRAFTED → APPROVED → SENT.

## Auth

Uses OAuth2 client-credentials flow (app-only) via raw HTTP —
POST ``login.microsoftonline.com/{tenant}/oauth2/v2.0/token`` with
grant_type=client_credentials + scope=graph.microsoft.com/.default.
Deliberately no dependency on ``azure-identity`` — that package
isn't a declared genlab-core dep (BacklogClient lazy-imports it),
and prod runs Postgres-primary mode where SharePoint / azure-identity
never fires. Raw HTTP is 20MB less to install + one fewer indirection
+ clearer error paths.

The Azure app needs ``Mail.Send`` API permission granted (Graph,
app-only variant) with admin consent. Verified on first live send
— fail-loud with a helpful message if the token exchange fails.

## Design decisions

* **Explicit sender UPN required.** ``GENLAB_OUTREACH_FROM_UPN`` env
  var. No fallback to "any mailbox" — sending from the wrong
  identity would confuse recipients + trip DMARC.
* **Inline send only.** No background worker. The Flask ``/send``
  handler calls ``OutlookMailSender.send()`` synchronously so the
  operator sees success/failure immediately after clicking Send.
  Prevents the "background worker sent last week's stale approval"
  class of bug.
* **Fail-loud on transport errors.** ``SendError`` distinguishes
  ``AUTH_FAILED`` / ``INVALID_RECIPIENT`` / ``RATE_LIMITED`` /
  ``UNKNOWN`` so the caller can decide whether to mark the row as
  REJECTED (bad recipient) vs leave APPROVED for retry (rate limit).
* **No template rendering here.** The pipeline row's body is
  pre-rendered by the draft generator. The sender is a dumb
  transport — one string in, one API call out.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

# Constant: reason codes on SendError so callers can route by class
AUTH_FAILED: Final[str] = "AUTH_FAILED"
INVALID_RECIPIENT: Final[str] = "INVALID_RECIPIENT"
RATE_LIMITED: Final[str] = "RATE_LIMITED"
UNKNOWN_ERROR: Final[str] = "UNKNOWN"

_GRAPH_BASE: Final[str] = "https://graph.microsoft.com/v1.0"
_TOKEN_URL_TMPL: Final[str] = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


@dataclass
class SendResult:
    """Outcome of one send attempt. When ``ok`` is False, ``reason``
    is one of the module-level constants."""
    ok: bool
    reason: str | None = None
    detail: str | None = None


class SendError(Exception):
    """Raised on hard sending failures — the caller decides how to
    mark the row. Attribute ``reason`` = one of the module constants."""
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class OutlookMailSender:
    """Thin wrapper around Graph ``users/{upn}/sendMail``. Cache the
    Azure credential across sends — token refresh is handled by
    ``azure.identity`` internally."""

    def __init__(
        self,
        from_upn: str | None = None,
        *,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        _requests_module=None,  # test seam
        _credential_factory=None,  # kept for BC — no longer used
    ):
        # 3-way fallback: constructor arg → env → raise. Ensures the
        # sender is always talking to a known identity — never
        # accidentally to "the tenant's default account".
        self.from_upn = (from_upn or os.environ.get("GENLAB_OUTREACH_FROM_UPN") or "").strip()
        if not self.from_upn:
            raise ValueError(
                "GENLAB_OUTREACH_FROM_UPN must be set (mailbox to send from)"
            )
        self._tenant = (tenant_id or os.environ.get("AZURE_TENANT_ID") or "").strip()
        self._client_id = (client_id or os.environ.get("AZURE_CLIENT_ID") or "").strip()
        self._client_secret = (client_secret or os.environ.get("AZURE_CLIENT_SECRET") or "").strip()
        if not all([self._tenant, self._client_id, self._client_secret]):
            raise ValueError(
                "AZURE_TENANT_ID + AZURE_CLIENT_ID + AZURE_CLIENT_SECRET required"
            )
        self._requests = _requests_module
        # Token cache: (token_str, expires_at_unix_seconds). Refresh
        # when < 60s remaining. Simpler than azure.identity's own
        # cache — we're the only caller.
        self._cached_token: tuple[str, float] | None = None

    def _get_token(self) -> str:
        """Fetch (or reuse cached) OAuth2 bearer token via client-
        credentials flow. Fail-loud on non-2xx — a missing Mail.Send
        permission surfaces at first live send, not silently later."""
        import time

        # Reuse cached token if > 60s remaining. Tokens live ~1h;
        # cache hit rate on a real send-batch is ~100%.
        now = time.time()
        if self._cached_token is not None and self._cached_token[1] > now + 60:
            return self._cached_token[0]

        requests = self._requests
        if requests is None:
            import requests as _r
            requests = _r

        url = _TOKEN_URL_TMPL.format(tenant=self._tenant)
        try:
            resp = requests.post(
                url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
                timeout=15,
            )
        except Exception as exc:
            raise SendError(AUTH_FAILED, f"token exchange network error: {exc}") from exc
        if resp.status_code != 200:
            raise SendError(
                AUTH_FAILED,
                f"token exchange HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            body = resp.json()
        except Exception as exc:
            raise SendError(AUTH_FAILED, f"token JSON decode failed: {exc}") from exc
        token = body.get("access_token")
        expires_in = body.get("expires_in", 3600)
        if not token:
            raise SendError(AUTH_FAILED, f"no access_token in response: {body}")
        self._cached_token = (token, now + float(expires_in))
        return token

    def send(self, to_email: str, subject: str, body: str) -> SendResult:
        """Send one plain-text email. Body is treated as plain text
        (not HTML) to keep templates simple + avoid template-render
        attacks from operator-supplied content.

        Raises ``SendError`` on transport failure so the Flask
        handler can distinguish AUTH_FAILED (503) vs INVALID_RECIPIENT
        (mark REJECTED) vs RATE_LIMITED (leave APPROVED).
        """
        if not to_email or "@" not in to_email:
            raise SendError(INVALID_RECIPIENT, f"malformed email: {to_email!r}")
        if not subject or not body:
            raise SendError(INVALID_RECIPIENT, "empty subject or body")

        requests = self._requests
        if requests is None:
            import requests as _r
            requests = _r

        token = self._get_token()
        url = f"{_GRAPH_BASE}/users/{self.from_upn}/sendMail"
        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body,
                },
                "toRecipients": [
                    {"emailAddress": {"address": to_email}},
                ],
            },
            # Graph writes to Sent Items by default — keep it so
            # the operator can audit sent outreach from Outlook.
            "saveToSentItems": True,
        }
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
        except Exception as exc:
            raise SendError(UNKNOWN_ERROR, f"network error: {exc}") from exc

        # Graph returns 202 Accepted on successful queue
        if resp.status_code == 202:
            logger.info(
                "[outlook_sender] sent to=%s subject=%r",
                to_email, subject[:50],
            )
            return SendResult(ok=True)

        # Route by class so the caller can decide
        detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
        if resp.status_code == 401:
            raise SendError(AUTH_FAILED, detail)
        if resp.status_code == 403:
            raise SendError(
                AUTH_FAILED,
                f"403 — likely missing Mail.Send app permission. {detail}",
            )
        if resp.status_code == 429:
            raise SendError(RATE_LIMITED, detail)
        if resp.status_code in (400, 404):
            # 400 = bad recipient / bad payload, 404 = sender UPN not found
            raise SendError(INVALID_RECIPIENT, detail)
        raise SendError(UNKNOWN_ERROR, detail)
