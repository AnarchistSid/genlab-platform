"""YouTube Data API v3 fetcher for outbound reply target discovery.

2026-07-17 (Layer 4 batch 2). Completes the outbound engagement loop
scaffolded in Layer 4 batch 1:

    outbound_reply_engine (poller)
        → outbound_youtube_fetcher (THIS MODULE — fetches raw data)
        → outbound_targeting (pure filter/rank)
        → persona_engine + toxicity_gate (generate + safety-check reply)
        → YouTubeClient.post_reply (posts the reply)
        → outbound_reply_history (idempotency dedup)

## Quota-efficient path

Naive path: `search.list?channelId=X&order=date` = **100 units per creator**
per fetch. With 3 creators × 5 niches × 6 fires/day = 9,000 units/day
= 90% of Gen Lab's daily 10K budget. Unacceptable.

Cheap path (this module):
1. Per creator: `channels.list?id=X&part=contentDetails` → returns
   the creator's "uploads" playlist ID. **1 unit.**
2. Per uploads playlist: `playlistItems.list?playlistId=X&maxResults=10`
   → the creator's 10 most-recent uploads. **1 unit.**
3. Batch: `videos.list?id=V1,V2,...&part=snippet,statistics&maxResults=50`
   → view_count, comment_count, published_at, title. **1 unit per
   50 videos.**
4. Per candidate video: `commentThreads.list?videoId=X&order=relevance&
   maxResults=20` → top comments with author + text + likes.
   **1 unit per video.**

Cost per creator: 2 units + 1/25 video (batch) + ~3 comment fetches
(only for videos that pass age + engagement filters) ≈ 5 units per
creator per fetch. **5 units × 3 creators × 5 niches × 6 fires/day
= 450 units/day** — under 5% of daily budget. Acceptable.

## Circuit breaker + quota tracking

Reuses `YOUTUBE_CB` circuit breaker (opens after N consecutive 429s)
and `_increment_quota` from the existing quota tracker. When quota
hard-stop fires (`HARD_STOP_PCT=1.00`), fetcher returns empty and
the poller's Layer 4 batch 1 no-op path handles gracefully.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from genlab_core.http.circuit_breaker import YOUTUBE_CB, CircuitOpenError

logger = logging.getLogger(__name__)


_YT_API_BASE = "https://www.googleapis.com/youtube/v3"
_REQUEST_TIMEOUT = 15


def _get_api_key() -> str:
    """Return the shared YOUTUBE_API_KEY, or empty string if unset."""
    return os.environ.get("YOUTUBE_API_KEY", "").strip()


def _increment_quota_soft(units: int, operation: str) -> None:
    """Increment the shared quota tracker if available; fail-quiet."""
    try:
        from genlab_core.monitoring.youtube_quota import YouTubeQuotaTracker

        tracker = YouTubeQuotaTracker()
        tracker.record("video_list" if units == 1 else "search", niche_id="all")
    except Exception as exc:
        logger.warning(
            "[outbound-yt] quota increment skipped for op=%s — YouTube "
            "quota tracker not counting this call (over-consumption risk): %s",
            operation,
            exc,
            exc_info=True,
        )


def _yt_get(path: str, params: dict[str, Any]) -> dict | None:
    """GET a YouTube Data API v3 endpoint with the shared circuit breaker.

    Returns parsed JSON on success, None on any failure (network,
    quota, circuit-open, non-2xx). Never raises.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.debug("[outbound-yt] YOUTUBE_API_KEY unset — skipping fetch")
        return None

    def _do_request() -> requests.Response:
        resp = requests.get(
            f"{_YT_API_BASE}/{path}",
            params={**params, "key": api_key},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp

    try:
        resp = YOUTUBE_CB.call(_do_request)
        return resp.json()
    except CircuitOpenError:
        logger.warning("[outbound-yt] circuit open — skipping %s", path)
        return None
    except Exception as exc:
        logger.warning("[outbound-yt] %s failed: %s", path, exc)
        return None


def _get_uploads_playlist_id(channel_id: str) -> str | None:
    """Return the creator's "uploads" playlist ID. Costs 1 unit."""
    body = _yt_get(
        "channels",
        {"part": "contentDetails", "id": channel_id, "maxResults": 1},
    )
    if not body:
        return None
    items = body.get("items") or []
    if not items:
        return None
    try:
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except (KeyError, IndexError):
        return None
    finally:
        _increment_quota_soft(1, f"channels.list/{channel_id[:10]}")


def _list_playlist_recent_uploads(
    playlist_id: str, *, max_videos: int = 10
) -> list[str]:
    """Return the most-recent up-to N video IDs from an uploads playlist.

    Costs 1 unit. playlistItems returns items ordered by insertion
    time desc (which for the "uploads" playlist = published desc).
    """
    body = _yt_get(
        "playlistItems",
        {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": min(50, max_videos),
        },
    )
    _increment_quota_soft(1, f"playlistItems.list/{playlist_id[:10]}")
    if not body:
        return []
    video_ids: list[str] = []
    for item in body.get("items") or []:
        vid = ((item or {}).get("contentDetails") or {}).get("videoId")
        if vid:
            video_ids.append(str(vid))
    return video_ids


def _batch_get_video_details(video_ids: list[str]) -> list[dict]:
    """Batch-fetch video metadata (title, view/comment count, published_at)
    + owner channel_id (needed for owner-skip filter). Costs 1 unit
    per 50 videos.

    Returns list of dicts in the shape expected by outbound_targeting.
    """
    if not video_ids:
        return []

    results: list[dict] = []
    # YouTube caps `id` param at 50 comma-separated IDs.
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        body = _yt_get(
            "videos",
            {
                "part": "snippet,statistics",
                "id": ",".join(batch),
                "maxResults": 50,
            },
        )
        _increment_quota_soft(1, f"videos.list/batch-of-{len(batch)}")
        if not body:
            continue
        for item in body.get("items") or []:
            snippet = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            results.append(
                {
                    "video_id": str(item.get("id") or ""),
                    "channel_id": str(snippet.get("channelId") or ""),
                    "title": str(snippet.get("title") or ""),
                    "view_count": int(stats.get("viewCount", 0) or 0),
                    "comment_count": int(stats.get("commentCount", 0) or 0),
                    "published_at": str(snippet.get("publishedAt") or ""),
                    "comments": [],  # filled by _fetch_top_comments
                }
            )
    return results


def _fetch_top_comments(video_id: str, *, max_results: int = 20) -> list[dict]:
    """Fetch top comments on a video by relevance. Costs 1 unit.

    Returns list of comment dicts in the shape expected by
    outbound_targeting.discover_youtube_targets.
    """
    body = _yt_get(
        "commentThreads",
        {
            "part": "snippet",
            "videoId": video_id,
            "order": "relevance",
            "maxResults": min(100, max_results),
            "textFormat": "plainText",
        },
    )
    _increment_quota_soft(1, f"commentThreads.list/{video_id}")
    if not body:
        return []

    comments: list[dict] = []
    for item in body.get("items") or []:
        top_level = ((item or {}).get("snippet") or {}).get("topLevelComment") or {}
        comment_id = str(top_level.get("id") or "")
        snippet = top_level.get("snippet") or {}
        if not comment_id:
            continue
        comments.append(
            {
                "comment_id": comment_id,
                "author_channel_id": str(
                    ((snippet.get("authorChannelId") or {}).get("value")) or ""
                ),
                "author_display_name": str(snippet.get("authorDisplayName") or ""),
                "text": str(snippet.get("textDisplay") or ""),
                "like_count": int(snippet.get("likeCount", 0) or 0),
            }
        )
    return comments


def fetch_creator_recent_videos_with_comments(
    niche_id: str,
    creator_channel_ids: list[str],
    *,
    max_video_age_days: int = 7,
    max_videos_per_creator: int = 10,
    max_comments_per_video: int = 20,
) -> list[dict]:
    """Fetch recent uploads + top comments for a list of creator channels.

    This is the entrypoint the outbound poller
    (`scripts/run_outbound_reply_engine.py`) imports. Returns a list
    in the shape `discover_youtube_targets` consumes.

    Quota cost budget: ~5 units per creator (channels 1 + playlistItems
    1 + videos batch 1 + comments ×2-3 for surviving videos).

    ``max_video_age_days`` is NOT enforced here — it's passed through
    to the targeting layer's video-age filter. This module just
    fetches; filtering happens downstream.

    Returns [] if:
    - YOUTUBE_API_KEY unset
    - All API calls fail (circuit open, quota exhausted)
    - No creators pass through (empty input, all channels invalid)
    """
    if not creator_channel_ids:
        return []
    if not _get_api_key():
        logger.debug(
            "[outbound-yt] niche=%s: YOUTUBE_API_KEY unset — returning empty",
            niche_id,
        )
        return []

    all_video_ids: list[str] = []
    for channel_id in creator_channel_ids:
        playlist_id = _get_uploads_playlist_id(channel_id)
        if not playlist_id:
            continue
        video_ids = _list_playlist_recent_uploads(
            playlist_id, max_videos=max_videos_per_creator
        )
        all_video_ids.extend(video_ids)

    if not all_video_ids:
        logger.info(
            "[outbound-yt] niche=%s: 0 videos found across %d creators",
            niche_id, len(creator_channel_ids),
        )
        return []

    videos = _batch_get_video_details(all_video_ids)
    logger.info(
        "[outbound-yt] niche=%s: fetched metadata for %d videos across %d creators",
        niche_id, len(videos), len(creator_channel_ids),
    )

    # Only fetch comments for videos we'll actually consider — i.e.,
    # those that pass the video-level filters
    # (comment_count ≥20). Saves ~50% of the comment-fetch quota
    # by not paying for videos that will be dropped downstream.
    for video in videos:
        if int(video.get("comment_count", 0) or 0) < 20:
            continue
        video["comments"] = _fetch_top_comments(
            video["video_id"], max_results=max_comments_per_video
        )

    return videos


__all__ = [
    "fetch_creator_recent_videos_with_comments",
]
