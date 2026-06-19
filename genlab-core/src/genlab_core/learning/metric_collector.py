"""Collect post-publish metrics at timed windows.

Reads pending feedback tasks from PendingFeedbackStore, checks which
collection windows are due, fetches platform metrics, and updates the
store. At the 48h window, computes a shaped reward via RewardShaper.

Run standalone:
    python -m genlab_core.learning.metric_collector

DESIGN NOTE — per-platform fetchers below intentionally do NOT delegate
to :mod:`genlab_core.platforms.metrics`. They are reward-shape
specialisations, not duplicates:

* ``_fetch_youtube`` uses OAuth refresh-token auth (vs the canonical's
  Data API key), caches the access token, computes ``like_rate`` and
  ``comment_rate``, and layers Analytics v2 extras
  (``avg_view_duration``, ``subscriber_gained``, ``minutes_viewed``).
* ``_fetch_instagram`` cascades three metric sets (Reels-first, then
  standard, then minimal) and deliberately OMITS unobservable fields so
  ``RewardShaper.compute_reward`` redistributes their weight rather than
  pinning to a fake zero.
* ``_fetch_x`` computes ``reply_chain_rate`` and aligns the return shape
  to ``RewardShaper.BASE_WEIGHTS["twitter"]``.
* ``_fetch_facebook_reel_insights`` + ``_fetch_facebook_video_object``
  pair to handle FB's two surfaces (Reels vs crossposted videos).

Substituting any of these with the canonical basic fetchers would
silently degrade the bandit reward signal. The pipeline-stage
``FetchInsights`` (basic snapshot path) DOES delegate to the canonical
in PR #69 — that's the correct migration target.
"""

from __future__ import annotations

import logging
import os
import threading
import time
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


# Internal imports must come *after* the prefect-stub decorators above so that
# any module that re-exports flow/task picks up these no-op fallbacks when
# Prefect is not installed.
from genlab_core.intelligence.lifecycle_tracker import record_lifecycle_snapshot  # noqa: E402
from genlab_core.learning.pending_feedback_store import PendingFeedbackStore  # noqa: E402
from genlab_core.learning.pending_feedback_task import (  # noqa: E402
    CollectionWindow,
    PendingFeedbackTask,
)
from genlab_core.learning.reward_shaper import RewardShaper  # noqa: E402

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
            logger.warning(
                "[metric_collector] instagram reels 6h fetch failed for %s: %s", post_id, exc
            )
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


# P5a (2026-06-19): YouTube fetcher + module-level state moved to
# learning/metrics/youtube.py. Re-exported below so existing imports
# (tests, scripts) keep working unchanged. See learning/metrics/__init__.py
# for the phased rollout plan.
# P5a phase 2 (2026-06-19): IG fetchers moved to learning/metrics/instagram.py.
# Re-exported below so existing imports (tests, scripts) keep working unchanged.
# P5a phase 3 (2026-06-19): FB fetchers moved to learning/metrics/facebook.py.
# Re-exported below so existing imports (tests, scripts) keep working unchanged.
from genlab_core.learning.metrics.facebook import (  # noqa: E402, F401
    _fetch_facebook,
    _fetch_facebook_reel_insights,
    _fetch_facebook_video_object,
)
from genlab_core.learning.metrics.instagram import (  # noqa: E402, F401
    _fetch_instagram,
    _fetch_instagram_reels_6h,
)
from genlab_core.learning.metrics.youtube import (  # noqa: E402, F401
    _YT_TOKEN_TTL,
    _fetch_youtube,
    _fetch_youtube_analytics_extras,
    _get_yt_analytics_access_token,
    _yt_analytics_token_cache,
    _yt_token_cache,
)


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
        # profile_clicks is in organic_tweet_metrics which requires
        # Twitter API Pro tier. Omit the key entirely so compute_reward
        # redistributes its 0.10 weight instead of pinning to a fake 0.
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
        # discovery_share isn't exposed by the Threads API; omit the key
        # so compute_reward redistributes its 0.15 weight to observed
        # metrics (views / replies / reposts) rather than treating it
        # as a real zero contribution.
        return metrics
    except Exception as exc:
        logger.warning("[metric_collector] Threads fetch failed for %s: %s", post_id, exc)
        return {}


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------


