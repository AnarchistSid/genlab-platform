"""Google Trends cache endpoint.

Routes:
    GET /api/v1/trends  -- returns trending topics per niche from local cache files
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Blueprint

from server.core.responses import api_success

logger = logging.getLogger(__name__)
bp = Blueprint("trends_api", __name__, url_prefix="/api/v1/trends")

NICHE_IDS = ["ai_creators", "gaming", "sports", "movies", "anime"]

# Cache directory relative to GenLab workspace root
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = _DASHBOARD_ROOT.parent / ".tmp" / "cache"


@bp.route("", methods=["GET"])
def get_trends():
    """Return trending topics for all 5 niches from local cache files.

    Reads ``trends_<niche_id>.json`` from the ``.tmp/cache/`` directory.
    Each file is a list of trending topic strings produced by the Google Trends
    stage of the daily intel pipeline.

    Returns:
        {
            "ai_creators": ["topic1", "topic2", ...],
            "gaming": [...],
            ...
        }
    """
    result: dict[str, list[str]] = {}

    for niche_id in NICHE_IDS:
        cache_file = _CACHE_DIR / f"trends_{niche_id}.json"
        if not cache_file.is_file():
            logger.debug("Trends cache missing for niche %s: %s", niche_id, cache_file)
            result[niche_id] = []
            continue

        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                result[niche_id] = [str(t) for t in data]
            elif isinstance(data, dict):
                # Some cache files may store {topics: [...]} — flatten
                topics = data.get("topics", data.get("trends", []))
                result[niche_id] = [str(t) for t in topics] if isinstance(topics, list) else []
            else:
                result[niche_id] = []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read trends cache for %s: %s", niche_id, exc)
            result[niche_id] = []

    return api_success(data=result)
