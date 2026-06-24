#!/usr/bin/env python3
"""
Gen Lab — Social Analytics Dashboard
Pulls engagement metrics from all connected platforms.
Run ad-hoc or scheduled for weekly reports.

Usage:
    uv run --package genlab-core python scripts/social_analytics.py
    uv run --package genlab-core python scripts/social_analytics.py --days 7
    uv run --package genlab-core python scripts/social_analytics.py --json
"""

# NOTE: Run via: uv run --package genlab-core python scripts/social_analytics.py
import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import requests

# PR #515 (2026-06-24): import canonical Meta Graph API base URL from
# genlab-core so this script stays in sync with version bumps. Pre-PR
# had 5 hardcoded ``v21.0`` URLs that lagged genlab-core's v22.0 bump.
from genlab_core.platforms.meta_api import META_GRAPH_BASE_URL as _META_GRAPH_BASE_URL  # noqa: E402

logger = logging.getLogger("social_analytics")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── YouTube Analytics ─────────────────────────────────────────────────
def _refresh_youtube_token() -> str | None:
    """Refresh YouTube OAuth token using refresh_token."""
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        return None
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    if resp.ok:
        return resp.json().get("access_token")
    logger.warning("YouTube token refresh failed: %s", resp.text[:200])
    return None


def get_youtube_analytics(days: int = 7) -> dict[str, Any]:
    """Pull YouTube channel stats for the last N days."""
    token = _refresh_youtube_token()
    if not token:
        return {"platform": "youtube", "error": "token_refresh_failed"}

    headers = {"Authorization": f"Bearer {token}"}

    # Channel stats
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "statistics,snippet", "mine": "true"},
        headers=headers,
        timeout=15,
    )
    if not resp.ok:
        return {"platform": "youtube", "error": resp.text[:200]}

    items = resp.json().get("items", [])
    if not items:
        return {"platform": "youtube", "error": "no_channel_found"}

    stats = items[0]["statistics"]
    channel_name = items[0]["snippet"]["title"]

    # Recent videos
    resp2 = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "channelId": items[0]["id"],
            "order": "date",
            "maxResults": 10,
            "type": "video",
            "publishedAfter": (datetime.now(UTC) - timedelta(days=days)).isoformat(),
        },
        headers=headers,
        timeout=15,
    )
    recent_ids = (
        [item["id"]["videoId"] for item in resp2.json().get("items", [])] if resp2.ok else []
    )

    video_stats = []
    if recent_ids:
        resp3 = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "statistics,snippet", "id": ",".join(recent_ids)},
            headers=headers,
            timeout=15,
        )
        if resp3.ok:
            for v in resp3.json().get("items", []):
                vs = v["statistics"]
                video_stats.append(
                    {
                        "title": v["snippet"]["title"][:60],
                        "views": int(vs.get("viewCount", 0)),
                        "likes": int(vs.get("likeCount", 0)),
                        "comments": int(vs.get("commentCount", 0)),
                        "published": v["snippet"]["publishedAt"],
                    }
                )

    return {
        "platform": "youtube",
        "channel": channel_name,
        "subscribers": int(stats.get("subscriberCount", 0)),
        "total_views": int(stats.get("viewCount", 0)),
        "total_videos": int(stats.get("videoCount", 0)),
        "recent_videos": sorted(video_stats, key=lambda x: x["views"], reverse=True),
    }


