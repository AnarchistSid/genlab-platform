"""Collect post-publish metrics at timed windows.

Reads pending feedback tasks from PendingFeedbackStore, checks which
collection windows are due, fetches platform metrics, and updates the
store. At the 48h window, computes a shaped reward via RewardShaper.

Run standalone:
    python -m genlab_core.learning.metric_collector
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# Callback type: (niche_id, content_type, platform, reward, bandit_context) -> None
BanditUpdater = Callable[[str, str, str, float, dict | None], None]

logger = logging.getLogger(__name__)

def flow(fn=None, **kwargs):  # type: ignore[misc]
    return fn if fn else lambda f: f

def task(fn=None, **kwargs):  # type: ignore[misc]
    return fn if fn else lambda f: f

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
    niche_id: str = "",
) -> dict[str, Any]:
    """Fetch metrics for a single post from its platform API.

    Uses per-niche credentials via niche_credentials to avoid cross-channel
    token leakage (e.g. fetching CriticalRush metrics with BB tokens).
    """
    # Strip platform prefix from composite IDs (e.g., "instagram:123" → "123")
    raw_id = post_id.split(":", 1)[1] if ":" in post_id else post_id

    # Instagram Reels: use specialised 6h fetcher for early skip-rate signal
    if platform == "instagram" and window == "6h":
        try:
            return _fetch_instagram_reels_6h(raw_id, niche_id=niche_id)
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
        return fn(raw_id, niche_id=niche_id)
    except Exception as exc:
        logger.warning("[metric_collector] %s fetch failed for %s: %s", platform, post_id, exc)
        return {}


# Module-level YouTube token cache (avoids re-refreshing on every metric call)
_yt_token_cache: dict[str, Any] = {"token": "", "niche": "", "ts": 0.0}
_YT_TOKEN_TTL = 2400.0  # 40 minutes (tokens last ~60 min)

# Per-niche YouTube channel ID cache — channels never change for a given OAuth
# token, so we cache forever within the process lifetime.
_yt_channel_cache: dict[str, str] = {}


def _get_yt_channel_id(access_token: str, niche_id: str) -> str:
    """Fetch and cache the channel ID owned by this OAuth token."""
    import requests

    cached = _yt_channel_cache.get(niche_id, "")
    if cached:
        return cached
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "id", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            channel_id = items[0].get("id", "")
            if channel_id:
                _yt_channel_cache[niche_id] = channel_id
            return channel_id
    except Exception as exc:
        logger.debug("[metric_collector] yt channel-id fetch failed for %s: %s", niche_id, exc)
    return ""


def _fetch_youtube_analytics_extras(
    access_token: str, video_id: str, channel_id: str,
) -> dict:
    """Pull avg_view_duration + subscribers_gained from YouTube Analytics v2.

    Returns a dict with keys ``avg_view_duration``, ``subscriber_gained``,
    ``minutes_viewed``, ``shares``.  Empty dict if data not propagated yet
    (the API has a 24-48h aggregation lag) or any error.

    Uses a wide 90-day startDate window since the ``video==`` filter scopes
    the result to a single video.
    """
    import requests
    from datetime import timedelta

    if not channel_id:
        return {}

    now = datetime.now(UTC)
    start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            params={
                "ids": f"channel=={channel_id}",
                "startDate": start_date,
                "endDate": end_date,
                "metrics": (
                    "estimatedMinutesWatched,averageViewDuration,"
                    "subscribersGained,shares"
                ),
                "filters": f"video=={video_id}",
                "dimensions": "video",
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        # 403 = scope missing; 400 = video too new; treat as soft fail.
        if resp.status_code in (400, 403):
            logger.debug(
                "[metric_collector] yt analytics soft-fail %d for %s",
                resp.status_code, video_id,
            )
            return {}
        resp.raise_for_status()
        body = resp.json()
        rows = body.get("rows", [])
        if not rows:
            return {}
        headers_meta = [col.get("name") for col in body.get("columnHeaders", [])]
        data = dict(zip(headers_meta, rows[0]))
        return {
            "minutes_viewed": float(data.get("estimatedMinutesWatched", 0)),
            "avg_view_duration": float(data.get("averageViewDuration", 0)),
            "subscriber_gained": int(data.get("subscribersGained", 0)),
            "shares": int(data.get("shares", 0)),
        }
    except Exception as exc:
        logger.debug("[metric_collector] yt analytics fetch failed for %s: %s", video_id, exc)
        return {}


def _fetch_youtube(post_id: str, niche_id: str = "") -> dict:
    """YouTube Data API v3 snapshot + Analytics v2 aggregates.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["youtube"]``:
    ``views, avg_view_duration, subscriber_gained, like_rate, comment_rate``.

    Data API gives the always-current viewCount/likeCount/commentCount.
    Analytics API fills in avg_view_duration + subscriber_gained from
    aggregated reports.  If Analytics hasn't propagated yet (typical at
    the 6h/24h windows), those keys stay at 0 and the engagement bandit
    learns from the snapshot signals only.
    """
    import time as _time

    import requests

    from genlab_core.publishing.niche_credentials import resolve_youtube_credentials

    creds = resolve_youtube_credentials(niche_id)
    client_id = creds.get("client_id", "")
    client_secret = creds.get("client_secret", "")
    refresh_token = creds.get("refresh_token", "")
    if not all([client_id, client_secret, refresh_token]):
        return {}

    # Reuse cached token if same niche and not expired
    now = _time.monotonic()
    if (
        _yt_token_cache["token"]
        and _yt_token_cache["niche"] == niche_id
        and (now - _yt_token_cache["ts"]) < _YT_TOKEN_TTL
    ):
        access_token = _yt_token_cache["token"]
    else:
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
        _yt_token_cache["token"] = access_token
        _yt_token_cache["niche"] = niche_id
        _yt_token_cache["ts"] = now

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
    views = int(stats.get("viewCount", 0))
    likes = int(stats.get("likeCount", 0))
    comments = int(stats.get("commentCount", 0))
    like_rate = (likes / views) if views > 0 else 0.0
    comment_rate = (comments / views) if views > 0 else 0.0

    result: dict[str, Any] = {
        "views": views,
        "likes": likes,
        "comments": comments,
        "like_rate": round(like_rate, 4),
        "comment_rate": round(comment_rate, 4),
        # Stubs — will be overwritten by Analytics call below when data has
        # propagated (typically ≥48h post-publish).
        "avg_view_duration": 0.0,
        "subscriber_gained": 0,
    }

    # Layer Analytics extras on top.  Empty dict on early-window calls or
    # scope/API failures — keeps the stub zeros, never crashes the fetch.
    channel_id = _get_yt_channel_id(access_token, niche_id)
    extras = _fetch_youtube_analytics_extras(access_token, post_id, channel_id)
    if extras:
        result["avg_view_duration"] = round(extras.get("avg_view_duration", 0.0), 2)
        result["subscriber_gained"] = extras.get("subscriber_gained", 0)
        result["minutes_viewed"] = extras.get("minutes_viewed", 0.0)
        result["shares"] = extras.get("shares", 0)

    return result


def _fetch_instagram(post_id: str, niche_id: str = "") -> dict:
    """Fetch Instagram metrics — tries Reels metrics first, falls back to standard.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["instagram"]``:
    ``views, saves, dm_send_rate, shares, skip_rate``.
    Graph API ``saved`` is returned as ``saves``. ``dm_send_rate`` and
    ``skip_rate`` aren't directly available from the basic insights
    endpoints; stubbed as 0.
    """
    import requests

    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    token = resolve_meta_credentials(niche_id).get("ig_access_token", "")
    if not token:
        return {}

    # Try Reels-compatible metrics first (Break 3 fix)
    for metric_set in [
        "plays,reach,likes,comments,shares,saved",
        "reach,saved,comments,shares,likes",  # without 'plays' (some posts reject it)
        "impressions,reach",  # minimal fallback
    ]:
        try:
            resp = requests.get(
                f"https://graph.facebook.com/v21.0/{post_id}/insights",
                params={"metric": metric_set, "access_token": token},
                timeout=15,
            )
            if resp.status_code == 400:
                continue  # try next metric set
            resp.raise_for_status()
            metrics: dict[str, Any] = {}
            for item in resp.json().get("data", []):
                name = item.get("name", "")
                vals = item.get("values", [{}])
                val = vals[0].get("value", 0) if vals else 0
                if name == "plays":
                    metrics["views"] = val
                elif name == "impressions":
                    metrics.setdefault("views", val)
                elif name == "saved":
                    metrics["saves"] = val
                elif name in ("reach", "likes", "comments", "shares"):
                    metrics[name] = val
            # Stubs for fields RewardShaper expects but Graph API doesn't expose
            metrics.setdefault("dm_send_rate", 0.0)
            metrics.setdefault("skip_rate", 0.0)
            return metrics
        except Exception:
            continue

    logger.warning("[metric_collector] All IG metric sets failed for %s", post_id)
    return {}


def _fetch_instagram_reels_6h(post_id: str, niche_id: str = "") -> dict:
    """IG Reels-specific metrics for early 6h skip-rate signal."""
    import requests

    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    token = resolve_meta_credentials(niche_id).get("ig_access_token", "")
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


def _fetch_facebook(post_id: str, niche_id: str = "") -> dict:
    """Fetch Facebook post insights.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["facebook"]``:
    ``minutes_viewed, shares, completion_rate, reach``.
    ``shares`` and ``completion_rate`` require additional Graph API calls
    (post object + video duration) — stubbed as 0 for now and tracked
    as a follow-up.
    """
    import requests

    from genlab_core.publishing.niche_credentials import resolve_fb_credentials

    token, _page_id = resolve_fb_credentials(niche_id)
    if not token:
        return {}
    resp = requests.get(
        f"https://graph.facebook.com/v21.0/{post_id}/insights",
        params={
            "metric": (
                "post_impressions,post_impressions_unique,"
                "post_engaged_users,post_video_views,"
                "post_video_avg_time_watched"
            ),
            "access_token": token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    metrics: dict[str, Any] = {}
    impressions = 0
    reach = 0
    video_views = 0
    avg_watch_time = 0.0
    for item in resp.json().get("data", []):
        name = item.get("name", "")
        vals = item.get("values", [{}])
        val = vals[0].get("value", 0) if vals else 0
        if name == "post_impressions":
            impressions = int(val)
        elif name == "post_impressions_unique":
            reach = int(val)
        elif name == "post_engaged_users":
            metrics["engaged_users"] = val
        elif name == "post_video_views":
            video_views = int(val)
            metrics["video_views"] = val
        elif name == "post_video_avg_time_watched":
            avg_watch_time = float(val)
            metrics["avg_watch_time"] = val

    metrics["impressions"] = impressions
    metrics["reach"] = reach or impressions  # fall back to impressions if reach unavailable
    # avg_watch_time is in milliseconds from Graph API; convert to minutes
    metrics["minutes_viewed"] = round((video_views * avg_watch_time) / 60_000.0, 2)
    # Stubs — need post object (shares) + video duration (completion_rate)
    metrics.setdefault("shares", 0)
    metrics.setdefault("completion_rate", 0.0)
    return metrics


def _fetch_x(post_id: str, niche_id: str = "") -> dict:
    """Fetch X/Twitter metrics via API v2.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["twitter"]``:
    ``impressions, reply_chain_rate, engagements, profile_clicks``.
    Raw ``likes/retweets/replies`` are also returned for compatibility
    with ``upsert_analytics`` storage. ``profile_clicks`` requires the
    organic_tweet metrics endpoint (premium-only) — stubbed as 0.
    """
    import os

    import requests

    bearer = os.getenv("X_BEARER_TOKEN", "").strip()  # X bearer is app-wide, no per-niche
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
    impressions = int(public.get("impression_count", 0))
    likes = int(public.get("like_count", 0))
    retweets = int(public.get("retweet_count", 0))
    replies = int(public.get("reply_count", 0))
    engagements = likes + retweets + replies
    reply_chain_rate = (replies / impressions) if impressions > 0 else 0.0
    return {
        "impressions": impressions,
        "likes": likes,
        "retweets": retweets,
        "replies": replies,
        "engagements": engagements,
        "reply_chain_rate": round(reply_chain_rate, 4),
        "profile_clicks": 0,  # not in public_metrics; needs organic_tweet metrics
    }


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


def _fetch_threads(post_id: str, niche_id: str = "") -> dict:
    """Threads API — media insights.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["threads"]``:
    ``views, replies, reposts, discovery_share``. ``discovery_share``
    isn't exposed by the Threads insights endpoint — stubbed as 0.
    """
    import requests

    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    token, _user_id = resolve_threads_credentials(niche_id)
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
        # discovery_share not exposed by Threads API
        metrics.setdefault("discovery_share", 0.0)
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


# Map platform → variant arm_id prefix used in cta_variants.yaml.
# Only platforms with configured CTA variants appear here.
_CTA_PLATFORM_PREFIX: dict[str, str] = {
    "instagram": "ig_",
    "youtube": "yt_",
    "facebook": "fb_",
}


def _match_variant_for_platform(variant_field: str, platform: str) -> str | None:
    """Pick the variant arm_id that belongs to ``platform``.

    ``variant_field`` is the comma-separated string stored at publish time in
    blueprints.affiliate_cta_variant — e.g. "ig_link_in_bio,yt_get_here,fb_check_out".
    """
    prefix = _CTA_PLATFORM_PREFIX.get(platform)
    if not prefix:
        return None
    for raw in variant_field.split(","):
        arm = raw.strip()
        if arm.startswith(prefix):
            return arm
    return None


def _update_cta_bandit_from_clicks(
    task_record: PendingFeedbackTask,
    backlog_client: Any,
) -> None:
    """Update CTA bandit posterior using observed affiliate clicks.

    At 48h, the published blueprint already stored which CTA variant arm_id
    was selected per platform.  We look up that arm_id, count clicks in
    ``affiliate_clicks`` for (blueprint_id, platform_source), and feed the
    boolean signal to the CTA bandit.  Zero-click at 48h is treated as a
    failure (β += 1.0) so the bandit can learn dud variants.

    No-ops when:
      * platform has no CTA variants (twitter, threads, tiktok)
      * blueprint has no affiliate_cta_variant (no affiliate matched)
      * backlog_client doesn't expose Postgres find() (Azure/SharePoint mode)
    """
    if task_record.platform not in _CTA_PLATFORM_PREFIX:
        return

    from genlab_core.monetization.cta_engine import get_bandit

    bandit = get_bandit()
    if bandit is None:
        return

    find = getattr(backlog_client, "find", None) if backlog_client else None
    if find is None:
        # SharePoint-mode backlog_client has no find(); skip rather than crash.
        return

    bp_rows = find(
        "blueprints",
        formula=f"{{task_id}} = '{task_record.content_id}'",
        niche_id=task_record.niche_id,
        max_records=1,
        columns=["affiliate_cta_variant"],
    )
    if not bp_rows:
        return

    bp_fields = bp_rows[0].get("fields", bp_rows[0]) or {}
    variant_field = (bp_fields.get("affiliate_cta_variant") or "").strip()
    if not variant_field:
        return

    arm_id = _match_variant_for_platform(variant_field, task_record.platform)
    if not arm_id:
        return

    click_rows = find(
        "affiliate_clicks",
        formula=(
            f"AND({{blueprint_id}} = '{task_record.content_id}', "
            f"{{platform_source}} = '{task_record.platform}')"
        ),
        niche_id=task_record.niche_id,
        max_records=100,
    )
    click_count = len(click_rows)
    clicked = click_count > 0

    bandit.update(arm_id, task_record.platform, clicked)
    logger.info(
        "[metric_collector] CTA bandit updated: niche=%s platform=%s arm=%s clicks=%d clicked=%s",
        task_record.niche_id,
        task_record.platform,
        arm_id,
        click_count,
        clicked,
    )


@task(name="process_pending_task")
def process_pending_task(
    task_record: PendingFeedbackTask,
    store: PendingFeedbackStore,
    shaper: RewardShaper,
    now: datetime | None = None,
    bandit_updater: BanditUpdater | None = None,
    backlog_client: Any = None,
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
        backlog_client: BacklogClient for writing metrics to the Analytics table.

    Returns True if a window was processed.
    """
    window = store.next_collection_window(task_record, now=now)
    if window is None:
        return False

    metrics = fetch_platform_metrics(
        task_record.platform,
        task_record.platform_post_id,
        window,
        niche_id=task_record.niche_id,
    )

    # Record lifecycle snapshot for content decay analysis
    if metrics:
        try:
            from genlab_core.intelligence.lifecycle_tracker import record_lifecycle_snapshot
            record_lifecycle_snapshot(
                post_id=task_record.platform_post_id,
                platform=task_record.platform,
                niche_id=task_record.niche_id,
                window=window,
                metrics=metrics,
            )
        except Exception as exc:
            logger.debug("[metric_collector] lifecycle snapshot failed: %s", exc)

    # Early-stop detection at 6h window (Break 14 fix)
    # If 6h views are far below niche floor, the post is bombing — skip
    # collection of later windows.  We do NOT update the bandit here:
    # the 48h reward path is the single source of bandit truth, so a
    # bombing post will naturally produce a near-zero reward there.
    # Sending 0.05 here previously hit the adaptive-threshold floor and
    # incremented α (Bug F in 2026-05-16 audit) — the opposite of intent.
    if window == "6h" and metrics:
        views_6h = metrics.get("views", 0)
        _NICHE_6H_FLOOR: dict[str, int] = {
            "ai_creators": 20,
            "gaming": 30,
            "sports": 25,
            "movies": 20,
            "anime": 15,
        }
        floor = _NICHE_6H_FLOOR.get(task_record.niche_id, 20)
        if 0 < views_6h < floor:
            task_record.early_stop = True
            logger.info(
                "[metric_collector] EARLY STOP: %s/%s 6h views=%d < floor=%d",
                task_record.platform,
                task_record.platform_post_id,
                views_6h,
                floor,
            )
            # Mark task as early-stopped — skips 24h/48h/168h collection
            task_record.collection_status = "early_stopped"
            task_record.reward_48h = 0.0
            store.update_window(task_record, window, reward_48h=0.0)
            return True

    reward_48h: float | None = None
    if window == "48h" and metrics:
        reward_48h = compute_reward(metrics, task_record.platform, shaper)
        logger.info(
            "[metric_collector] 48h reward for %s/%s: %.3f",
            task_record.platform,
            task_record.platform_post_id,
            reward_48h,
        )

        # Update content bandit with the 48h reward signal
        if bandit_updater is not None:
            try:
                bandit_updater(
                    task_record.niche_id,
                    task_record.content_type,
                    task_record.platform,
                    reward_48h,
                    task_record.bandit_context,
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

        # Update CTA bandit using click attribution (NOT engagement reward).
        # Engagement reward was the wrong signal: same shape of bug as Bug F
        # (always-truthy float cast to clicked: bool).  Real signal lives in
        # the affiliate_clicks table, keyed by blueprint_id + platform_source.
        try:
            _update_cta_bandit_from_clicks(task_record, backlog_client)
        except Exception as exc:
            logger.warning(
                "[metric_collector] CTA bandit update failed (degraded): %s",
                exc,
            )

    # Write fetched metrics to the Analytics table for dashboard consumption
    if metrics and backlog_client is not None:
        try:
            backlog_client.upsert_analytics(
                post_id=task_record.platform_post_id,
                platform=task_record.platform,
                insights=metrics,
                published_at=task_record.published_at.isoformat(),
                fetch_window=window,
                niche_id=task_record.niche_id,
            )
        except Exception as exc:
            logger.debug(
                "[metric_collector] Analytics upsert failed for %s/%s: %s",
                task_record.platform, task_record.platform_post_id, exc,
            )

    store.update_window(task_record, window, reward_48h=reward_48h)
    return True


@flow(name="collect_metrics")
def collect_metrics(
    niche_id: str | None = None,
    backlog_client: Any = None,
    bandit_updater: BanditUpdater | None = None,
) -> int:
    """Collect metrics for all pending feedback tasks.

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
    now = datetime.now(UTC)

    for task_record in pending:
        try:
            if process_pending_task(
                task_record, store, shaper, now=now,
                bandit_updater=bandit_updater,
                backlog_client=backlog_client,
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

    # Health check: warn if no tasks have been processed and pending list is large
    if processed == 0 and len(pending) > 10:
        logger.warning(
            "[metric_collector] HEALTH CHECK: 0/%d tasks processed — "
            "learning loop may be stalled. Check publish_time values "
            "and next_collection_window eligibility.",
            len(pending),
        )

    return processed


def _default_bandit_updater(
    niche_id: str,
    content_type: str,
    platform: str,
    reward: float,
    bandit_context: dict | None = None,
) -> None:
    """Default bandit updater — writes reward into bandit_arms table.

    Math (2026-05-16 audit fix):
      * Fractional Thompson update preserves signal magnitude:
            alpha += clip(reward, 0, 1)
            beta  += 1 - clip(reward, 0, 1)
      * n_plays is incremented per observation.

    Multi-arm credit (2026-05-17 closure):
      The primary arm is ``content_type``. Additional arms listed in
      ``bandit_context["extra_arms"]`` get the SAME reward applied —
      this is how the hook-style consumer (style:{niche}:{name})
      receives feedback. LinUCB context is only applied to the
      primary arm because the 12-dim feature vector is content-shape
      specific, not style-shape specific.

    Idempotency:
      The pending_feedback state machine in process_pending_task
      guarantees a single bandit_updater fire per (task_id, window).
      The audit-removed PerformanceLearner parallel update path is
      not coming back.
    """
    try:
        import json as _json

        import numpy as np

        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import save_arm
        from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm

        client = BacklogClient()
        proxy = client.bandit_arms
        if proxy is None:
            logger.warning("[bandit_updater] No bandit_arms proxy")
            return

        reward_clipped = max(0.0, min(1.0, float(reward)))

        # Build the target set: primary arm (content_type) plus any
        # extra arms the publisher recorded for this task.
        target_arms: set[str] = {content_type}
        if bandit_context:
            extra = bandit_context.get("extra_arms", [])
            if isinstance(extra, list):
                target_arms.update(a for a in extra if isinstance(a, str) and a)

        # Pre-load linucb context once (shared across primary update).
        linucb_ctx_array: np.ndarray | None = None
        if bandit_context and "linucb_context" in bandit_context:
            try:
                ctx_list = bandit_context["linucb_context"]
                if len(ctx_list) == CONTEXT_DIM:
                    linucb_ctx_array = np.array(ctx_list, dtype=np.float64)
            except Exception:
                linucb_ctx_array = None

        existing = proxy.all()
        updated: list[str] = []
        for item in existing:
            fields = item.get("fields", item)
            item_arm = fields.get("arm_id", "") or fields.get("Title", "")
            item_niche = fields.get("niche_id", "")
            if item_niche != niche_id or item_arm not in target_arms:
                continue
            if item_arm in updated:
                continue  # Defensive: skip if the proxy returns duplicates.

            alpha = float(fields.get("alpha", 1.0) or 1.0)
            beta = float(fields.get("beta", 1.0) or 1.0)
            n_plays = int(fields.get("n_plays", 0) or 0)

            alpha += reward_clipped
            beta += 1.0 - reward_clipped
            n_plays += 1

            # LinUCB lives only on the primary content_type arm. The
            # 12-dim feature vector encodes content properties, not
            # style; mixing it into the style arm's posterior would
            # learn a confounded model.
            linucb_state_dict = None
            if item_arm == content_type and linucb_ctx_array is not None:
                try:
                    raw_state = (
                        fields.get("linucb_state")
                        or fields.get("LinUCB_State")
                        or ""
                    )
                    if raw_state:
                        arm = LinUCBArm.from_dict(_json.loads(raw_state))
                    else:
                        arm = LinUCBArm(d=CONTEXT_DIM)
                    arm.update(linucb_ctx_array, reward_clipped)
                    linucb_state_dict = arm.to_dict()
                    logger.info(
                        "[bandit_updater] LinUCB updated: %s/%s n_obs=%d",
                        niche_id, item_arm, arm.n_obs,
                    )
                except Exception as linucb_exc:
                    logger.warning(
                        "[bandit_updater] LinUCB update failed for %s/%s "
                        "(falling back to Thompson): %s",
                        niche_id, item_arm, linucb_exc,
                    )

            save_arm(
                proxy,
                arm_id=item_arm,
                alpha=alpha,
                beta=beta,
                linucb_state=linucb_state_dict,
                n_plays=n_plays,
            )
            updated.append(item_arm)
            logger.info(
                "[bandit_updater] %s/%s reward=%.3f → a=%.2f b=%.2f n_plays=%d",
                niche_id, item_arm, reward_clipped, alpha, beta, n_plays,
            )

        # Sanity log: if we asked for N arms but updated fewer, surface
        # the gap. Common cause: the arm doesn't exist in bandit_arms
        # (e.g. style not yet seeded for this niche).
        missing = target_arms - set(updated)
        if missing:
            logger.warning(
                "[bandit_updater] %d arm(s) requested but not found in "
                "bandit_arms (niche=%s): %s",
                len(missing), niche_id, sorted(missing),
            )
    except Exception as exc:
        logger.warning("[bandit_updater] Failed: %s", exc)


if __name__ == "__main__":
    collect_metrics(bandit_updater=_default_bandit_updater)
