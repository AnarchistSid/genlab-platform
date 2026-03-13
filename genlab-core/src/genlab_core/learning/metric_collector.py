"""Prefect flow for collecting post-publish metrics at timed windows.

Reads pending feedback tasks from PendingFeedbackStore, checks which
collection windows are due, fetches platform metrics, and updates the
store. At the 48h window, computes a shaped reward via RewardShaper.

Run standalone:
    python -m genlab_core.learning.metric_collector

Or as a Prefect deployment:
    prefect deployment build genlab_core/learning/metric_collector.py:collect_metrics
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# Callback type: (niche_id, content_type, platform, reward) -> None
BanditUpdater = Callable[[str, str, str, float], None]

logger = logging.getLogger(__name__)

try:
    from prefect import flow, task
except ImportError:  # pragma: no cover — Prefect is optional
    # Provide no-op decorators so the module loads without Prefect installed.
    def flow(fn=None, **kwargs):  # type: ignore[misc]
        if fn is not None:
            return fn
        return lambda f: f

    def task(fn=None, **kwargs):  # type: ignore[misc]
        if fn is not None:
            return fn
        return lambda f: f

from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
from genlab_core.learning.pending_feedback_task import (
    CollectionWindow,
    PendingFeedbackTask,
)
from genlab_core.learning.reward_shaper import RewardShaper


# ---------------------------------------------------------------------------
# Platform metric fetching (delegates to lightweight HTTP calls)
# ---------------------------------------------------------------------------

@task(name="fetch_platform_metrics", retries=1)
def fetch_platform_metrics(
    platform: str,
    post_id: str,
    window: CollectionWindow,
) -> dict[str, Any]:
    """Fetch metrics for a single post from its platform API.

    Kept intentionally thin — each platform handler mirrors the patterns
    already established in CriticalRush FeedbackCollector.
    """
    # Instagram Reels: use specialised 6h fetcher for early skip-rate signal
    if platform == "instagram" and window == "6h":
        try:
            return _fetch_instagram_reels_6h(post_id)
        except Exception as exc:
            logger.warning("[metric_collector] instagram reels 6h fetch failed for %s: %s", post_id, exc)
            return {}

    fetchers = {
        "youtube": _fetch_youtube,
        "instagram": _fetch_instagram,
        "facebook": _fetch_facebook,
        "x": _fetch_x,
        "twitter": _fetch_x,
        "tiktok": _fetch_tiktok,
        "threads": _fetch_threads,
    }
    fn = fetchers.get(platform)
    if fn is None:
        logger.warning("[metric_collector] no fetcher for platform '%s'", platform)
        return {}
    try:
        return fn(post_id)
    except Exception as exc:
        logger.warning("[metric_collector] %s fetch failed for %s: %s", platform, post_id, exc)
        return {}


def _fetch_youtube(post_id: str) -> dict:
    """YouTube Data API v3 basic stats."""
    import os
    import requests

    client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip()
    if not all([client_id, client_secret, refresh_token]):
        return {}

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "statistics", "id": post_id},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return {}

    stats = items[0].get("statistics", {})
    return {
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
    }


def _fetch_instagram(post_id: str) -> dict:
    import os
    import requests

    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    if not token:
        return {}
    resp = requests.get(
        f"https://graph.facebook.com/v21.0/{post_id}/insights",
        params={
            "metric": "plays,reach,likes,comments,shares,saved",
            "access_token": token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    metrics: dict[str, Any] = {}
    for item in resp.json().get("data", []):
        name = item.get("name", "")
        vals = item.get("values", [{}])
        val = vals[0].get("value", 0) if vals else 0
        if name == "plays":
            metrics["views"] = val
        elif name in ("reach", "likes", "comments", "shares", "saved"):
            metrics[name] = val
    return metrics


def _fetch_instagram_reels_6h(post_id: str) -> dict:
    """IG Reels-specific metrics for early 6h skip-rate signal."""
    import os
    import requests

    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    if not token:
        return {}
    resp = requests.get(
        f"https://graph.facebook.com/v21.0/{post_id}/insights",
        params={
            "metric": "ig_reels_avg_watch_time,ig_reels_video_view_total_time,plays",
            "access_token": token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    metrics: dict[str, Any] = {}
    for item in resp.json().get("data", []):
        name = item.get("name", "")
        vals = item.get("values", [{}])
        val = vals[0].get("value", 0) if vals else 0
        if name == "ig_reels_avg_watch_time":
            metrics["avg_watch_time"] = val
        elif name == "ig_reels_video_view_total_time":
            metrics["total_watch_time"] = val
        elif name == "plays":
            metrics["views"] = val
    return metrics


def _fetch_facebook(post_id: str) -> dict:
    import os
    import requests

    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    if not token:
        return {}
    resp = requests.get(
        f"https://graph.facebook.com/v21.0/{post_id}/insights",
        params={
            "metric": "post_impressions,post_engaged_users,post_video_views,post_video_avg_time_watched",
            "access_token": token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    metrics: dict[str, Any] = {}
    for item in resp.json().get("data", []):
        name = item.get("name", "")
        vals = item.get("values", [{}])
        val = vals[0].get("value", 0) if vals else 0
        if name == "post_impressions":
            metrics["impressions"] = val
        elif name == "post_engaged_users":
            metrics["engaged_users"] = val
        elif name == "post_video_views":
            metrics["video_views"] = val
        elif name == "post_video_avg_time_watched":
            metrics["avg_watch_time"] = val
    return metrics


def _fetch_x(post_id: str) -> dict:
    import os
    import requests

    bearer = os.getenv("X_BEARER_TOKEN", "").strip()
    if not bearer:
        return {}
    resp = requests.get(
        f"https://api.twitter.com/2/tweets/{post_id}",
        params={"tweet.fields": "public_metrics"},
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=15,
    )
    resp.raise_for_status()
    public = resp.json().get("data", {}).get("public_metrics", {})
    return {
        "impressions": public.get("impression_count", 0),
        "likes": public.get("like_count", 0),
        "retweets": public.get("retweet_count", 0),
        "replies": public.get("reply_count", 0),
    }


def _fetch_tiktok(post_id: str) -> dict:
    """TikTok Content Posting API — video insights."""
    import os
    import requests

    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
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


def _fetch_threads(post_id: str) -> dict:
    """Threads API — media insights."""
    import os
    import requests

    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        return {}
    try:
        resp = requests.get(
            f"https://graph.threads.net/v1.0/{post_id}/insights",
            params={
                "metric": "views,likes,replies,reposts,quotes",
                "access_token": token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        metrics: dict[str, Any] = {}
        for item in resp.json().get("data", []):
            name = item.get("name", "")
            vals = item.get("values", [{}])
            val = vals[0].get("value", 0) if vals else 0
            metrics[name] = val
        return metrics
    except Exception as exc:
        logger.warning("[metric_collector] Threads fetch failed for %s: %s", post_id, exc)
        return {}


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------

@task(name="compute_reward")
def compute_reward(
    metrics: dict[str, Any],
    platform: str,
    shaper: RewardShaper,
) -> float:
    """Compute shaped reward from 48h metrics."""
    return shaper.compute_reward(platform=platform, metrics=metrics)


@task(name="process_pending_task")
def process_pending_task(
    task_record: PendingFeedbackTask,
    store: PendingFeedbackStore,
    shaper: RewardShaper,
    now: datetime | None = None,
    bandit_updater: Optional[BanditUpdater] = None,
) -> bool:
    """Process a single pending task: check window, fetch, update.

    Args:
        task_record: The feedback task to process.
        store: SharePoint store for reading/writing task state.
        shaper: Reward shaper for computing 48h rewards.
        now: Override for current time (testing).
        bandit_updater: Optional callback invoked at the 48h window with
            (niche_id, content_type, platform, reward). Allows niche-specific
            bandit implementations to receive partial_fit updates without
            genlab-core importing them directly.

    Returns True if a window was processed.
    """
    window = store.next_collection_window(task_record, now=now)
    if window is None:
        return False

    metrics = fetch_platform_metrics(
        task_record.platform,
        task_record.platform_post_id,
        window,
    )

    reward_48h: float | None = None
    if window == "48h" and metrics:
        reward_48h = compute_reward(metrics, task_record.platform, shaper)
        logger.info(
            "[metric_collector] 48h reward for %s/%s: %.3f",
            task_record.platform,
            task_record.platform_post_id,
            reward_48h,
        )

        # Update bandit with the 48h reward signal
        if bandit_updater is not None:
            try:
                bandit_updater(
                    task_record.niche_id,
                    task_record.content_type,
                    task_record.platform,
                    reward_48h,
                )
                logger.info(
                    "[metric_collector] bandit updated: niche=%s type=%s platform=%s reward=%.3f",
                    task_record.niche_id,
                    task_record.content_type,
                    task_record.platform,
                    reward_48h,
                )
            except Exception as exc:
                logger.warning(
                    "[metric_collector] bandit update failed for %s/%s: %s",
                    task_record.platform,
                    task_record.platform_post_id,
                    exc,
                )

    store.update_window(task_record, window, reward_48h=reward_48h)
    return True


@flow(name="collect_metrics")
def collect_metrics(
    niche_id: str | None = None,
    backlog_client: Any = None,
    bandit_updater: Optional[BanditUpdater] = None,
) -> int:
    """Main Prefect flow: collect metrics for all pending feedback tasks.

    Args:
        niche_id: Optional filter to process only tasks for a specific niche.
        backlog_client: BacklogClient instance. If None, creates one from env.
        bandit_updater: Optional callback for bandit partial_fit at 48h window.
            Signature: (niche_id, content_type, platform, reward) -> None.

    Returns:
        Number of tasks processed.
    """
    if backlog_client is None:
        try:
            from genlab_core.http.backlog_client import BacklogClient
            backlog_client = BacklogClient()
        except Exception as exc:
            logger.error("[metric_collector] Failed to create BacklogClient: %s", exc)
            return 0

    store = PendingFeedbackStore(backlog_client)
    shaper = RewardShaper()

    pending = store.get_pending(niche_id=niche_id)
    if not pending:
        logger.info("[metric_collector] No pending tasks")
        return 0

    logger.info("[metric_collector] Processing %d pending tasks", len(pending))
    processed = 0
    now = datetime.now(timezone.utc)

    for task_record in pending:
        try:
            if process_pending_task(
                task_record, store, shaper, now=now, bandit_updater=bandit_updater,
            ):
                processed += 1
        except Exception as exc:
            logger.warning(
                "[metric_collector] Failed to process %s/%s: %s",
                task_record.platform,
                task_record.platform_post_id,
                exc,
            )

    logger.info("[metric_collector] Processed %d / %d tasks", processed, len(pending))
    return processed


if __name__ == "__main__":
    collect_metrics()