# ── monetisationprogress pool + TTL cache (audit P-1) ───────────────────────
#
# Was: ``psycopg.connect(db_url, connect_timeout=3)`` per get_channel_metrics
# call, which RewardShaper invokes once per (niche, platform) per reward
# computation. A 48h-window batch of 30 tasks × 5 platforms = 150 fresh
# connections per run; on a slow-Postgres day each connect can hit the 3s
# timeout, putting worst-case latency at 7.5 min of pure handshake.
#
# Now: module-level psycopg_pool.ConnectionPool (max 4 connections, shared
# across the whole metric_collector flow + RewardShaper consumers) plus a
# 60-second TTL cache. monetisationprogress rows don't change inside a
# reward batch, so a single cron tick of staleness is acceptable.
_PG_POOL: Any = None  # None = uninit; False = tried + unavailable; else ConnectionPool
_PG_POOL_LOCK = threading.Lock()
_CHANNEL_METRICS_CACHE: dict[tuple[str, str], tuple[float, dict[str, float]]] = {}
_CHANNEL_METRICS_TTL_SEC: float = 60.0


def _get_pg_pool() -> Any:
    """Lazy-init a module-level Postgres ConnectionPool. Returns ``None``
    when ``DATABASE_URL`` is unset or pool creation failed — the caller
    falls back to ``{}`` so SharePoint-only legacy mode keeps working."""
    global _PG_POOL
    if _PG_POOL is not None:
        return _PG_POOL if _PG_POOL is not False else None
    with _PG_POOL_LOCK:
        if _PG_POOL is not None:
            return _PG_POOL if _PG_POOL is not False else None
        db_url = os.environ.get("DATABASE_URL", "").strip()
        if not db_url:
            _PG_POOL = False
            return None
        try:
            from psycopg_pool import ConnectionPool

            _PG_POOL = ConnectionPool(db_url, min_size=1, max_size=4, open=True)
        except Exception as exc:
            logger.warning("[metric_collector] Postgres pool init failed (P-1): %s", exc)
            _PG_POOL = False
            return None
    return _PG_POOL


def _invalidate_channel_metrics_cache_for_tests() -> None:
    """Test hook: clear the TTL cache between tests so cache hits don't
    leak between unrelated cases."""
    _CHANNEL_METRICS_CACHE.clear()


