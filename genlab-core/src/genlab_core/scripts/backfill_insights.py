#!/usr/bin/env python3
"""Historical engagement backfill — fetch metrics for ALL published posts.

Iterates every SUCCESS record in publishing_analytics, fetches live
engagement metrics from the platform API, and writes to the analytics table.

Skips posts that already have analytics data (idempotent).

Usage:
    uv run python -m genlab_core.scripts.backfill_insights
    uv run python -m genlab_core.scripts.backfill_insights --dry-run
    uv run python -m genlab_core.scripts.backfill_insights --niche-id gaming
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Any

from genlab_core.platforms.meta_api import META_GRAPH_BASE_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("genlab.backfill")

NICHE_ENVS: dict[str, str] = {
    "ai_creators": "BlackboxBrief",
    "gaming": "CriticalRush",
    "sports": "ClutchWire",
    "movies": "SpliceReel",
    "anime": "FrameDrift",
}

GENLAB_ROOT = Path(__file__).resolve().parents[4]


def _load_env(niche_id: str) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(GENLAB_ROOT / ".env", override=False)
        dir_name = NICHE_ENVS.get(niche_id, "")
        if dir_name:
            load_dotenv(GENLAB_ROOT / dir_name / ".env", override=True)
    except ImportError:
        pass


def _strip_prefix(post_id: str) -> str:
    return post_id.split(":", 1)[1] if ":" in post_id else post_id


def _resolve_ig_media_id(shortcode: str, token: str, ig_user_id: str) -> str | None:
    if shortcode.isdigit():
        return shortcode
    if not ig_user_id:
        return None
    import requests

    resp = requests.get(
        f"{META_GRAPH_BASE_URL}/{ig_user_id}/media",
        params={"fields": "id,shortcode", "limit": 100, "access_token": token},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    for item in resp.json().get("data", []):
        if item.get("shortcode") == shortcode:
            return item["id"]
    return None


def fetch_instagram(post_id: str, niche_id: str) -> dict[str, Any] | None:
    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    creds = resolve_meta_credentials(niche_id)
    token = creds.get("ig_access_token", "")
    ig_user_id = creds.get("ig_user_id", "")
    if not token:
        return None

    raw_id = _strip_prefix(post_id)
    media_id = _resolve_ig_media_id(raw_id, token, ig_user_id)
    if not media_id:
        logger.debug("Could not resolve IG media ID for %s", raw_id)
        return None

    import requests

    base = META_GRAPH_BASE_URL
    r = requests.get(
        f"{base}/{media_id}",
        params={"fields": "like_count,comments_count", "access_token": token},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    data = r.json()

    ins = requests.get(
        f"{base}/{media_id}/insights",
        params={
            "metric": "reach,saved,shares,likes,comments,total_interactions",
            "access_token": token,
        },
        timeout=15,
    )
    insights = {}
    if ins.status_code == 200:
        for item in ins.json().get("data", []):
            insights[item["name"]] = item["values"][0]["value"] if item.get("values") else 0

    return {
        "likes": insights.get("likes", data.get("like_count", 0)),
        "comments": insights.get("comments", data.get("comments_count", 0)),
        "reach": insights.get("reach", 0),
        "saved": insights.get("saved", 0),
        "shares": insights.get("shares", 0),
        "engagement": insights.get("total_interactions", 0),
        "impressions": insights.get("reach", 0),
    }


def fetch_youtube(post_id: str) -> dict[str, Any] | None:
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return None
    raw_id = _strip_prefix(post_id)
    import requests

    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "statistics", "id": raw_id, "key": api_key},
        timeout=15,
    )
    if resp.status_code != 200 or not resp.json().get("items"):
        return None
    stats = resp.json()["items"][0]["statistics"]
    views = int(stats.get("viewCount", 0))
    likes = int(stats.get("likeCount", 0))
    comments = int(stats.get("commentCount", 0))
    return {
        "views": views,
        "likes": likes,
        "comments": comments,
        "reach": views,
        "engagement": likes + comments,
    }


def fetch_facebook(post_id: str, niche_id: str) -> dict[str, Any] | None:
    from genlab_core.publishing.niche_credentials import resolve_fb_credentials

    token, _ = resolve_fb_credentials(niche_id)
    if not token:
        return None
    raw_id = _strip_prefix(post_id)
    import requests

    resp = requests.get(
        f"{META_GRAPH_BASE_URL}/{raw_id}/video_insights",
        params={"access_token": token},
        timeout=15,
    )
    if resp.status_code != 200:
        # Try basic metrics
        resp2 = requests.get(
            f"{META_GRAPH_BASE_URL}/{raw_id}",
            params={
                "fields": "shares,reactions.summary(true),comments.summary(true)",
                "access_token": token,
            },
            timeout=15,
        )
        if resp2.status_code != 200:
            return None
        d = resp2.json()
        reactions = d.get("reactions", {}).get("summary", {}).get("total_count", 0)
        comments = d.get("comments", {}).get("summary", {}).get("total_count", 0)
        return {
            "likes": reactions,
            "comments": comments,
            "engagement": reactions + comments,
            "reach": 0,
        }

    metrics = {}
    likes = 0
    for item in resp.json().get("data", []):
        name = item.get("name", "")
        val = item["values"][0]["value"] if item.get("values") else 0
        if name == "post_video_likes_by_reaction_type" and isinstance(val, dict):
            likes = sum(val.values())
        elif name == "post_video_views":
            metrics["views"] = val
    return {
        "likes": likes,
        "views": metrics.get("views", 0),
        "engagement": likes + metrics.get("views", 0),
        "reach": metrics.get("views", 0),
    }


def fetch_twitter(post_id: str) -> dict[str, Any] | None:
    bearer = os.environ.get("X_BEARER_TOKEN", "")
    if not bearer:
        return None
    raw_id = _strip_prefix(post_id)
    import requests

    resp = requests.get(
        f"https://api.twitter.com/2/tweets/{raw_id}",
        params={"tweet.fields": "public_metrics"},
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    m = resp.json().get("data", {}).get("public_metrics", {})
    return {
        "likes": m.get("like_count", 0),
        "retweets": m.get("retweet_count", 0),
        "replies": m.get("reply_count", 0),
        "shares": m.get("retweet_count", 0),
        "impressions": m.get("impression_count", 0),
        "engagement": m.get("like_count", 0) + m.get("retweet_count", 0),
        "reach": m.get("impression_count", 0),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Backfill engagement metrics for all published posts"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--niche-id", default="all", choices=list(NICHE_ENVS.keys()) + ["all"])
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    _load_env("ai_creators")
    dsn = os.environ.get("DATABASE_URL", "postgresql://localhost/genlab")
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.execute("SELECT set_config('app.niche_id', 'all', true)")

    # Get all SUCCESS posts
    if args.niche_id == "all":
        posts = conn.execute("""
            SELECT DISTINCT post_id, platform, niche_id, created_at
            FROM publishing_analytics
            WHERE status = 'SUCCESS' AND post_id IS NOT NULL AND LENGTH(post_id) > 5
            ORDER BY created_at
        """).fetchall()
    else:
        posts = conn.execute(
            """
            SELECT DISTINCT post_id, platform, niche_id, created_at
            FROM publishing_analytics
            WHERE status = 'SUCCESS' AND niche_id = %s AND post_id IS NOT NULL AND LENGTH(post_id) > 5
            ORDER BY created_at
        """,
            (args.niche_id,),
        ).fetchall()

    # Filter out posts that already have analytics
    existing = set()
    for r in conn.execute("SELECT DISTINCT post_id FROM analytics").fetchall():
        existing.add(r["post_id"])

    to_backfill = [p for p in posts if p["post_id"] not in existing]
    logger.info(
        "Total published: %d  Already have insights: %d  To backfill: %d",
        len(posts),
        len(posts) - len(to_backfill),
        len(to_backfill),
    )

    if args.dry_run:
        for p in to_backfill:
            logger.info(
                "[DRY RUN] Would fetch: %s/%s (%s)", p["platform"], p["post_id"][:30], p["niche_id"]
            )
        return

    from genlab_core.http.backlog_client import BacklogClient

    client = BacklogClient()

    fetched = 0
    errors = 0

    for p in to_backfill:
        _load_env(p["niche_id"])
        platform = p["platform"]
        post_id = p["post_id"]
        niche_id = p["niche_id"]

        insights = None
        try:
            if platform == "instagram":
                insights = fetch_instagram(post_id, niche_id)
            elif platform == "youtube":
                insights = fetch_youtube(post_id)
            elif platform == "facebook":
                insights = fetch_facebook(post_id, niche_id)
            elif platform == "threads":
                # 2026-07-22: Threads DOES have an insights API — the prior
                # `# threads: no insights API` comment was wrong. Delegate to
                # the canonical fetcher (proven live via probe returning real
                # views). Sibling gap to the run_fetch_insights.py Threads
                # dispatch shipped tonight in `f9f186c2`.
                from genlab_core.platforms.metrics import fetch_threads as _canonical_threads
                insights = _canonical_threads(_strip_prefix(post_id), niche_id=niche_id)
            elif platform in ("twitter", "x_twitter"):
                insights = fetch_twitter(post_id)
        except Exception as e:
            logger.warning("Fetch failed for %s/%s: %s", platform, post_id[:20], e)
            errors += 1
            time.sleep(0.5)
            continue

        if not insights:
            logger.debug("No data for %s/%s", platform, post_id[:20])
            errors += 1
            time.sleep(0.3)
            continue

        try:
            client.upsert_analytics(
                post_id=post_id,
                platform=platform,
                insights=insights,
                niche_id=niche_id,
                fetch_window="backfill",
                published_at=str(p["created_at"]) if p["created_at"] else "",
            )
            fetched += 1
            logger.info(
                "Backfilled %s/%s: engagement=%s",
                platform,
                post_id[:20],
                insights.get("engagement", "?"),
            )
        except Exception as e:
            logger.warning("Write failed for %s/%s: %s", platform, post_id[:20], e)
            errors += 1

        time.sleep(0.5)

    logger.info("Done: %d fetched, %d errors out of %d total", fetched, errors, len(to_backfill))
    conn.close()


if __name__ == "__main__":
    main()
