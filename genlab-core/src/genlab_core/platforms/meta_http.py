"""Shared HTTP session for Meta Graph API (FB / IG / Threads).

Two purposes (both from the 2026-07-22 Meta anti-fingerprint audit):

**B — User-Agent header.** Bare `requests.get()` sends no UA header;
Meta's WAF fingerprints anonymous requests as bot automation before
even looking at the payload. This module's `get_shared_session()`
returns a Session that always sets a stable identified UA:

    GenLab-Publisher/1.0 (+https://github.com/AnarchistSid/genlab-platform)

Operator can override via `GENLAB_META_USER_AGENT` env if Meta ever
signals that a different UA works better.

**A — X-App-Usage telemetry capture.** Every Meta response includes
`X-App-Usage` and `X-Business-Use-Case-Usage` headers with real-time
percentage of our rate-limit windows consumed. Currently discarded.
The response hook here captures them onto the response object as
`response._genlab_meta_usage`; upstream publisher code can log to
compliance_events for the enforcement-observability signal that turns
Meta rate limiting from unpredictable → visible.

## Usage

    from genlab_core.platforms.meta_http import get_shared_session, extract_usage

    _SESSION = get_shared_session()
    resp = _SESSION.get(url, timeout=30)
    if usage := extract_usage(resp):
        # log to compliance_events, emit metric, etc.
        ...

## Design notes

- Session is process-shared (one Session per Python process) — reuses
  TCP connections, which additionally reduces the "isolated bot
  request" fingerprint. Sessions are thread-safe for `.get/post/put`
  when using default connection pool sizing.
- Response hook is fail-open: any exception in hook processing MUST
  NOT break the real response. Hook returns the response unchanged.
- No retry / circuit breaker here — that's caller's responsibility
  (the existing `@resilient` decorator wraps most Meta calls).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "GenLab-Publisher/1.0 (+https://github.com/AnarchistSid/genlab-platform)"
)


def _get_user_agent() -> str:
    """Read UA at session-creation time, not import time.
    Lets operator swap UA via env without a process restart on the
    next lazy-init."""
    return os.environ.get("GENLAB_META_USER_AGENT") or _DEFAULT_UA


def _capture_usage_headers(response: requests.Response, *args: Any, **kwargs: Any) -> requests.Response:
    """Response hook — extract Meta rate-limit telemetry onto the
    response for upstream extraction. Fail-OPEN: hook exceptions
    never propagate (real response must be preserved intact).
    """
    try:
        app_usage = response.headers.get("X-App-Usage")
        buc_usage = response.headers.get("X-Business-Use-Case-Usage")
        ad_usage = response.headers.get("X-Ad-Account-Usage")  # occasionally present
        if app_usage or buc_usage or ad_usage:
            # Attach as non-standard attribute for upstream extraction.
            # Using _-prefix keeps it out of `dict(response)` if any
            # caller ever iterates response attributes.
            response._genlab_meta_usage = {  # type: ignore[attr-defined]
                "x_app_usage": app_usage,
                "x_business_use_case_usage": buc_usage,
                "x_ad_account_usage": ad_usage,
            }
    except Exception as exc:  # noqa: BLE001 — hook must fail-open
        logger.debug("[meta_http] usage-header capture failed: %s", exc)
    return response


def get_shared_session() -> requests.Session:
    """Return a Session pre-configured with:
      * User-Agent header (breaks anonymous-script fingerprint)
      * Response hook to capture X-App-Usage headers

    Returns a NEW session per call — callers should stash their own
    at module level. Not a singleton to avoid the "session outlives
    process lifetime" corner case in tests. Real production callers
    should keep ONE session for the process lifetime for connection
    reuse benefit.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": _get_user_agent()})
    session.hooks["response"] = [_capture_usage_headers]
    return session


def extract_usage(response: requests.Response) -> dict[str, str | None] | None:
    """Read the usage-header dict attached by the response hook.

    Returns None if no usage headers were present (which is normal for
    non-Meta responses or Meta responses that don't return usage
    headers on that endpoint — some read-only endpoints skip them).
    """
    return getattr(response, "_genlab_meta_usage", None)
