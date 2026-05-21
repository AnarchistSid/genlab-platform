"""YouTube API quota monitoring endpoint.

Routes:
    GET /api/v1/monitoring/youtube-quota  -- daily quota usage + upload count
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from flask import Blueprint

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("youtube_quota_api", __name__, url_prefix="/api/v1/monitoring")


@bp.route("/youtube-quota")
def youtube_quota_status():
    """Returns YouTube API quota usage for today."""
    try:
        from genlab_core.monitoring.youtube_quota import YouTubeQuotaTracker

        tracker = YouTubeQuotaTracker()
        data = tracker.status()
        # Ensure reset_date always reflects the current UTC date
        data["reset_date"] = datetime.now(UTC).strftime("%Y-%m-%d")
        return api_success(data=data)
    except Exception as exc:
        logger.error("YouTube quota check failed: %s", exc)
        return api_error(error=str(exc), code=500)
