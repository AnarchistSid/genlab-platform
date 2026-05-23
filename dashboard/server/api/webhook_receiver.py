"""Meta webhook receiver stub.

Handles Instagram webhook verification (GET) and event ingestion (POST).
Currently logs events only -- processing to be implemented when Meta approves
the webhook subscription.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from flask import Blueprint, Response, abort, request

from server.core.responses import api_success

logger = logging.getLogger(__name__)

webhook_bp = Blueprint("webhook", __name__, url_prefix="/api/v1/webhooks")

# Meta sends a verify_token during subscription setup
_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")
if not _VERIFY_TOKEN:
    logger.warning(
        "META_WEBHOOK_VERIFY_TOKEN not set — webhook verification will reject all requests"
    )
_APP_SECRET = os.environ.get("META_APP_SECRET", "")


@webhook_bp.route("/meta", methods=["GET"])
def verify_webhook():
    """Handle Meta webhook verification challenge.

    Meta sends: GET /api/v1/webhooks/meta?hub.mode=subscribe&hub.verify_token=TOKEN&hub.challenge=CHALLENGE
    We must respond with the challenge string if the verify_token matches.
    """
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == _VERIFY_TOKEN:
        logger.info("[Webhook] Meta verification successful")
        return Response(challenge, status=200, content_type="text/plain")

    logger.warning(
        "[Webhook] Meta verification failed: mode=%s token_match=%s", mode, token == _VERIFY_TOKEN
    )
    abort(403)


@webhook_bp.route("/meta", methods=["POST"])
def receive_webhook():
    """Receive Meta webhook events (Instagram mentions, comments, etc.).

    Currently logs events only. Future: route to engagement collector.
    """
    # R-14: fail CLOSED. Without a configured app secret we cannot verify
    # authenticity, so reject rather than accept forged events (which would
    # trigger LLM replies + Anthropic spend). Previously this was gated on
    # `if _APP_SECRET:`, so an unset secret skipped verification entirely.
    if not _APP_SECRET:
        logger.error("[Webhook] META_APP_SECRET not set — rejecting unverifiable webhook")
        abort(403)
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(request.data, signature):
        logger.warning("[Webhook] Invalid signature on incoming webhook")
        abort(403)

    try:
        payload = request.get_json(force=True)
    except Exception:
        payload = None

    if not isinstance(payload, dict):
        logger.warning("[Webhook] Invalid JSON in webhook payload")
        abort(400)

    object_type = payload.get("object", "unknown")
    entries = payload.get("entry", [])
    logger.info("[Webhook] Received %s event with %d entries", object_type, len(entries))

    # Field names worth forwarding to the engagement server. Both Instagram
    # product fields and FB Page product fields are included since the same
    # receiver serves both subscriptions. Pre-2026-05-21 this set was only
    # the IG side, which silently dropped every FB Page event because the
    # field-name check failed. Diagnosed when verifying production logs
    # showed ~1 instagram event/day for two weeks with zero forwards.
    forwardable_fields = (
        # Instagram product field names
        "comments",
        "mentions",
        "live_comments",
        "story_insights",
        # FB Page product field names (delivered when Page is subscribed
        # with subscribed_fields=feed, mention, etc.)
        "feed",
        "mention",
    )

    comment_count = 0
    fields_seen: list[str] = []
    for entry in entries:
        entry_id = entry.get("id", "?")
        changes = entry.get("changes", [])
        for change in changes:
            field = change.get("field", "")
            fields_seen.append(field)
            if field in forwardable_fields:
                comment_count += 1
        # Single info line per entry with full field-name list so operators
        # see immediately what Meta is actually delivering.
        logger.info(
            "[Webhook] Entry %s: %d changes, fields=%s",
            entry_id,
            len(changes),
            fields_seen,
        )

    if comment_count > 0:
        # Forward full payload to the standalone engagement webhook server
        try:
            import requests as _requests

            _requests.post(
                "http://127.0.0.1:8765/webhooks/meta",
                json=payload,
                timeout=5,
            )
            logger.info(
                "[Webhook] Forwarded %d engagement events to engagement server (fields=%s)",
                comment_count,
                fields_seen,
            )
        except Exception as exc:
            logger.warning("[Webhook] Failed to forward to engagement server: %s", exc)
    else:
        # Explicitly log when fields are received but not in the forward set
        # so future "missing field name" issues surface immediately.
        logger.info(
            "[Webhook] No forwardable fields in %s event (got %s)",
            object_type,
            fields_seen,
        )

    # Meta requires 200 response within 20 seconds
    return api_success(data={"status": "received", "comments_forwarded": comment_count})


@webhook_bp.route("/meta/deletion", methods=["POST"])
def data_deletion():
    """Handle Meta data deletion callback.

    Meta requires a data deletion callback URL for App Review.
    Since we don't store user data beyond comment processing,
    we acknowledge the request and return a confirmation URL.
    """
    if not _APP_SECRET:  # R-14: fail closed — reject unverifiable callbacks
        abort(403)
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(request.data, signature):
        abort(403)

    try:
        payload = request.get_json(force=True)
    except Exception:
        payload = {}

    user_id = payload.get("user_id", "unknown")
    logger.info("[Webhook] Data deletion request for user %s", user_id)

    import hashlib as _hl

    confirmation_code = _hl.sha256(f"del-{user_id}".encode()).hexdigest()[:16]

    return api_success(
        data={
            "url": f"https://review.aspirehub.ai/api/v1/webhooks/meta/deletion/status?code={confirmation_code}",
            "confirmation_code": confirmation_code,
        }
    )


@webhook_bp.route("/meta/deletion/status", methods=["GET"])
def deletion_status():
    """Return data deletion status (always complete since we don't store user data)."""
    return api_success(data={"status": "complete"})


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 using app secret."""
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.HMAC(_APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
