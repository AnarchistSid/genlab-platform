"""Reddit video post fetcher — OAuth app-only, no user login.

Polls subreddits for high-upvote video posts. Returns posts with
direct v.redd.it video URLs that yt-dlp can download.

Auth: OAuth2 client_credentials (REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET).
Rate: 100 req/min averaged over 10 min.

If REDDIT_CLIENT_ID is not set, returns empty list (never crashes).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_OAUTH_BASE = "https://oauth.reddit.com"
_USER_AGENT = "GenLab/1.0 (content pipeline)"


def _get_access_token(client_id: str, client_secret: str) -> str | None:
    """Acquire OAuth2 app-only token."""
    try:
        resp = requests.post(
            _TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        logger.warning("[Reddit] OAuth token failed: %s", e)
        return None


def fetch_reddit_videos(
    subreddit_configs: list[dict],
    max_per_sub: int = 10,
) -> list[dict[str, Any]]:
    """Fetch top video posts from configured subreddits.

    Args:
        subreddit_configs: List of dicts with ``subreddit``, ``min_score``,
            ``time_filter`` (day/week), and ``content_type`` (video/any).
        max_per_sub: Maximum posts to fetch per subreddit.

    Returns:
        List of story dicts with video URLs, ready for pipeline merge.
    """
    client_id = os.environ.get("REDDIT_CLIENT_ID", "")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        logger.info("[Reddit] REDDIT_CLIENT_ID not set — skipping Reddit fetch")
        return []

    token = _get_access_token(client_id, client_secret)
    if not token:
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": _USER_AGENT,
    }

    results: list[dict[str, Any]] = []

    for cfg in subreddit_configs:
        sub = cfg.get("subreddit", "").lstrip("r/")
        min_score = cfg.get("min_score", 1000)
        time_filter = cfg.get("time_filter", "day")
        content_type = cfg.get("content_type", "video")

        try:
            url = f"{_OAUTH_BASE}/r/{sub}/top"
            resp = requests.get(
                url,
                headers=headers,
                params={"t": time_filter, "limit": max_per_sub},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning("[Reddit] r/%s returned %d", sub, resp.status_code)
                continue

            for child in resp.json().get("data", {}).get("children", []):
                post = child.get("data", {})
                score = post.get("score", 0)
                if score < min_score:
                    continue

                is_video = post.get("is_video", False)
                bool(post.get("media"))

                if content_type == "video" and not is_video:
                    continue

                # Extract video URL
                video_url = ""
                if is_video and post.get("media", {}).get("reddit_video"):
                    video_url = post["media"]["reddit_video"].get("fallback_url", "")

                results.append(
                    {
                        "title": post.get("title", ""),
                        "url": post.get("url", ""),
                        "source_url": f"https://reddit.com{post.get('permalink', '')}",
                        "video_url": video_url,
                        "score": score,
                        "upvote_ratio": post.get("upvote_ratio", 0),
                        "num_comments": post.get("num_comments", 0),
                        "subreddit": sub,
                        "created_utc": post.get("created_utc", 0),
                        "is_video": is_video,
                        "source": "reddit",
                        "_trending_video": is_video and bool(video_url),
                        "_clip_url": video_url if video_url else None,
                    }
                )

        except Exception as e:
            logger.warning("[Reddit] r/%s fetch failed: %s", sub, e)

    logger.info(
        "[Reddit] Fetched %d posts across %d subreddits", len(results), len(subreddit_configs)
    )
    return results
