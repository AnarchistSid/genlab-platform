"""Dashboard events API — real-time notifications from pipeline and publisher.

Routes:
    GET /api/v1/events/recent  — last 50 events for notification center
"""

import logging
import os

from flask import Blueprint, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

from server.core.responses import api_success

logger = logging.getLogger(__name__)
bp = Blueprint("events_api", __name__, url_prefix="/api/v1/events")


@bp.route("/recent", methods=["GET"])
def recent_events():
    """Return recent dashboard events for the notification center."""
    limit = int(request.args.get("limit", 50))
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_success(data=[])
    try:
        from psycopg.rows import dict_row

        with pg_connect(
            dsn, row_factory=dict_row, niche_id=request.args.get("niche_id", "all") or "all"
        ) as conn:
            rows = conn.execute(
                """SELECT id, event_type, title, body, entity_id, entity_type, niche_id, created_at
                   FROM dashboard_events
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (limit,),
            ).fetchall()

        events = [
            {
                "id": str(r["id"]),
                "type": r["event_type"],
                "title": r["title"],
                "body": r["body"],
                "entity_id": r["entity_id"],
                "entity_type": r["entity_type"],
                "niche_id": r["niche_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
        return api_success(data=events)
    except Exception as e:
        logger.error("Events query failed: %s", e)
        return api_success(data=[])