# ── Instagram / Meta Analytics ────────────────────────────────────────
def get_instagram_analytics(days: int = 7) -> dict[str, Any]:
    """Pull Instagram account insights via Facebook Page token."""
    token = os.getenv("FB_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("META_FB_PAGE_ID")
    if not token or not page_id:
        return {"platform": "instagram", "error": "no_page_token_or_page_id"}

    # Get IG business account ID from the Facebook Page
    resp = requests.get(
        f"{_META_GRAPH_BASE_URL}/{page_id}",
        params={"access_token": token, "fields": "instagram_business_account"},
        timeout=15,
    )
    if not resp.ok:
        return {"platform": "instagram", "error": f"page_lookup_failed: {resp.text[:200]}"}

    ig_id = resp.json().get("instagram_business_account", {}).get("id")
    if not ig_id:
        return {"platform": "instagram", "error": "no_ig_business_account_linked"}

    # Account info
    resp2 = requests.get(
        f"{_META_GRAPH_BASE_URL}/{ig_id}",
        params={"access_token": token, "fields": "username,followers_count,media_count"},
        timeout=15,
    )
    account = resp2.json() if resp2.ok else {}

    # Recent media
    resp3 = requests.get(
        f"{_META_GRAPH_BASE_URL}/{ig_id}/media",
        params={
            "access_token": token,
            "fields": "caption,like_count,comments_count,timestamp,media_type",
            "limit": 10,
        },
        timeout=15,
    )
    media = []
    if resp3.ok:
        since = datetime.now(UTC) - timedelta(days=days)
        for m in resp3.json().get("data", []):
            ts = datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts >= since:
                media.append(
                    {
                        "caption": (m.get("caption") or "")[:60],
                        "likes": m.get("like_count", 0),
                        "comments": m.get("comments_count", 0),
                        "type": m.get("media_type"),
                        "posted": m["timestamp"],
                    }
                )

    return {
        "platform": "instagram",
        "username": account.get("username"),
        "followers": account.get("followers_count", 0),
        "total_posts": account.get("media_count", 0),
        "recent_posts": sorted(media, key=lambda x: x["likes"], reverse=True),
    }


# ── Facebook Page Analytics ───────────────────────────────────────────
def get_facebook_analytics(days: int = 7) -> dict[str, Any]:
    """Pull Facebook page engagement metrics."""
    token = os.getenv("FB_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("META_FB_PAGE_ID")
    if not token or not page_id:
        return {"platform": "facebook", "error": "no_token_or_page_id"}

    # Page info
    resp = requests.get(
        f"{_META_GRAPH_BASE_URL}/{page_id}",
        params={"access_token": token, "fields": "name,followers_count,fan_count"},
        timeout=15,
    )
    if not resp.ok:
        return {"platform": "facebook", "error": f"page_fetch_failed: {resp.text[:200]}"}

    page = resp.json()

    # Recent posts
    since_ts = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    resp2 = requests.get(
        f"{_META_GRAPH_BASE_URL}/{page_id}/posts",
        params={
            "access_token": token,
            "fields": "message,created_time,shares,likes.summary(true),comments.summary(true)",
            "since": since_ts,
            "limit": 10,
        },
        timeout=15,
    )
    posts = []
    if resp2.ok:
        for p in resp2.json().get("data", []):
            posts.append(
                {
                    "message": (p.get("message") or "")[:60],
                    "likes": p.get("likes", {}).get("summary", {}).get("total_count", 0),
                    "comments": p.get("comments", {}).get("summary", {}).get("total_count", 0),
                    "shares": p.get("shares", {}).get("count", 0),
                    "posted": p.get("created_time"),
                }
            )

    return {
        "platform": "facebook",
        "page_name": page.get("name"),
        "followers": page.get("followers_count", 0),
        "fans": page.get("fan_count", 0),
        "recent_posts": sorted(posts, key=lambda x: x["likes"], reverse=True),
    }


# ── X/Twitter Analytics ──────────────────────────────────────────────
def get_x_analytics(days: int = 7) -> dict[str, Any]:
    """Pull X/Twitter account and tweet metrics using OAuth 1.0a User Context."""
    from requests_oauthlib import OAuth1

    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_SECRET")

    if not all([api_key, api_secret, access_token, access_secret]):
        return {"platform": "x_twitter", "error": "missing_oauth1_credentials"}

    auth = OAuth1(api_key, api_secret, access_token, access_secret)

    # Get authenticated user
    resp = requests.get(
        "https://api.twitter.com/2/users/me",
        params={"user.fields": "public_metrics,username"},
        auth=auth,
        timeout=15,
    )
    if not resp.ok:
        return {"platform": "x_twitter", "error": f"user_fetch_failed: {resp.text[:200]}"}

    user = resp.json().get("data", {})
    metrics = user.get("public_metrics", {})

    # Recent tweets
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    resp2 = requests.get(
        f"https://api.twitter.com/2/users/{user['id']}/tweets",
        params={
            "start_time": since,
            "max_results": 10,
            "tweet.fields": "public_metrics,created_at",
        },
        auth=auth,
        timeout=15,
    )
    tweets = []
    if resp2.ok:
        for t in resp2.json().get("data", []):
            tm = t.get("public_metrics", {})
            tweets.append(
                {
                    "text": t["text"][:60],
                    "likes": tm.get("like_count", 0),
                    "retweets": tm.get("retweet_count", 0),
                    "replies": tm.get("reply_count", 0),
                    "impressions": tm.get("impression_count", 0),
                    "posted": t.get("created_at"),
                }
            )

    return {
        "platform": "x_twitter",
        "username": user.get("username"),
        "followers": metrics.get("followers_count", 0),
        "following": metrics.get("following_count", 0),
        "tweet_count": metrics.get("tweet_count", 0),
        "recent_tweets": sorted(tweets, key=lambda x: x["likes"], reverse=True),
    }


# ── Threads Analytics ─────────────────────────────────────────────────
def get_threads_analytics(days: int = 7) -> dict[str, Any]:
    """Pull Threads profile and post metrics."""
    token = os.getenv("THREADS_ACCESS_TOKEN")
    if not token:
        return {"platform": "threads", "error": "no_token"}

    resp = requests.get(
        "https://graph.threads.net/v1.0/me",
        params={"access_token": token, "fields": "username,threads_profile_picture_url"},
        timeout=15,
    )
    if not resp.ok:
        return {"platform": "threads", "error": f"profile_failed: {resp.text[:200]}"}

    profile = resp.json()

    # Recent threads
    resp2 = requests.get(
        "https://graph.threads.net/v1.0/me/threads",
        params={
            "access_token": token,
            "fields": "text,timestamp,likes,replies",
            "limit": 10,
        },
        timeout=15,
    )
    posts = []
    if resp2.ok:
        since = datetime.now(UTC) - timedelta(days=days)
        for t in resp2.json().get("data", []):
            ts_str = t.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("+0000", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts < since:
                    continue
            except (ValueError, TypeError):
                pass
            posts.append(
                {
                    "text": (t.get("text") or "")[:60],
                    "likes": t.get("likes", 0),
                    "replies": t.get("replies", 0),
                    "posted": ts_str,
                }
            )

    return {
        "platform": "threads",
        "username": profile.get("username"),
        "recent_posts": posts,
    }


# ── Aggregator ────────────────────────────────────────────────────────
def pull_all_analytics(days: int = 7) -> dict[str, Any]:
    """Pull analytics from all platforms and aggregate."""
    results = {}
    for name, fn in [
        ("youtube", get_youtube_analytics),
        ("instagram", get_instagram_analytics),
        ("facebook", get_facebook_analytics),
        ("x_twitter", get_x_analytics),
        ("threads", get_threads_analytics),
    ]:
        try:
            results[name] = fn(days)
        except Exception as e:
            results[name] = {"platform": name, "error": str(e)[:200]}
            logger.exception("Failed to pull %s analytics", name)

    results["_meta"] = {
        "pulled_at": datetime.now(UTC).isoformat(),
        "period_days": days,
    }
    return results


def print_summary(data: dict[str, Any]) -> None:
    """Print a human-readable summary."""
    days = data["_meta"]["period_days"]
    print(f"\n{'=' * 60}")
    print(f"  Gen Lab Social Analytics — Last {days} days")
    print(f"  Pulled: {data['_meta']['pulled_at'][:19]}Z")
    print(f"{'=' * 60}\n")

    for platform in ["youtube", "instagram", "facebook", "x_twitter", "threads"]:
        d = data.get(platform, {})
        if "error" in d:
            print(f"  {platform.upper():12s}  ⚠ {d['error']}")
            continue

        followers = (
            d.get("followers") if d.get("followers") is not None else d.get("subscribers", "?")
        )
        print(f"  {platform.upper():12s}  {followers:>8} followers", end="")
        if d.get("username"):
            print(f"  (@{d['username']})", end="")
        print()

        posts = d.get("recent_videos") or d.get("recent_posts") or d.get("recent_tweets") or []
        if posts:
            top = posts[0]
            title = (
                top.get("title")
                or top.get("caption")
                or top.get("text")
                or top.get("message")
                or ""
            )
            likes = top.get("likes") or top.get("views") or 0
            metric_name = "views" if "views" in top else "likes"
            print(f"               Top: {title[:45]}... ({likes} {metric_name})")
        print()

    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gen Lab Social Analytics")
    parser.add_argument("--days", type=int, default=7, help="Lookback period in days")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--platform", type=str, help="Single platform to check")
    args = parser.parse_args()

    if args.platform:
        fn_map = {
            "youtube": get_youtube_analytics,
            "instagram": get_instagram_analytics,
            "facebook": get_facebook_analytics,
            "x_twitter": get_x_analytics,
            "threads": get_threads_analytics,
        }
        fn = fn_map.get(args.platform)
        if not fn:
            print(f"Unknown platform: {args.platform}. Options: {list(fn_map.keys())}")
            sys.exit(1)
        result = fn(args.days)
        print(json.dumps(result, indent=2, default=str))
    else:
        data = pull_all_analytics(args.days)
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            print_summary(data)
