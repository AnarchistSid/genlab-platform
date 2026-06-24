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


# PR #543 (2026-06-24, SR-F wire pass 4): the publishing-queue endpoints
# are queue-level mutations on the same blueprints the review endpoints
# touch. Same tenant guard applies — a tenant-B operator shouldn't be
# able to approve/hold/release/unschedule/archive a tenant-A blueprint
# even through this alternate API surface. We import the existing guard
# from blueprints.py rather than re-implementing so a single helper
# change propagates everywhere.
def _enforce_queue_tenant_guard(record_id: str):
    """Lazy-imported wrapper for the SR-F guard from blueprints.py.

    Lazy import lets this module load even if blueprints.py imports
    haven't been resolved yet (Flask blueprint registration order).
    """
    from server.api.blueprints import _enforce_blueprint_niche_allowlist

    return _enforce_blueprint_niche_allowlist(record_id)


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
        from server.api.blueprints import _record_niche_id, _transform_media

        items = [_transform_media(item, lite=True) for item in items]

        # PR #543 (SR-F wire pass 4): per-user allowlist filter on the
        # queue list. Symmetric with /review-queue (#540) — operator
        # scoped to gaming only sees gaming items in the queue. Default
        # (unrestricted) returns the full list unchanged.
        from server.auth.niche_allowlist import get_allowed_niches

        _allowed = get_allowed_niches()
        if _allowed is not None:
            items = [it for it in items if _record_niche_id(it) in _allowed]

        return api_success(
            data={"data": items, "meta": {"total": len(items), "niche_id": niche_id}}
        )
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


def _log_queue_calibration(action: str, record_id: str) -> None:
    """Best-effort calibration log for the 3 Publishing Queue endpoints
    (approve/hold/release). Each opens its own BacklogClient because
    the QueueManager doesn't expose one; the helper handles all
    fail-open semantics. Exists at module level so each endpoint stays
    a 3-line wire-up.

    Found by 2026-06-15 post-shipping audit of PR #231: these 3
    endpoints update action_taken via PublishingQueueManager but
    bypassed calibration_logger — exactly the S2 bug PR #231 was
    meant to close, except for the queue surface instead of the
    review surface. Once Mission Control's Publishing Queue view is
    the operator's primary surface, calibration data would quietly
    stop accumulating again."""
    try:
        from server.core.calibration_helper import log_calibration_for_action
        from server.core.graph_sync import get_sync_client

        log_calibration_for_action(
            client=get_sync_client(),
            record_id=record_id,
            action=action,
        )
    except Exception as cal_exc:  # noqa: BLE001 — never block the caller
        logger.debug("[calibration] queue endpoint log skipped: %s", cal_exc)


@bp.route("/queue/<record_id>/approve", methods=["POST"])
def approve_item(record_id):
    """Approve a blueprint for publishing."""
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    # PR #543 (SR-F wire pass 4): tenant guard — see _enforce_queue_tenant_guard.
    _err = _enforce_queue_tenant_guard(record_id)
    if _err is not None:
        return _err
    data = request.json or {}
    try:
        mgr = _get_queue_manager()
        mgr.approve(
            record_id,
            notes=data.get("notes", ""),
            niche_id=data.get("niche_id", ""),
        )
        _log_queue_calibration("approved", record_id)

        # Emit socket event
        try:
            from server.review_server import socketio

            socketio.emit(
                "blueprint_updated",
                {
                    "id": record_id,
                    "record_id": record_id,
                    "action": "approved",
                    "queue_status": "APPROVED",
                },
            )
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
    # PR #543 (SR-F wire pass 4): tenant guard — see _enforce_queue_tenant_guard.
    _err = _enforce_queue_tenant_guard(record_id)
    if _err is not None:
        return _err
    data = request.json or {}
    reason = data.get("reason", "")
    try:
        mgr = _get_queue_manager()
        mgr.hold(record_id, reason=reason)
        # "held" maps to "rejected" in the calibration vocabulary
        # via _ACTION_ALIAS in calibration_helper — see the docstring
        # there for why hold ≡ "operator says don't publish (yet)".
        _log_queue_calibration("held", record_id)

        try:
            from server.review_server import socketio

            socketio.emit(
                "blueprint_updated",
                {
                    "id": record_id,
                    "record_id": record_id,
                    "action": "held",
                    "queue_status": "HELD",
                },
            )
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
    # PR #543 (SR-F wire pass 4): tenant guard — see _enforce_queue_tenant_guard.
    _err = _enforce_queue_tenant_guard(record_id)
    if _err is not None:
        return _err
    try:
        mgr = _get_queue_manager()
        mgr.release(record_id)
        # "released" maps to "skipped" — operator's signal is "not
        # acting on this right now", not a verdict on the content.
        _log_queue_calibration("released", record_id)

        try:
            from server.review_server import socketio

            socketio.emit(
                "blueprint_updated",
                {
                    "id": record_id,
                    "record_id": record_id,
                    "action": "released",
                    "queue_status": "PENDING_APPROVAL",
                },
            )
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
    # PR #543 (SR-F wire pass 4): tenant guard — see _enforce_queue_tenant_guard.
    _err = _enforce_queue_tenant_guard(record_id)
    if _err is not None:
        return _err
    try:
        from server.core.graph_sync import get_sync_client

        client = get_sync_client()
        # Keep action_taken=approved so the post appears in the unscheduled pool
        # (the pool filters for approved + no scheduled_for)
        client.blueprints.update(
            record_id,
            {
                "scheduled_for": None,
            },
        )

        try:
            from server.review_server import socketio

            socketio.emit(
                "blueprint_updated",
                {
                    "id": record_id,
                    "record_id": record_id,
                    "action": "unscheduled",
                    "queue_status": "PENDING_APPROVAL",
                },
            )
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
    # PR #543 (SR-F wire pass 4): tenant guard — see _enforce_queue_tenant_guard.
    _err = _enforce_queue_tenant_guard(record_id)
    if _err is not None:
        return _err
    try:
        from server.core.graph_sync import get_sync_client

        client = get_sync_client()
        client.blueprints.update(
            record_id,
            {
                "status": "ARCHIVED",
                "action_taken": "archived",
                "scheduled_for": None,
            },
        )
        # S2 (2026-06-15): archive previously bypassed calibration_logger.
        # Operator-driven archive is semantically a strong reject (don't
        # publish this), so the helper maps "archived" → "rejected" for
        # the confusion-matrix denominator. Without this, every archive
        # was an invisible operator action — making "operator agreed
        # with gate's reject" appear less common than reality.
        from server.core.calibration_helper import log_calibration_for_action

        log_calibration_for_action(client=client, record_id=record_id, action="archived")

        try:
            from server.review_server import socketio

            socketio.emit(
                "blueprint_updated",
                {
                    "id": record_id,
                    "record_id": record_id,
                    "action": "archived",
                },
            )
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
    return api_success(
        data={
            "slots": [],
            "redirect": "/api/v1/schedule",
            "status": "use_schedule_endpoint",
            "message": "Publishing schedule is served at /api/v1/schedule",
        }
    )
