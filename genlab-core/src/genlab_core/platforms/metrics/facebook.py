"""Facebook metrics fetcher (canonical implementation).

Facebook Reels and the legacy crosspost surface require different insights
endpoints: ``/{post_id}/insights`` returns HTTP 400 on Reels, so this caller
uses ``/{post_id}/video_insights`` which Meta uses for the Reels family.

Returns a :class:`PlatformMetrics` with ``views/likes/comments/shares/reach/watch_time_ms/engagement``.
"""

from __future__ import annotations

import logging
from typing import Final

import requests

from genlab_core.platforms.meta_api import META_GRAPH_BASE_URL

from .types import PlatformMetrics

logger = logging.getLogger(__name__)

_API_BASE: Final[str] = META_GRAPH_BASE_URL
_TIMEOUT_S: Final[int] = 15


def _resolve_credentials(niche_id: str) -> str:
    """Return the per-niche FB page access token or ``""``."""
    from genlab_core.publishing.niche_credentials import resolve_fb_credentials

    token, _page_id = resolve_fb_credentials(niche_id)
    return token


def fetch_facebook(
    post_id: str,
    *,
    token: str | None = None,
    niche_id: str = "",
) -> PlatformMetrics | None:
    """Return a :class:`PlatformMetrics` for the given FB ``post_id``.

    ``post_id`` is the FB-native id (e.g. ``page_id_postnumber``). Returns
    ``None`` on missing token, non-200, or network failure.

    Credentials are resolved from ``niche_id`` unless ``token`` is passed
    explicitly.
    """
    if token is None:
        token = _resolve_credentials(niche_id)

    if not token:
        logger.warning(
            "[platforms.metrics.facebook] no FB token for niche %r — analytics missing",
            niche_id,
        )
        return None

    try:
        resp = requests.get(
            f"{_API_BASE}/{post_id}/video_insights",
            params={"access_token": token},
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning("[platforms.metrics.facebook] request failed for %s: %s", post_id, exc)
        return None

    if resp.status_code != 200:
        logger.debug(
            "[platforms.metrics.facebook] video_insights %d: %s",
            resp.status_code,
            resp.text[:100],
        )
        return None

    views = 0
    watch_time_ms = 0
    likes = 0
    for item in resp.json().get("data", []):
        name = item.get("name", "")
        values = item.get("values", [{}])
        val = values[0].get("value", 0) if values else 0
        if name == "post_video_likes_by_reaction_type" and isinstance(val, dict):
            # Sum all reaction types (Like, Love, Wow, …) into the canonical
            # ``likes`` bucket.
            likes = sum(int(v or 0) for v in val.values())
        elif name == "post_video_views":
            views = int(val or 0)
        elif name == "post_video_view_time":
            watch_time_ms = int(val or 0)

    return PlatformMetrics(
        views=views,
        reach=views,  # FB has no separate reach on /video_insights; alias.
        likes=likes,
        watch_time_ms=watch_time_ms,
        # views proxies engagement here because /video_insights doesn't
        # expose shares/comments directly — the original implementation
        # used the same formula and we preserve it for write-back stability.
        engagement=likes + views,
    )