def get_channel_metrics(niche_id: str, platform: str) -> dict[str, float]:
    """Return channel-level metrics from the monetisationprogress table.

    Maps each row's ``metric_name`` to its ``current_value`` for the
    given (niche_id, platform). RewardShaper uses this to detect when
    a channel is within 20% of a monetisation threshold and boost the
    relevant per-post reward metric accordingly.

    Returns ``{}`` on any error — RewardShaper falls back to base
    weights so the bandit keeps learning during outages.

    Pools connections + memoises results for 60 s (audit P-1 follow-up).
    """
    cache_key = (niche_id, platform)
    cached = _CHANNEL_METRICS_CACHE.get(cache_key)
    if cached is not None and (time.monotonic() - cached[0]) < _CHANNEL_METRICS_TTL_SEC:
        return cached[1]

    pool = _get_pg_pool()
    if pool is None:
        return {}

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT metric_name, current_value
                    FROM monetisationprogress
                    WHERE niche_id = %s AND platform = %s
                      AND current_value IS NOT NULL
                    """,
                    (niche_id, platform),
                )
                result = {str(name): float(val) for name, val in cur.fetchall() if val is not None}
    except Exception as exc:
        logger.debug(
            "[reward] get_channel_metrics failed for %s/%s: %s",
            niche_id,
            platform,
            exc,
        )
        return {}

    _CHANNEL_METRICS_CACHE[cache_key] = (time.monotonic(), result)
    return result


@task(name="compute_reward")
def compute_reward(
    metrics: dict[str, Any],
    platform: str,
    shaper: RewardShaper,
    niche_id: str = "",
) -> float:
    """Compute shaped reward from 48h metrics.

    Threshold-proximity boosting fires when ``niche_id`` is provided
    and the channel is within 20% of any monetisation threshold for
    this platform. Without ``niche_id``, falls back to base weights.
    """
    channel_metrics = get_channel_metrics(niche_id, platform) if niche_id else None
    return shaper.compute_reward(
        platform=platform,
        metrics=metrics,
        channel_metrics=channel_metrics,
    )


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
        reward_48h = compute_reward(
            metrics,
            task_record.platform,
            shaper,
            niche_id=task_record.niche_id,
        )
        logger.info(
            "[metric_collector] 48h reward for %s/%s: %.3f",
            task_record.platform,
            task_record.platform_post_id,
            reward_48h,
        )

        # Update content bandit with the 48h reward signal.
        # The arm name is the niche-specific classified arm (e.g.
        # 'gameplay_clip', 'cast_reveal', 'season_announcement') —
        # stored as task_record.bandit_arm by push_to_backlog._classify_arm.
        # ``content_type`` is just the media kind ('video' / 'unknown')
        # and won't match any row in bandit_arms.  Fall back to it only
        # if bandit_arm is missing so legacy rows still flow.
        arm_for_update = task_record.bandit_arm or task_record.content_type
        if bandit_updater is not None and arm_for_update:
            bandit_update_succeeded = False
            try:
                bandit_updater(
                    task_record.niche_id,
                    arm_for_update,
                    task_record.platform,
                    reward_48h,
                    task_record.bandit_context,
                )
                logger.info(
                    "[metric_collector] bandit updated: niche=%s arm=%s platform=%s reward=%.3f",
                    task_record.niche_id,
                    arm_for_update,
                    task_record.platform,
                    reward_48h,
                )
                bandit_update_succeeded = True
            except Exception as exc:
                logger.warning(
                    "[metric_collector] bandit update failed for %s/%s: %s",
                    task_record.platform,
                    task_record.platform_post_id,
                    exc,
                )
            # Stamp the row so the daily backfill timer's
            # ``(extra->>'bandit_backfilled_at') IS NULL`` filter
            # correctly excludes it. Without this, the live updater
            # and the backfill script were independent — the backfill
            # would double-update bandit_arms when it next ran with
            # --include-post-fix. See PendingFeedbackStore.
            # mark_bandit_processed for the contract details.
            if bandit_update_succeeded:
                store.mark_bandit_processed(task_record)

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
    not_due = 0
    failed = 0
    now = datetime.now(UTC)

    for task_record in pending:
        try:
            # A None next-window means "no collection window has elapsed yet"
            # — not a failure. Track separately so the health check can
            # distinguish "everything is too young" from "everything broke".
            if store.next_collection_window(task_record, now=now) is None:
                not_due += 1
                continue
            if process_pending_task(
                task_record,
                store,
                shaper,
                now=now,
                bandit_updater=bandit_updater,
                backlog_client=backlog_client,
            ):
                processed += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            logger.warning(
                "[metric_collector] Failed to process %s/%s: %s",
                task_record.platform,
                task_record.platform_post_id,
                exc,
            )

    logger.info(
        "[metric_collector] Processed %d / %d tasks (not_due=%d, failed=%d)",
        processed,
        len(pending),
        not_due,
        failed,
    )

    # Health check fires only when something *should* have processed but
    # didn't. "All tasks waiting on their next window" is the happy path
    # when posts are fresh — flagging it as stalled sends future audits
    # chasing a non-bug (2026-05-20 RCA found this pattern wasted ~30 min).
    due_tasks = len(pending) - not_due
    if processed == 0 and due_tasks > 10:
        logger.warning(
            "[metric_collector] HEALTH CHECK: 0/%d eligible tasks processed "
            "(of %d total, %d not yet due) — learning loop may be stalled. "
            "Check fetch_platform_metrics + next_collection_window logic.",
            due_tasks,
            len(pending),
            not_due,
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

    Math (2026-05-16 audit fix + 2026-06-15 D3.8 platform multipliers):
      * Per-platform multiplier scales reward BEFORE clip:
            scaled = reward * get_multiplier(platform)
            alpha += clip(scaled, 0, 1)
            beta  += 1 - clip(scaled, 0, 1)
      * Multipliers live in ``config/platform_reward_multipliers.yaml``;
        default 1.0 for any platform not listed (no behaviour change).
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

        # D3.8 (2026-06-15, AUTO #2 runbook): scale by per-platform
        # multiplier BEFORE clip(0, 1). Platforms with higher
        # monetisation density per impression earn more bandit credit
        # per post, biasing the gate toward arms that perform on
        # monetisation-heavy surfaces. Default 1.0 preserves pre-D3.8
        # behaviour for any platform not listed in the YAML.
        from genlab_core.learning.platform_reward_multipliers import get_multiplier

        platform_multiplier = get_multiplier(platform)
        scaled_reward = float(reward) * platform_multiplier
        reward_clipped = max(0.0, min(1.0, scaled_reward))
        if platform_multiplier != 1.0:
            logger.debug(
                "[bandit_updater] %s/%s: raw_reward=%.3f * mult=%.2f -> %.3f (clipped %.3f)",
                niche_id,
                platform,
                reward,
                platform_multiplier,
                scaled_reward,
                reward_clipped,
            )

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
                    raw_state = fields.get("linucb_state") or fields.get("LinUCB_State") or ""
                    if raw_state:
                        # 2026-06-15 audit fix: psycopg auto-decodes JSONB
                        # columns to Python dict. The previous unconditional
                        # _json.loads() raised "JSON object must be str,
                        # bytes or bytearray, not dict" on every populated
                        # arm — the exception was caught and silenced as
                        # "falling back to Thompson", so every LinUCB
                        # contextual update was lost across 18 arms in
                        # 5 niches. Tolerate both shapes: dict (Postgres
                        # path) or JSON string (SharePoint / legacy path).
                        state_dict = (
                            raw_state if isinstance(raw_state, dict) else _json.loads(raw_state)
                        )
                        arm = LinUCBArm.from_dict(state_dict)
                    else:
                        arm = LinUCBArm(d=CONTEXT_DIM)
                    arm.update(linucb_ctx_array, reward_clipped)
                    linucb_state_dict = arm.to_dict()
                    logger.info(
                        "[bandit_updater] LinUCB updated: %s/%s n_obs=%d",
                        niche_id,
                        item_arm,
                        arm.n_obs,
                    )
                except Exception as linucb_exc:
                    logger.warning(
                        "[bandit_updater] LinUCB update failed for %s/%s "
                        "(falling back to Thompson): %s",
                        niche_id,
                        item_arm,
                        linucb_exc,
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
                niche_id,
                item_arm,
                reward_clipped,
                alpha,
                beta,
                n_plays,
            )

        # Sanity log: if we asked for N arms but updated fewer, surface
        # the gap. Common cause: the arm doesn't exist in bandit_arms
        # (e.g. style not yet seeded for this niche).
        missing = target_arms - set(updated)
        if missing:
            logger.warning(
                "[bandit_updater] %d arm(s) requested but not found in bandit_arms (niche=%s): %s",
                len(missing),
                niche_id,
                sorted(missing),
            )
    except Exception as exc:
        logger.warning("[bandit_updater] Failed: %s", exc)


if __name__ == "__main__":
    # Configure root logger so INFO lines surface to systemd journal.
    # Without this, the root logger has no handler and every logger.info
    # call (Processing N tasks, per-row bandit update, 48h reward, etc.)
    # is silently dropped — only WARNING+ reaches journald. This made
    # the May 2026 audit invisible to anyone reading systemctl status.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    collect_metrics(bandit_updater=_default_bandit_updater)
