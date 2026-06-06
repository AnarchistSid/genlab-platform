"""YouTube Data API v3 metrics fetcher (canonical implementation).

Replaces the three parallel copies that lived in
:mod:`genlab_core.scripts.run_fetch_insights`,
:mod:`genlab_core.pipeline.stages.fetch_insights` and
:mod:`genlab_core.learning.metric_collector` — each of which had a subtly
different return shape and bug surface. Centralising here means a fix lands
once and reaches every caller.
"""

from __future__ import annotations

import logging
import os
from typing import Final

import requests

from .types import PlatformMetrics

logger = logging.getLogger(__name__)

_API_BASE: Final[str] = "https://www.googleapis.com/youtube/v3/videos"
_TIMEOUT_S: Final[int] = 15


def fetch_youtube(post_id: str, *, api_key: str | None = None) -> PlatformMetrics | None:
    """Return a :class:`PlatformMetrics` for the given YouTube ``post_id``.

    Returns ``None`` on any of:

    * no ``YOUTUBE_API_KEY`` in env (and none passed explicitly)
    * non-200 response
    * empty ``items`` (post not found / deleted / private)
    * network failure (logged at WARN, swallowed)

    ``api_key`` may be passed explicitly when the caller has already resolved
    per-niche credentials; otherwise falls back to the process env var. This
    is the only API by which YouTube credentials enter — no direct
    ``os.getenv`` reads downstream.
    """
    key = api_key or os.getenv("YOUTUBE_API_KEY", "")
    if not key:
        logger.warning("[platforms.metrics.youtube] YOUTUBE_API_KEY not set — analytics missing")
        return None

    try:
        resp = requests.get(
            _API_BASE,
            params={"part": "statistics", "id": post_id, "key": key},
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning(
            "[platforms.metrics.youtube] request failed for %s: %s",
            post_id,
            exc,
        )
        return None

    if resp.status_code != 200:
        return None

    items = resp.json().get("items", [])
    if not items:
        return None

    stats = items[0].get("statistics") or {}
    views = int(stats.get("viewCount") or 0)
    likes = int(stats.get("likeCount") or 0)
    comments = int(stats.get("commentCount") or 0)

    return PlatformMetrics(
        views=views,
        likes=likes,
        comments=comments,
        # YouTube has no separate "reach" metric on the Data API; the
        # analytics-collector callers expect this alias.
        reach=views,
        engagement=likes + comments,
    )
