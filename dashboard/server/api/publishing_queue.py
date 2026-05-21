"""Publishing Queue API endpoints for Command Centre Phase 2.

Routes:
    GET  /api/v1/queue              — list queue items (filterable by queue_status)
    GET  /api/v1/queue/stats        — counts per queue status
    POST /api/v1/queue/:id/approve  — approve for publishing
    POST /api/v1/queue/:id/hold     — hold with reason
    POST /api/v1/queue/:id/release  — release held item back to pending
    GET  /api/v1/channel-health     — platform health summary
"""

import logging
import re
from pathlib import Path

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("publishing_queue_api", __name__, url_prefix="/api/v1")

RECORD_RE = re.compile(r"^[\w-]+$")  # Integer (SharePoint) or UUID (Postgres)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_queue_manager():
    from server.core.publishing_queue import PublishingQueueManager
    return PublishingQueueManager()


# ── Queue endpoints ──────────────────────────────────────────


@bp.route("/queue", methods=["GET"])
def list_queue():
    """List publishing queue items with virtual queue_status."""
    niche_id = request.args.get("niche_id", "all")
    queue_status = request.args.get("queue_status")
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (ValueError, TypeError):
        limit = 50

    try:
        mgr = _get_queue_manager()
        items = mgr.get_queue(
            niche_id=niche_id,
            queue_status=queue_status,
            limit=limit,
        )

        # Lite transform: resolve visual_paths to URLs for thumbnails
        # without expensive ffprobe / review_server import
        from server.api.blueprints import _transform_media
        items = [_transform_media(item, lite=True) for item in items]

        return api_success(data={"data": items, "meta": {"total": len(items), "niche_id": niche_id}})
    except Exception as e:
        logger.error("Queue list failed: %s", e, exc_info=True)
        return api_error(error="Failed to fetch publishing queue", code=502)


@bp.route("/queue/stats", methods=["GET"])
def queue_stats():
    """Return counts per queue status for the stats bar."""
    niche_id = request.args.get("niche_id", "all")
    try:
        mgr = _get_queue_manager()
        stats = mgr.get_stats(niche_id=niche_id)
        return api_success(data={"data": stats})
    except Exception as e:
        logger.error("Queue stats failed: %s", e, exc_info=True)
        return api_error(error="Failed to fetch queue stats", code=502)


@bp.route("/queue/<record_id>/approve", methods=["POST"])
def approve_item(record_id):
    """Approve a blueprint for publishing."""
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    data = request.json or {}
    try:
        mgr = _get_queue_manager()
        mgr.approve(
            record_id,
            notes=data.get("notes", ""),
            niche_id=data.get("niche_id", ""),
        )

        # Emit socket event
        try:
            from server.review_server import socketio
            socketio.emit("blueprint_updated", {
                "id": record_id,
                "record_id": record_id,
                "action": "approved",
                "queue_status": "APPROVED",
            })
        except Exception:
            pass

        return api_success(data={"status": "ok", "action": "approved", "id": record_id})
    except Exception as e:
        logger.error("Approve failed for %s: %s", record_id, e)
        return api_error(error=f"Approve failed: {e}", code=500)


@bp.route("/queue/<record_id>/hold", methods=["POST"])
def hold_item(record_id):
    """Hold a blueprint — blocks publishing."""
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    data = request.json or {}
    reason = data.get("reason", "")
    try:
        mgr = _get_queue_manager()
        mgr.hold(record_id, reason=reason)

        try:
            from server.review_server import socketio
            socketio.emit("blueprint_updated", {
                "id": record_id,
                "record_id": record_id,
                "action": "held",
                "queue_status": "HELD",
            })
        except Exception:
            pass

        return api_success(data={"status": "ok", "action": "held", "id": record_id})
    except Exception as e:
        logger.error("Hold failed for %s: %s", record_id, e)
        return api_error(error=f"Hold failed: {e}", code=500)


@bp.route("/queue/<record_id>/release", methods=["POST"])
def release_item(record_id):
    """Release a held blueprint back to PENDING_APPROVAL."""
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    try:
        mgr = _get_queue_manager()
        mgr.release(record_id)

        try:
            from server.review_server import socketio
            socketio.emit("blueprint_updated", {
                "id": record_id,
                "record_id": record_id,
                "action": "released",
                "queue_status": "PENDING_APPROVAL",
            })
        except Exception:
            pass

        return api_success(data={"status": "ok", "action": "released", "id": record_id})
    except Exception as e:
        logger.error("Release failed for %s: %s", record_id, e)
        return api_error(error=f"Release failed: {e}", code=500)


# ── Unschedule ──────────────────────────────────────────────


@bp.route("/queue/<record_id>/unschedule", methods=["POST"])
def unschedule_item(record_id):
    """Remove a blueprint from the schedule (clear scheduled_for)."""
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    try:
        from server.core.graph_sync import get_sync_client
        client = get_sync_client()
        # Keep action_taken=approved so the post appears in the unscheduled pool
        # (the pool filters for approved + no scheduled_for)
        client.blueprints.update(record_id, {
            "scheduled_for": None,
        })

        try:
            from server.review_server import socketio
            socketio.emit("blueprint_updated", {
                "id": record_id,
                "record_id": record_id,
                "action": "unscheduled",
                "queue_status": "PENDING_APPROVAL",
            })
        except Exception:
            pass

        return api_success(data={"status": "ok", "action": "unscheduled", "id": record_id})
    except Exception as e:
        logger.error("Unschedule failed for %s: %s", record_id, e)
        return api_error(error=f"Unschedule failed: {e}", code=500)


@bp.route("/queue/<record_id>/archive", methods=["POST"])
def archive_item(record_id):
    """Archive a blueprint — permanently removes from all queues."""
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    try:
        from server.core.graph_sync import get_sync_client
        client = get_sync_client()
        client.blueprints.update(record_id, {
            "status": "ARCHIVED",
            "action_taken": "archived",
            "scheduled_for": None,
        })

        try:
            from server.review_server import socketio
            socketio.emit("blueprint_updated", {
                "id": record_id,
                "record_id": record_id,
                "action": "archived",
            })
        except Exception:
            pass

        return api_success(data={"status": "ok", "action": "archived", "id": record_id})
    except Exception as e:
        logger.error("Archive failed for %s: %s", record_id, e)
        return api_error(error=f"Archive failed: {e}", code=500)


# ── Channel Health ───────────────────────────────────────────


@bp.route("/channel-health", methods=["GET"])
def channel_health():
    """Platform health summary from recent run reports."""
    from server.api.overview import _platform_health_from_reports

    health = _platform_health_from_reports()
    return api_success(data={"data": health})


@bp.route("/publishing/schedule", methods=["GET"])
def publishing_schedule_stub():
    """Stub endpoint — redirects to /api/v1/schedule which has the real implementation."""
    return api_success(data={
        "slots": [],
        "redirect": "/api/v1/schedule",
        "status": "use_schedule_endpoint",
        "message": "Publishing schedule is served at /api/v1/schedule",
    })
