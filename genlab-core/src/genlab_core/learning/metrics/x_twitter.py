"""X (Twitter) metric fetcher — extracted from learning/metric_collector.py.

Uses the X API v2 ``GET /2/tweets/{id}?tweet.fields=public_metrics`` endpoint
with an app-wide bearer token (X bearer is NOT per-niche-scoped — there's
a single ``X_BEARER_TOKEN`` env var used across all niches).

Reward-shape specialisation (see metric_collector.py docstring): computes
``reply_chain_rate`` (replies / impressions) and aligns the return shape to
``RewardShaper.BASE_WEIGHTS["twitter"]``. ``profile_clicks`` is in the
organic_tweet_metrics endpoint (Twitter API Pro tier only) — deliberately
OMITTED from the return dict so ``compute_reward`` redistributes its 0.10
weight instead of pinning to a fake zero.

Migration note (P5a phase 4, 2026-06-19): fourth per-platform module in the
metric_collector split. Smallest module yet (~45 LOC) since X has the simplest
auth model (no per-niche credentials, single API call, single response shape).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
    # Wrapped so 404 on deleted tweets, 429 rate-limits, 401 rotated auth,
    # or transient 5xx never propagate to the orchestrator's bare
    # `except Exception` (which would silently skip reward_48h). Round-2
    # audit (2026-07-08) found this was the same failure mode as YouTube's
    # unwrapped `raise_for_status()`; 76% of Twitter `pending_feedback`
    # rows had NULL `reward_48h`. See `audit-deep-dive-2026-07-08.md`.
    try:
        resp = requests.get(
            f"https://api.twitter.com/2/tweets/{post_id}",
            params={"tweet.fields": "public_metrics"},
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — never crash the reward pass
        logger.warning(
            "[metric_collector] X/Twitter fetch failed for %s: %s",
            post_id,
            exc,
        )
        return {}
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


__all__ = ["_fetch_x"]
