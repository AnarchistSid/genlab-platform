"""Outlook mail sender via Microsoft Graph (Phase 3.C session 2).

Wraps ``POST /users/{upn}/sendMail`` with the existing Azure app
credentials (same tenant/client/secret used by ``BacklogClient`` for
SharePoint). Session 2 of the sponsorship-outreach pipeline —
DRAFTED → APPROVED → SENT.

## Auth

Uses client-credentials flow (app-only). The Azure app needs
``Mail.Send`` API permission granted (Graph, app-only variant) with
admin consent. Verified for the existing tenant on first live send
— fail-loud with a helpful message if the token comes back without
the scope.

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
        _credential_factory=None,  # test seam
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
        self._credential_factory = _credential_factory
        self._cred = None  # lazy — first send builds it

    def _get_credential(self):
        """Build (once) an azure.identity ClientSecretCredential
        matching the pattern in BacklogClient. Cached on the instance
        so repeated sends reuse the token cache inside azure.identity."""
        if self._cred is not None:
            return self._cred
        if self._credential_factory is not None:
            self._cred = self._credential_factory(
                self._tenant, self._client_id, self._client_secret,
            )
            return self._cred
        from azure.identity import ClientSecretCredential
        self._cred = ClientSecretCredential(
            self._tenant, self._client_id, self._client_secret,
        )
        return self._cred

    def _get_token(self) -> str:
        """Return a bearer token for the Graph API. Fail-loud on
        auth errors — a token with the wrong scope means the app
        permission wasn't granted."""
        cred = self._get_credential()
        try:
            tok = cred.get_token("https://graph.microsoft.com/.default")
        except Exception as exc:
            raise SendError(AUTH_FAILED, f"azure.identity token fetch failed: {exc}") from exc
        return tok.token

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
