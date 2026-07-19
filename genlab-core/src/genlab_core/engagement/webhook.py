"""Meta webhook receiver for Instagram/Facebook comment events.

Used for real-time comment notifications from Meta. Requires:
  - instagram_manage_comments permission (via App Review)
  - META_APP_SECRET for HMAC signature verification
  - META_WEBHOOK_VERIFY_TOKEN for subscription handshake
  - Public HTTPS endpoint (ngrok for dev, VPS for prod)

Start: uvicorn genlab_core.engagement.webhook:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re

from fastapi import FastAPI, HTTPException, Query, Request

logger = logging.getLogger(__name__)

app = FastAPI(title="GenLab Engagement Webhook")

_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
_APP_SECRET = os.environ.get("META_APP_SECRET", "")
# 2026-06-20 hardening: by default, refuse to accept unsigned events
# when META_APP_SECRET is missing. Operators in dev/test environments
# can set this to ``"0"`` to fall back to "accept unsigned with WARNING"
# behaviour — the only safe place to do that is when the webhook is
# bound to localhost or a private network.
_REQUIRE_SIGNATURE = os.environ.get("GENLAB_REQUIRE_WEBHOOK_SIGNATURE", "1") not in (
    "0",
    "false",
    "False",
    "FALSE",
    "no",
)

# Meta object IDs (media/comment) are numeric, occasionally with an underscore
# separator (e.g. ``<page>_<post>``). R-60: anything outside this shape is
# rejected before it can reach a backlog query formula — see _resolve_niche.
_META_ID_RE = re.compile(r"^[0-9_]+$")

# Cache post_id → niche_id lookups (populated from Publishing_Analytics)
_niche_cache: dict[str, str] = {}


def _resolve_niche(media_id: str) -> str:
    """Look up niche_id for an Instagram media ID, defaulting to ai_creators."""
    # R-60: media_id arrives straight off the Meta webhook body and is
    # interpolated into a backlog query formula below. Validate it's a real
    # numeric Meta object id BEFORE it touches the formula. A non-numeric value
    # can inject query fragments — the SharePoint/Graph path is NOT escaped
    # (and _esc() can't neutralize bare OData operators), and even the
    # parameterized Postgres path only fails the query rather than rejecting the
    # input. Anything malformed → default niche, no query built.
    if not media_id or not _META_ID_RE.match(media_id):
        if media_id:
            logger.warning(
                "Rejecting non-numeric webhook media_id (possible injection): %r", media_id
            )
        return "ai_creators"
    if media_id in _niche_cache:
        return _niche_cache[media_id]
    env_niche = os.environ.get("NICHE_ID", "")
    if env_niche:
        return env_niche
    try:
        from genlab_core.http.backlog_client import BacklogClient

        client = BacklogClient()
        records = client.publishing_analytics.all(
            formula=f"AND({{platform}}='instagram', SEARCH('{media_id}', {{post_id}}))",
            max_records=1,
        )
        if records:
            niche = records[0].get("fields", {}).get("niche_id", "ai_creators")
            _niche_cache[media_id] = niche
            return niche
    except Exception as exc:
        logger.warning(
            "[webhook] Niche lookup failed for media_id=%s — engagement "
            "event may be misrouted (defaults to ai_creators): %s",
            media_id,
            exc,
            exc_info=True,
        )
    return "ai_creators"


@app.get("/webhooks/meta")
async def verify_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
):
    """Meta webhook verification handshake (GET).

    Meta sends hub.mode, hub.challenge, hub.verify_token as query params.
    On successful verification, return hub.challenge as a plain integer.
    """
    if hub_mode == "subscribe" and hub_verify_token == _VERIFY_TOKEN:
        logger.info("[WEBHOOK] Meta webhook verified")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhooks/meta")
async def receive_meta_event(request: Request):
    """Receive Meta comment webhook events (POST).

    Always returns 200 to Meta to prevent exponential retry floods.
    Errors are logged, not propagated.
    """
    body = await request.body()

    # Signature verification (MANDATORY, 2026-06-20 hardening).
    #
    # Pre-fix the check was ``if _APP_SECRET: <verify>`` — when
    # ``META_APP_SECRET`` was unset, signature verification SILENTLY
    # SKIPPED and every POST to ``/webhooks/meta`` was accepted as
    # valid. An attacker who learned the public URL could inject fake
    # comment events: spam the engagement engine, exhaust dramatiq
    # workers, force reply-LLM API calls (cost), or trigger replies
    # to non-existent comments (auditable footprint on the
    # operator-owned brand pages).
    #
    # Two-layer defense:
    #   1. ``GENLAB_REQUIRE_WEBHOOK_SIGNATURE`` env (default ``"1"``)
    #      means "fail loudly on every request if ``META_APP_SECRET``
    #      is missing". Set to ``"0"`` ONLY in dev/test environments
    #      where the webhook is reachable only from localhost / a
    #      private network.
    #   2. When ``META_APP_SECRET`` IS set, verification is performed
    #      and a bad signature still raises 403.
    if not _APP_SECRET:
        if _REQUIRE_SIGNATURE:
            logger.error(
                "[WEBHOOK] META_APP_SECRET is not set; refusing to accept "
                "unsigned event. Set the env var, or set "
                "GENLAB_REQUIRE_WEBHOOK_SIGNATURE=0 for dev/test only."
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Webhook signature verification not configured "
                    "(META_APP_SECRET missing). Refusing unsigned event."
                ),
            )
        # Dev/test escape hatch — operator must explicitly opt OUT
        logger.warning(
            "[WEBHOOK] META_APP_SECRET unset AND GENLAB_REQUIRE_WEBHOOK_SIGNATURE=0; "
            "accepting UNSIGNED event (dev mode)"
        )
    else:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        data = json.loads(body)
    except ValueError as e:
        logger.warning("[WEBHOOK] Non-JSON body (%d bytes): %s", len(body), e)
        return {"status": "ok"}

    try:
        _process_comment_events(data)
    except Exception as e:
        logger.error("[WEBHOOK] Event processing failed: %s", e, exc_info=True)

    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


def _process_comment_events(webhook_data: dict) -> None:
    """Parse Meta webhook payload and dispatch to Dramatiq queue."""
    for entry in webhook_data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue

            value = change.get("value", {})
            comment_id = value.get("id", "")
            if not comment_id:
                continue

            media_id = value.get("media", {}).get("id", "")
            niche_id = _resolve_niche(media_id)

            event = {
                "comment_id": comment_id,
                "comment_text": value.get("text", ""),
                "platform": "instagram",
                "niche_id": niche_id,
                "post_id": media_id,
                "post_context": "",
            }

            is_question = "?" in event["comment_text"]

            try:
                if is_question:
                    from genlab_core.engagement.tasks import reply_to_comment_high

                    reply_to_comment_high.send(event)
                else:
                    from genlab_core.engagement.tasks import reply_to_comment_normal

                    reply_to_comment_normal.send(event)
                logger.info(
                    "[WEBHOOK] Dispatched comment %s (priority=%s)",
                    comment_id,
                    "high" if is_question else "normal",
                )
            except Exception as e:
                logger.error("[WEBHOOK] Failed to dispatch %s: %s", comment_id, e)
