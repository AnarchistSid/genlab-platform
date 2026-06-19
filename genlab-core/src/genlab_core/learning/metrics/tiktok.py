"""TikTok metric fetcher — extracted from learning/metric_collector.py.

Uses TikTok Content Posting API's ``/v2/video/query/`` endpoint with an
app-wide bearer token (``TIKTOK_ACCESS_TOKEN`` env var — TikTok is not
per-niche-credentialed in production yet because the TikTok platform client
itself is currently a 31-LOC stub per ``platforms/tiktok.py``).

When the TikTok client is properly implemented (v2 review P10 item), the
token resolution should migrate to ``niche_credentials.resolve_tiktok_*``
and this fetcher should pick up the per-niche pattern automatically.

Migration note (P5a phase 5, 2026-06-19): fifth per-platform module in the
metric_collector split. Small (~50 LOC). No module-level state.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _fetch_tiktok(post_id: str, niche_id: str = "") -> dict:
    """TikTok Content Posting API — video insights."""
    import os

    import requests

    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()  # TikTok disabled, no per-niche yet
    if not token:
        return {}
    try:
        resp = requests.post(
            "https://open.tiktokapis.com/v2/video/query/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "filters": {"video_ids": [post_id]},
                "fields": ["id", "like_count", "comment_count", "share_count", "view_count"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        videos = resp.json().get("data", {}).get("videos", [])
        if not videos:
            return {}
        v = videos[0]
        return {
            "views": v.get("view_count", 0),
            "likes": v.get("like_count", 0),
            "comments": v.get("comment_count", 0),
            "shares": v.get("share_count", 0),
        }
    except Exception as exc:
        logger.warning("[metric_collector] TikTok fetch failed for %s: %s", post_id, exc)
        return {}


__all__ = ["_fetch_tiktok"]
