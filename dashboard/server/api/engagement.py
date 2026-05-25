"""Engagement engine status API for Mission Control.

Routes:
    GET  /api/v1/engagement/status                       -- replies sent today, pending, platform breakdown
    GET  /api/v1/engagement/pending-replies               -- list replies awaiting human approval
    POST /api/v1/engagement/pending-replies/<id>/approve  -- approve and send a queued reply
    POST /api/v1/engagement/pending-replies/<id>/reject   -- reject a queued reply
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, request

from server.core.responses import api_error, api_not_found, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("engagement_api", __name__, url_prefix="/api/v1/engagement")

NICHE_IDS = ["ai_creators", "gaming", "sports", "movies", "anime"]

# In-memory cache for the /recent endpoint (Instagram API calls are expensive ~6.5s)
_recent_cache: dict = {"data": None, "ts": 0.0}
_RECENT_CACHE_TTL = 120  # seconds


def _recheck_outbound_toxicity(text: str):
    """R-77: re-screen a dashboard-approved reply before it is posted.

    The queued reply text was only screened when generated; a reviewer can edit
    it in the UI, so the dispatched text must be re-gated. Returns the
    ToxicityResult when the model actually ran (caller blocks on ``is_toxic``),
    or ``None`` when Detoxify isn't installed on this host — in which case the
    human approval stands (fail-OPEN on infra absence, since a person already
    vetted it; this is a secondary net, not the primary gate).
    """
    try:
        import importlib.util

        if importlib.util.find_spec("detoxify") is None:
            logger.warning("Detoxify not installed — skipping approve-time toxicity re-check")
            return None
        from genlab_core.engagement.toxicity_gate import ToxicityGate

        return ToxicityGate().check_outbound(text)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Outbound toxicity re-check unavailable: %s", e)
        return None


def _get_client():
    from server.core.graph_sync import get_sync_client

    return get_sync_client()


def _count_replied_today(agent_root: Path) -> dict:
    """Parse the idempotency JSONL file for today's reply count per platform."""
    replied_file = agent_root / ".engagement_replied.jsonl"
    counts: dict[str, int] = {}
    if not replied_file.exists():
        return counts

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        for line in replied_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                # Only count replies from today
                ts = record.get("ts", "")
                if ts and not ts.startswith(today):
                    continue
                platform = record.get("p", "unknown")
                counts[platform] = counts.get(platform, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue
    except OSError:
        pass
    return counts


@bp.route("/status")
def engagement_status():
    """Returns engagement engine status for the dashboard.

    Queries PendingEngagement SharePoint list for pending/replied/failed counts,
    plus local replied-set for today's activity.
    """
    try:
        client = _get_client()
    except Exception as exc:
        logger.error("BacklogClient init failed: %s", exc)
        return api_error(error="backlog_unavailable", code=502)

    result = {
        "by_niche": {},
        "totals": {
            "pending": 0,
            "replied": 0,
            "failed": 0,
            "skipped": 0,
        },
    }

    for niche_id in NICHE_IDS:
        niche_stats = {"pending": 0, "replied": 0, "failed": 0, "skipped": 0}

        try:
            proxy = getattr(client, "pending_engagement", None)
            if proxy is None:
                result["by_niche"][niche_id] = niche_stats
                continue

            items = proxy.all(
                formula=f"{{niche_id}}='{niche_id}'",
            )

            for item in items:
                fields = item.get("fields", {})
                status = (fields.get("status") or "").lower()
                if status == "replied":
                    niche_stats["replied"] += 1
                elif status == "failed":
                    niche_stats["failed"] += 1
                elif status in ("skipped", "rate_limited"):
                    niche_stats["skipped"] += 1
                else:
                    niche_stats["pending"] += 1

        except Exception as exc:
            logger.warning("Engagement status query failed for %s: %s", niche_id, exc)

        result["by_niche"][niche_id] = niche_stats
        for key in result["totals"]:
            result["totals"][key] += niche_stats[key]

    # Platform breakdown from local replied set
    import os

    agent_root = Path(
        os.environ.get(
            "GENLAB_PROJECT_ROOT",
            str(Path(__file__).resolve().parent.parent.parent.parent),
        )
    )
    result["replied_today_by_platform"] = _count_replied_today(agent_root)

    return api_success(data=result)


@bp.route("/queue")
def engagement_queue():
    """Return pending engagement items from the PendingEngagement table."""
    try:
        client = _get_client()
        proxy = getattr(client, "pending_engagement", None)
        if proxy is None:
            return api_success(data={"pending": [], "total": 0})
        items = proxy.all(formula="{status}='pending'", max_records=50)
        pending = [r.get("fields", r) for r in items]
        return api_success(data={"pending": pending, "total": len(pending)})
    except Exception as exc:
        logger.warning("engagement_queue failed: %s", exc)
        return api_success(data={"pending": [], "total": 0, "error": str(exc)[:100]})


# ── Pending Replies (stopgap JSON file store) ──────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent.parent
_PENDING_REPLIES_FILE = _DASHBOARD_ROOT.parent / ".tmp" / "pending_replies.json"


def _load_pending_replies() -> list[dict]:
    """Load pending replies from the JSON stopgap file."""
    if not _PENDING_REPLIES_FILE.is_file():
        return []
    try:
        with open(_PENDING_REPLIES_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load pending replies: %s", exc)
    return []


def _save_pending_replies(replies: list[dict]) -> None:
    """Persist pending replies to the JSON stopgap file."""
    _PENDING_REPLIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_PENDING_REPLIES_FILE, "w") as f:
        json.dump(replies, f, indent=2)


@bp.route("/pending-replies", methods=["GET"])
def list_pending_replies():
    """List engagement replies awaiting human approval.

    Query params:
        niche_id  -- filter by niche (optional)
        status    -- filter by status: pending, approved, rejected (default: pending)
    """
    replies = _load_pending_replies()

    niche_filter = request.args.get("niche_id")
    status_filter = request.args.get("status", "pending")

    filtered = [
        r
        for r in replies
        if (not niche_filter or r.get("niche_id") == niche_filter)
        and r.get("status", "pending") == status_filter
    ]

    return api_success(
        data={
            "replies": filtered,
            "total": len(filtered),
            "all_count": len(replies),
        }
    )


@bp.route("/pending-replies", methods=["POST"])
def create_pending_reply():
    """Create a new pending reply for human review.

    Body (JSON):
        comment_id   -- original comment ID (required)
        platform     -- platform name (required)
        niche_id     -- niche identifier (required)
        comment_text -- the original comment text (required)
        reply_text   -- generated reply text (required)
        author       -- comment author name (optional)
    """
    data = request.get_json(silent=True) or {}

    required_fields = ["comment_id", "platform", "niche_id", "comment_text", "reply_text"]
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        return api_error(
            error=f"Missing required fields: {', '.join(missing)}",
            message="Validation failed",
            code=400,
        )

    reply_id = str(uuid.uuid4())
    reply = {
        "id": reply_id,
        "comment_id": data["comment_id"],
        "platform": data["platform"],
        "niche_id": data["niche_id"],
        "comment_text": data["comment_text"],
        "reply_text": data["reply_text"],
        "author": data.get("author", "unknown"),
        # R-77: carry the post/media id so the dispatch can pass it as
        # post_reply(context_id=...) for log traceability.
        "post_id": data.get("post_id", ""),
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
        "reviewed_at": None,
    }

    replies = _load_pending_replies()
    replies.append(reply)
    _save_pending_replies(replies)

    return api_success(data=reply, code=201)


@bp.route("/pending-replies/<reply_id>/approve", methods=["POST"])
def approve_reply(reply_id: str):
    """Approve a queued reply and dispatch it to the platform."""
    replies = _load_pending_replies()

    # R-77: a reviewer may edit the reply in the UI before approving; honor the
    # edited text from the request body (falls back to the queued text).
    data = request.get_json(silent=True) or {}
    edited_text = (data.get("reply_text") or "").strip()

    for reply in replies:
        if reply.get("id") == reply_id:
            if reply.get("status") != "pending":
                return api_error(
                    error=f"Reply already {reply.get('status')}",
                    message="Cannot approve a non-pending reply",
                    code=409,
                )
            if edited_text:
                reply["reply_text"] = edited_text

            # R-77: re-gate the (possibly edited) text before it is posted. The
            # generated text was screened once at creation, but an edit bypasses
            # that — never dispatch un-rechecked text. Block on a real toxic flag;
            # if the model is unavailable the human approval stands.
            tox = _recheck_outbound_toxicity(reply["reply_text"])
            if tox is not None and tox.is_toxic:
                reply["status"] = "blocked"
                reply["block_reason"] = f"toxicity {tox.max_dimension}={tox.max_score:.2f}"
                reply["reviewed_at"] = datetime.now(UTC).isoformat()
                _save_pending_replies(replies)
                return api_error(
                    error="Reply blocked by toxicity gate",
                    message=(
                        f"Edited reply scored toxic "
                        f"({tox.max_dimension}={tox.max_score:.2f}); not dispatched"
                    ),
                    code=422,
                )

            reply["status"] = "approved"
            reply["reviewed_at"] = datetime.now(UTC).isoformat()

            # Dispatch the approved reply to the platform
            try:
                from genlab_core.platforms import get_client

                client = get_client(reply["platform"])
                if hasattr(client, "post_reply"):
                    client.post_reply(
                        parent_id=reply["comment_id"],
                        text=reply["reply_text"],
                        context_id=reply.get("post_id", ""),
                    )
                    reply["dispatched_at"] = datetime.now(UTC).isoformat()
            except Exception as e:
                logger.warning("Failed to dispatch approved reply: %s", e)
                reply["dispatch_error"] = str(e)

            _save_pending_replies(replies)
            return api_success(data=reply, message="Reply approved")

    return api_not_found(message=f"Reply {reply_id} not found")


@bp.route("/pending-replies/<reply_id>/reject", methods=["POST"])
def reject_reply(reply_id: str):
    """Reject a queued reply."""
    replies = _load_pending_replies()
    data = request.get_json(silent=True) or {}

    for reply in replies:
        if reply.get("id") == reply_id:
            if reply.get("status") != "pending":
                return api_error(
                    error=f"Reply already {reply.get('status')}",
                    message="Cannot reject a non-pending reply",
                    code=409,
                )
            reply["status"] = "rejected"
            reply["reviewed_at"] = datetime.now(UTC).isoformat()
            reply["rejection_reason"] = data.get("reason", "")
            _save_pending_replies(replies)
            return api_success(data=reply, message="Reply rejected")

    return api_not_found(message=f"Reply {reply_id} not found")


@bp.route("/recent", methods=["GET"])
def recent_comments():
    """Return the 30 most recent Instagram comments across all niches.

    For each niche fetches the latest 10 media items, then fetches comments
    for any media that has comments.  Returns the 30 most recent comments
    across all niches sorted by timestamp descending.

    Cached for 120s — Instagram API round-trips are expensive (~6.5s uncached).
    """
    now = time.monotonic()
    if _recent_cache["data"] is not None and now - _recent_cache["ts"] < _RECENT_CACHE_TTL:
        return api_success(data=_recent_cache["data"])

    import requests as _requests
    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    all_comments: list[dict] = []

    for niche_id in NICHE_IDS:
        try:
            creds = resolve_meta_credentials(niche_id)
        except Exception as exc:
            logger.warning("recent_comments: could not resolve creds for %s: %s", niche_id, exc)
            continue

        ig_id = creds.get("ig_user_id", "")
        token = creds.get("ig_access_token", "")
        if not token or not ig_id:
            logger.warning("recent_comments: missing ig credentials for niche %s", niche_id)
            continue

        try:
            media_resp = _requests.get(
                f"https://graph.facebook.com/v21.0/{ig_id}/media",
                params={
                    "fields": "id,comments_count",
                    "limit": 10,
                    "access_token": token,
                },
                timeout=15,
            )
            media_resp.raise_for_status()
            media_items = media_resp.json().get("data", [])
        except Exception as exc:
            logger.warning("recent_comments: media fetch failed for %s: %s", niche_id, exc)
            continue

        for item in media_items:
            if not item.get("comments_count"):
                continue

            try:
                comments_resp = _requests.get(
                    f"https://graph.facebook.com/v21.0/{item['id']}/comments",
                    params={
                        "fields": "id,text,username,timestamp",
                        "limit": 10,
                        "access_token": token,
                    },
                    timeout=15,
                )
                comments_resp.raise_for_status()
                comments = comments_resp.json().get("data", [])
            except Exception as exc:
                logger.warning(
                    "recent_comments: comments fetch failed for media %s (%s): %s",
                    item.get("id"),
                    niche_id,
                    exc,
                )
                continue

            for comment in comments:
                all_comments.append(
                    {
                        "id": comment.get("id", ""),
                        "text": comment.get("text", ""),
                        "username": comment.get("username", ""),
                        "timestamp": comment.get("timestamp", ""),
                        "media_id": item["id"],
                        "niche_id": niche_id,
                        "platform": "instagram",
                    }
                )

    # Sort by timestamp descending; ISO 8601 strings sort lexicographically
    all_comments.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
    recent = all_comments[:30]

    result = {"comments": recent, "total": len(recent)}
    _recent_cache["data"] = result
    _recent_cache["ts"] = time.monotonic()
    return api_success(data=result)


@bp.route("/fetch-comments", methods=["POST"])
def fetch_comments():
    """Manually fetch recent comments from Instagram for all niches.

    Useful for backfilling engagement data that webhooks missed.
    """
    import requests as _requests
    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    total_comments = 0
    results = {}

    for niche_id in NICHE_IDS:
        creds = resolve_meta_credentials(niche_id)
        ig_id = creds.get("ig_user_id", "")
        token = creds.get("ig_access_token", "")
        if not token or not ig_id:
            results[niche_id] = {"error": "missing ig credentials"}
            continue

        try:
            # Get recent media
            media_resp = _requests.get(
                f"https://graph.facebook.com/v21.0/{ig_id}/media",
                params={"fields": "id,comments_count", "limit": 20, "access_token": token},
                timeout=15,
            )
            media = media_resp.json().get("data", [])

            niche_comments = 0
            for item in media:
                if item.get("comments_count", 0) == 0:
                    continue

                # Fetch comments for this media
                comments_resp = _requests.get(
                    f"https://graph.facebook.com/v21.0/{item['id']}/comments",
                    params={
                        "fields": "id,text,username,timestamp",
                        "limit": 50,
                        "access_token": token,
                    },
                    timeout=15,
                )
                comments = comments_resp.json().get("data", [])
                niche_comments += len(comments)

            total_comments += niche_comments
            results[niche_id] = {"comments_found": niche_comments}
        except Exception as e:
            results[niche_id] = {"error": str(e)[:100]}

    return api_success(data={"total_comments": total_comments, "niches": results})
