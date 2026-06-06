"""X (Twitter) metrics fetcher (canonical implementation).

Uses the v2 ``/2/tweets/{id}?tweet.fields=public_metrics`` endpoint. Auth is
a process-wide ``X_BEARER_TOKEN`` env var (no per-niche split today; the
``bearer_token`` kwarg exists for tests and for the per-niche path when
credentials are added later).

Returns a :class:`PlatformMetrics` with ``views/likes/comments/shares/impressions/reach/engagement``.
"""

from __future__ import annotations

import logging
import os
from typing import Final

import requests

from .types import PlatformMetrics

logger = logging.getLogger(__name__)

_API_BASE: Final[str] = "https://api.twitter.com/2/tweets"
_TIMEOUT_S: Final[int] = 15


def fetch_twitter(post_id: str, *, bearer_token: str | None = None) -> PlatformMetrics | None:
    """Return a :class:`PlatformMetrics` for the given X tweet ``post_id``.

    Returns ``None`` on missing token, non-200, or network failure. ``bearer_token``
    overrides the ``X_BEARER_TOKEN`` env var when supplied.
    """
    token = bearer_token or os.getenv("X_BEARER_TOKEN", "")
    if not token:
        logger.warning("[platforms.metrics.twitter] X_BEARER_TOKEN not set — analytics missing")
        return None

    try:
        resp = requests.get(
            f"{_API_BASE}/{post_id}",
            params={"tweet.fields": "public_metrics"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning("[platforms.metrics.twitter] request failed for %s: %s", post_id, exc)
        return None

    if resp.status_code != 200:
        return None

    metrics = (resp.json().get("data") or {}).get("public_metrics") or {}
    likes = int(metrics.get("like_count") or 0)
    retweets = int(metrics.get("retweet_count") or 0)
    replies = int(metrics.get("reply_count") or 0)
    impressions = int(metrics.get("impression_count") or 0)

    return PlatformMetrics(
        views=impressions,
        impressions=impressions,
        reach=impressions,  # alias for cross-platform sort/compare callers
        likes=likes,
        comments=replies,  # canonical name for "comments-equivalent"
        shares=retweets,  # canonical name for "shares-equivalent"
        engagement=likes + retweets + replies,
    )
