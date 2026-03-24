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


def _fetch_youtube(post_id: str, niche_id: str = "") -> dict:
    """YouTube Data API v3 basic stats (per-niche credentials)."""
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
    return {
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
    }


def _fetch_instagram(post_id: str, niche_id: str = "") -> dict:
    """Fetch Instagram metrics — tries Reels metrics first, falls back to standard."""
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
                elif name in ("reach", "likes", "comments", "shares", "saved"):
                    metrics[name] = val
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
    import requests

    from genlab_core.publishing.niche_credentials import resolve_fb_credentials

    token, _page_id = resolve_fb_credentials(niche_id)
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


def _fetch_x(post_id: str, niche_id: str = "") -> dict:
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
    return {
        "impressions": public.get("impression_count", 0),
        "likes": public.get("like_count", 0),
        "retweets": public.get("retweet_count", 0),
        "replies": public.get("reply_count", 0),
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
    """Threads API — media insights."""
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

    # Early-stop detection at 6h window (Break 14 fix)
    # If 6h views are far below niche floor, the post is bombing — skip to
    # negative reward immediately instead of waiting 48h for the inevitable.
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
            # Give immediate negative reward to bandit so it learns fast
            early_reward = 0.05  # very low but non-zero
            if bandit_updater is not None:
                try:
                    bandit_updater(
                        task_record.niche_id,
                        task_record.content_type,
                        task_record.platform,
                        early_reward,
                        task_record.bandit_context,
                    )
                    logger.info(
                        "[metric_collector] early-stop bandit penalty: %s/%s reward=%.3f",
                        task_record.niche_id, task_record.content_type, early_reward,
                    )
                except Exception as exc:
                    logger.debug("[metric_collector] early-stop bandit update failed: %s", exc)
            # Mark task as early-stopped — skips 24h/48h/168h collection
            task_record.collection_status = "early_stopped"
            task_record.reward_48h = early_reward
            store.update_window(task_record, window, reward_48h=early_reward)
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

        # Update CTA bandit with engagement reward (Break 10 fix)
        try:
            from genlab_core.monetization.cta_engine import get_bandit
            cta_bandit = get_bandit()
            if cta_bandit is not None:
                cta_bandit.update(task_record.platform, reward_48h)
                logger.debug("[metric_collector] CTA bandit updated: platform=%s reward=%.3f",
                             task_record.platform, reward_48h)
        except Exception as exc:
            logger.debug("[metric_collector] CTA bandit update skipped: %s", exc)

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
    """Default bandit updater — writes reward directly to bandit_arms table.

    This closes the critical gap: metric_collector computes rewards but they
    were never fed back to bandit_arms because collect_metrics() was called
    from CLI without a bandit_updater callback (Break 8 fix).

    Uses adaptive threshold instead of hardcoded 0.5 (Break 6 fix).
    Also updates LinUCB arm with context vector when available (Break 11 fix).
    """
    try:
        import json as _json

        import numpy as np

        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms_extended, save_arm
        from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm

        client = BacklogClient()
        proxy = client.bandit_arms
        if proxy is None:
            logger.warning("[bandit_updater] No bandit_arms proxy")
            return

        existing = proxy.all()
        for item in existing:
            fields = item.get("fields", item)
            item_arm = fields.get("arm_id", "") or fields.get("Title", "")
            item_niche = fields.get("niche_id", "")
            if item_niche != niche_id or item_arm != content_type:
                continue

            alpha = float(fields.get("alpha", 1.0) or 1.0)
            beta = float(fields.get("beta", 1.0) or 1.0)

            # Adaptive threshold: use running mean instead of hardcoded 0.5
            mean = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5
            threshold = max(0.05, mean * 0.8)

            if reward >= threshold:
                alpha += 1.0
            else:
                beta += 0.5  # softer penalty

            # Update LinUCB arm with context vector (Break 11 fix)
            linucb_state_dict = None
            if bandit_context and "linucb_context" in bandit_context:
                try:
                    ctx_list = bandit_context["linucb_context"]
                    if len(ctx_list) == CONTEXT_DIM:
                        ctx = np.array(ctx_list, dtype=np.float64)
                        # Load or create LinUCB arm
                        raw_state = fields.get("linucb_state") or fields.get("LinUCB_State") or ""
                        if raw_state:
                            arm = LinUCBArm.from_dict(_json.loads(raw_state))
                        else:
                            arm = LinUCBArm(d=CONTEXT_DIM)
                        arm.update(ctx, reward)
                        linucb_state_dict = arm.to_dict()
                        logger.info(
                            "[bandit_updater] LinUCB updated: %s/%s n_obs=%d",
                            niche_id, item_arm, arm.n_obs,
                        )
                except Exception as linucb_exc:
                    logger.debug("[bandit_updater] LinUCB update skipped: %s", linucb_exc)

            save_arm(proxy, arm_id=item_arm, alpha=alpha, beta=beta,
                     linucb_state=linucb_state_dict)
            logger.info(
                "[bandit_updater] %s/%s reward=%.3f thr=%.3f → a=%.1f b=%.1f",
                niche_id, item_arm, reward, threshold, alpha, beta,
            )
            return
    except Exception as exc:
        logger.warning("[bandit_updater] Failed: %s", exc)


if __name__ == "__main__":
    collect_metrics(bandit_updater=_default_bandit_updater)
