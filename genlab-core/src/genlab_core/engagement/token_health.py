"""Token-expiry detector + visibility helper for engagement pollers.

Closes the visibility half of fix #3 of the autonomy roadmap: when a
platform token expires, the poller has been silently logging WARN and
moving on, so the engagement queue dries up without any operator-facing
signal. The 22-day silence in pending_engagement (2026-05-21 → 2026-06-12)
traced back to Threads tokens expiring on 2026-05-26 with nothing in
``pipeline_alerts`` to surface it.

After this module: each token-expiry detection writes a CRITICAL row to
``pipeline_alerts`` with the literal env var name the operator needs to
refresh. ``CriticalAlertsBanner`` on Mission Control (PR #174) surfaces
it immediately. The poller continues to log WARN so the journalctl
audit trail remains intact.

The auto-resolve sweep (``health_monitor.resolve_stale_alerts``) clears
the alert as soon as polls succeed again — no manual close-out needed
once the operator refreshes the token.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Final

logger = logging.getLogger(__name__)


# Maps each (platform, env var token name pattern) so the alert message
# carries the literal env var the operator needs to update. Kept as
# free-form strings — niche prefixes (CRITICALRUSH_, CLUTCHWIRE_, etc.)
# are resolved at message-render time via niche_credentials' prefix map.
_TOKEN_ENV_HINT: Final[dict[str, str]] = {
    "threads": "{NICHE_PREFIX}_THREADS_ACCESS_TOKEN",
    "instagram": "{NICHE_PREFIX}_META_ACCESS_TOKEN (Instagram side)",
    "facebook": "{NICHE_PREFIX}_FB_PAGE_ACCESS_TOKEN",
    "x_twitter": "{NICHE_PREFIX}_X_ACCESS_TOKEN + _X_ACCESS_SECRET",
    "twitter": "{NICHE_PREFIX}_X_ACCESS_TOKEN + _X_ACCESS_SECRET",
    "youtube": "{NICHE_PREFIX}_YOUTUBE_API_KEY",
}


def _niche_prefix(niche_id: str) -> str:
    """Map niche_id to its env-var prefix.

    Mirrors the static map in niche_credentials so this module doesn't
    import that file at module load (lazy-loaded once at first use).
    Falls back to the upper-cased niche_id which is the right shape for
    any new niches that haven't been added to the credential resolver
    yet.
    """
    mapping = {
        "ai_creators": "BLACKBOXBRIEF",
        "ai_tech": "BLACKBOXBRIEF",  # canonical alias
        "gaming": "CRITICALRUSH",
        "sports": "CLUTCHWIRE",
        "movies": "SPLICEREEL",
        "anime": "FRAMEDRIFT",
    }
    return mapping.get(niche_id, niche_id.upper())


def is_oauth_expiry(error_body: str) -> bool:
    """Return True when the response body matches Meta's OAuthException
    "session has expired" shape.

    Meta's `code=190` covers expired token, invalid token, revoked token,
    and password change — all of which require an operator-initiated
    refresh, so we treat them identically.

    Accepts the raw text body (the poller logs typically have it on hand
    via ``e.response.text`` or the message string).
    """
    if not error_body:
        return False
    try:
        body = (
            json.loads(error_body)
            if error_body.lstrip().startswith("{")
            else {"error": {"message": error_body}}
        )
    except (ValueError, TypeError):
        return False
    err = body.get("error", {}) if isinstance(body, dict) else {}
    if not isinstance(err, dict):
        return False
    if err.get("code") == 190 or err.get("type") == "OAuthException":
        return True
    # Fallback: string match for callers that handed us a plain message.
    msg = (err.get("message") or "").lower()
    return "session has expired" in msg or "access token" in msg and "expired" in msg


def emit_token_expiry_alert(
    platform: str,
    niche_id: str,
    error_body: str = "",
) -> bool:
    """Write a CRITICAL `token_expired` alert to ``pipeline_alerts``.

    Returns True when a row was written; False when no DSN, when an
    unresolved alert already exists for this (check_name, niche_id), or
    when the DB write raised. Never raises — alert failure must not
    break poll loops.

    The message embeds the literal env var name the operator needs to
    refresh, e.g. ``CRITICALRUSH_THREADS_ACCESS_TOKEN``. Mission Control
    surfaces unresolved CRITICAL alerts on the home page (PR #174).

    Deduplication: the SELECT-against-unresolved-rows check below is the
    only throttle. An earlier version of this function used a
    process-level in-memory throttle on top of the DB check, which made
    behaviour order-dependent across pytest modules (state from one
    test's poller call would suppress emission in a token_health test
    later in the run). The DB-side check is functionally equivalent at
    the granularity that matters (one row per unresolved alert) without
    the cross-test coupling.
    """
    prefix = _niche_prefix(niche_id)
    env_hint = _TOKEN_ENV_HINT.get(platform, "{NICHE_PREFIX}_<PLATFORM>_TOKEN")
    env_hint = env_hint.replace("{NICHE_PREFIX}", prefix)

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.warning(
            "[token_health] No DATABASE_URL — alert for %s/%s not persisted",
            platform,
            niche_id,
        )
        return False

    message = (
        f"{platform.title()} access token expired for {niche_id} — "
        f"engagement polling and replies will fail until refreshed. "
        f"Update {env_hint} in /opt/genlab/.env and "
        f"`systemctl restart genlab-engagement-poller`."
    )
    details = {
        "platform": platform,
        "niche_id": niche_id,
        "env_var_hint": env_hint,
        "error_body": (error_body or "")[:400],
        "remediation": [
            f"Refresh {env_hint} via the platform's developer console",
            "Update /opt/genlab/.env on prod",
            "systemctl restart genlab-engagement-poller",
        ],
    }

    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # Deduplicate: don't pile up the same unresolved alert
                # if the poller fires before the 1h throttle window.
                cur.execute(
                    "SELECT id FROM pipeline_alerts "
                    "WHERE check_name = %s AND niche_id IS NOT DISTINCT FROM %s "
                    "AND resolved_at IS NULL",
                    ("token_expired", niche_id),
                )
                if cur.fetchone():
                    return False

                cur.execute(
                    "INSERT INTO pipeline_alerts "
                    "(niche_id, check_name, severity, message, details) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (niche_id, "token_expired", "critical", message, json.dumps(details)),
                )
                conn.commit()
        logger.error(
            "[token_health] CRITICAL ALERT emitted: %s token expired for %s",
            platform,
            niche_id,
        )
        return True
    except Exception as exc:
        logger.warning("[token_health] Failed to write alert: %s", exc)
        return False


def reset_throttle() -> None:
    """Deprecated no-op kept for backward compatibility.

    The in-memory throttle was removed because it created cross-test
    state coupling (state from one pytest module's poller calls would
    suppress emission in token_health tests run later in the same
    session). Deduplication is now handled by the SELECT-against-
    unresolved-rows check in :func:`emit_token_expiry_alert`. Existing
    callers and tests that called this function continue to work; new
    callers should not depend on it.
    """
    return


# ────────────────────────────────────────────────────────────────────────────
# Threads Long-Lived Token (LLUT) auto-refresh — 2026-06-14
# ────────────────────────────────────────────────────────────────────────────
#
# Threads tokens are 60-day LLUTs that can be refreshed BEFORE expiry via
# GET https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token=...
# The 2026-06-14 engagement-loop audit found all 5 niche tokens expired
# around 2026-05-21 (i.e., the previous refresh cycle never happened),
# producing a 24-day silent outage with 720 warning logs/day and zero
# replies posted.
#
# This module's refresh function is called by
# ``scripts/refresh_threads_tokens.py`` on a daily systemd timer. The
# script handles per-niche dispatch, persistence to the JSON cache file,
# and operator alerting on failure.

_THREADS_REFRESH_URL = "https://graph.threads.net/refresh_access_token"


class ThreadsRefreshResult:
    """Outcome of a single refresh attempt.

    Holds the new token + expiry, or an error description. The token
    value lives ONLY on this in-memory object — callers must persist it
    intentionally; never logged.
    """

    __slots__ = ("ok", "access_token", "expires_in", "error")

    def __init__(
        self,
        *,
        ok: bool,
        access_token: str = "",
        expires_in: int = 0,
        error: str = "",
    ):
        self.ok = ok
        self.access_token = access_token
        self.expires_in = expires_in  # seconds; typically 60-day = 5,184,000
        self.error = error

    def __repr__(self) -> str:
        if self.ok:
            return f"<ThreadsRefreshResult ok expires_in={self.expires_in}s>"
        return f"<ThreadsRefreshResult FAILED: {self.error[:60]}>"


def refresh_threads_token(current_token: str, *, timeout: float = 10.0) -> ThreadsRefreshResult:
    """Refresh a Threads long-lived access token via the graph.threads.net
    refresh endpoint.

    Per Threads API docs, a long-lived token can be refreshed if it's
    at least 24 hours old. The response shape is:

        {"access_token": "...", "token_type": "bearer", "expires_in": 5184000}

    Never logs ``access_token`` (the value or any prefix of it). Callers
    must persist the result themselves; this function is pure-effect-free
    apart from the outbound HTTP call.

    Returns ``ThreadsRefreshResult(ok=True, ...)`` on success, or
    ``ThreadsRefreshResult(ok=False, error="...")`` on any failure
    (HTTP error, network timeout, malformed JSON, missing field).
    """
    if not current_token:
        return ThreadsRefreshResult(ok=False, error="empty current_token")
    try:
        import requests

        resp = requests.get(
            _THREADS_REFRESH_URL,
            params={
                "grant_type": "th_refresh_token",
                "access_token": current_token,
            },
            timeout=timeout,
        )
        if not resp.ok:
            # Don't include the response body verbatim — it may echo the
            # token. Use status + short reason instead.
            return ThreadsRefreshResult(
                ok=False,
                error=f"HTTP {resp.status_code} {resp.reason}",
            )
        body = resp.json()
        new_token = body.get("access_token", "")
        expires_in = int(body.get("expires_in", 0))
        if not new_token or expires_in <= 0:
            return ThreadsRefreshResult(
                ok=False,
                error=f"malformed response (keys={sorted(body.keys())})",
            )
        return ThreadsRefreshResult(
            ok=True,
            access_token=new_token,
            expires_in=expires_in,
        )
    except Exception as exc:
        # Catch broad — network errors, JSON decode, ImportError if
        # requests not installed, etc. Surface as a structured failure
        # so the caller's alert path can fire uniformly.
        return ThreadsRefreshResult(ok=False, error=f"{type(exc).__name__}: {exc}")
