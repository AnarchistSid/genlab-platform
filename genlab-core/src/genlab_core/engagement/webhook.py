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

from fastapi import FastAPI, HTTPException, Query, Request

logger = logging.getLogger(__name__)

app = FastAPI(title="GenLab Engagement Webhook")

_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
_APP_SECRET = os.environ.get("META_APP_SECRET", "")

# Cache post_id → niche_id lookups (populated from Publishing_Analytics)
_niche_cache: dict[str, str] = {}


def _resolve_niche(media_id: str) -> str:
    """Look up niche_id for an Instagram media ID, defaulting to ai_creators."""
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
        logger.debug("Niche lookup failed for %s: %s", media_id, exc)
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

    # Signature verification
    if _APP_SECRET:
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
