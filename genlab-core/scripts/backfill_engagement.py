#!/usr/bin/env python3
"""One-time backfill: fetch engagement for 6 live posts and write to blueprints.

Queries IG Insights API + YouTube Data API v3 for known post IDs,
computes engagement_rate, and writes to the Blueprints SharePoint list.

Usage:
    cd /Users/anarchistsid/GenLab
    BACKLOG_CONFIG_PATH="BlackboxBrief/config/lists_config.yaml" \
    uv run --package genlab-core python genlab-core/scripts/backfill_engagement.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure genlab-core is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

# Load credentials
genlab_root = Path(__file__).resolve().parents[2]
load_dotenv(genlab_root / ".env")
load_dotenv(genlab_root / ".env")
os.environ.setdefault(
    "BACKLOG_CONFIG_PATH",
    str(genlab_root / "genlab-core" / "config" / "lists_config.yaml"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_engagement")

POSTS = [
    {"blueprint_id": "1518", "niche": "anime",
     "ig_post_id": "17856613446624912", "yt_video_id": "8Y1MJr1aa70"},
    {"blueprint_id": "1519", "niche": "anime",
     "ig_post_id": "18203490037332700", "yt_video_id": "G9yJgS4RsXY"},
    {"blueprint_id": "1525", "niche": "movies",
     "ig_post_id": "18077819144617709", "yt_video_id": "CV6JAhX5qDM"},
    {"blueprint_id": "1536", "niche": "sports",
     "ig_post_id": "18100047686494718", "yt_video_id": "dwJKj09Necs"},
    {"blueprint_id": "1554", "niche": "gaming",
     "ig_post_id": "17936059974186072", "yt_video_id": "XMspX_-_iZQ"},
    {"blueprint_id": "1557", "niche": "ai_creators",
     "ig_post_id": None, "yt_video_id": "djMTelsepMU"},
]


def fetch_youtube_stats(video_id: str) -> dict[str, Any] | None:
    """Fetch YouTube video statistics. Only needs YOUTUBE_API_KEY.

    Retries once on empty response (video may not be indexed yet).
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        logger.warning("YOUTUBE_API_KEY not set — skipping YT")
        return None

    import requests
    for attempt in range(2):
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "statistics", "id": video_id, "key": api_key},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("YT API %d for %s: %s", resp.status_code, video_id, resp.text[:100])
            return None
        items = resp.json().get("items", [])
        if items:
            stats = items[0].get("statistics", {})
            return {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
            }
        # Empty response — video may not be indexed yet, retry once
        if attempt == 0:
            logger.info("YT video %s returned empty — retrying in 2s", video_id)
            time.sleep(2)

    logger.warning("YT video %s not found after retry", video_id)
    return None


def fetch_instagram_insights(post_id: str) -> dict[str, Any] | None:
    """Fetch IG media metrics + insights via graph.facebook.com."""
    token = os.getenv("META_ACCESS_TOKEN", "")
    if not token:
        logger.warning("META_ACCESS_TOKEN not set — skipping IG")
        return None

    import requests
    base = "https://graph.facebook.com/v21.0"

    # Basic metrics (likes, comments)
    r = requests.get(
        f"{base}/{post_id}",
        params={"fields": "like_count,comments_count", "access_token": token},
        timeout=15,
    )
    if r.status_code != 200:
        logger.warning("IG basic %d for %s: %s", r.status_code, post_id, r.text[:150])
        return None
    data = r.json()

    # Insights — Reels do NOT support 'impressions' (400 error); use reach,saved,shares,total_interactions
    ir = requests.get(
        f"{base}/{post_id}/insights",
        params={
            "metric": "reach,saved,shares,total_interactions",
            "access_token": token,
        },
        timeout=15,
    )
    insights = {}
    if ir.status_code == 200:
        for item in ir.json().get("data", []):
            name = item.get("name", "")
            values = item.get("values", [{}])
            insights[name] = values[0].get("value", 0) if values else 0
    else:
        logger.warning("IG insights %d for %s: %s", ir.status_code, post_id, ir.text[:100])

    return {
        "likes": data.get("like_count", 0),
        "comments": data.get("comments_count", 0),
        "reach": insights.get("reach", 0),
        "saved": insights.get("saved", 0),
        "shares": insights.get("shares", 0),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Backfill engagement data for live posts")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Re-fetch and overwrite even if data exists")
    parser.add_argument("--window", type=int, default=6,
                        help="Collection window in hours (6 or 24) — sets timestamp field")
    args = parser.parse_args()

    from genlab_core.http.backlog_client import BacklogClient

    client = BacklogClient()
    now_iso = datetime.now(UTC).isoformat()

    success = 0
    errors = 0

    for post in POSTS:
        bp_id = post["blueprint_id"]
        niche = post["niche"]
        logger.info("=== Blueprint #%s [%s] ===", bp_id, niche)

        fields: dict[str, Any] = {}

        # YouTube — write None explicitly on failure (not absent)
        yt_id = post["yt_video_id"]
        if yt_id:
            yt = fetch_youtube_stats(yt_id)
            if yt:
                fields["yt_views"] = yt["views"]
                fields["yt_likes"] = yt["likes"]
                fields["yt_comments"] = yt["comments"]
                logger.info("  YT: views=%d likes=%d comments=%d", yt["views"], yt["likes"], yt["comments"])
            else:
                logger.warning("  YT: no data for %s — writing None", yt_id)
                fields["yt_views"] = None
                fields["yt_likes"] = None
                fields["yt_comments"] = None
            time.sleep(1)

        # Instagram — write None explicitly on failure (not absent)
        ig_id = post["ig_post_id"]
        if ig_id:
            ig = fetch_instagram_insights(ig_id)
            if ig:
                fields["ig_reach"] = ig["reach"]
                fields["ig_likes"] = ig["likes"]
                fields["ig_comments"] = ig["comments"]
                logger.info("  IG: reach=%d likes=%d comments=%d", ig["reach"], ig["likes"], ig["comments"])
            else:
                logger.warning("  IG: no data for %s — writing None", ig_id)
                fields["ig_reach"] = None
                fields["ig_likes"] = None
                fields["ig_comments"] = None
            time.sleep(1)

        # Engagement rate
        ig_reach = fields.get("ig_reach") or 0
        ig_likes = fields.get("ig_likes") or 0
        ig_comments = fields.get("ig_comments") or 0
        yt_views = fields.get("yt_views") or 0
        yt_likes = fields.get("yt_likes") or 0
        yt_comments = fields.get("yt_comments") or 0

        if ig_reach > 0:
            fields["engagement_rate"] = round((ig_likes + ig_comments) / ig_reach, 4)
        elif yt_views > 0:
            fields["engagement_rate"] = round((yt_likes + yt_comments) / yt_views, 4)

        # Timestamps
        fields["insights_collected_at"] = now_iso
        window = args.window
        if window <= 8:
            fields["insights_6h_at"] = now_iso
        elif window <= 26:
            fields["insights_24h_at"] = now_iso

        # Always write — even if all metrics are None (explicit absence > silent gap)
        try:
            client.blueprints.update(bp_id, fields, typecast=True)
            logger.info("  Written to blueprint #%s: %s", bp_id, list(fields.keys()))
            success += 1
        except Exception as exc:
            logger.error("  Failed to write blueprint #%s: %s", bp_id, exc)
            errors += 1

    logger.info("=" * 50)
    logger.info("BACKFILL COMPLETE: %d success, %d errors", success, errors)


if __name__ == "__main__":
    main()
