"""Pipeline stage: Fetch post-publish engagement metrics.

Pulls metrics from Instagram, YouTube, Facebook, and X/Twitter for
recently published stories. Uses multi-window strategy:
  - FRESH: 6-48h after publish (first snapshot)
  - WARM:  2-7 days (growth tracking)

Writes metrics into context['run_stats']['insights'] and updates
each story's engagement data in context['stories'].

Non-fatal: API failures are logged per-platform, never crash pipeline.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Minimum hours after publish before fetching (API data delay)
MIN_DELAY_HOURS = 6
# Maximum age for "warm" window
MAX_WARM_DAYS = 7


class FetchInsights:
    """Fetch post-publish engagement metrics from platform APIs.

    Reads: context['stories'], context['niche_config']
    Writes: context['stories'][*]['engagement'], context['run_stats']['insights']
    """

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        stories = context.get("stories", [])
        if not stories:
            logger.info("[FetchInsights] No stories to fetch insights for")
            return context

        config = context.get("niche_config", {})
        now = datetime.now(timezone.utc)

        fetched = 0
        skipped = 0
        errors = 0
        platform_stats: Dict[str, Dict[str, int]] = {}

        for story in stories:
            published = story.get("published_platforms", {})
            if not published:
                skipped += 1
                continue

            published_at = story.get("published_at")
            if not published_at:
                skipped += 1
                continue

            # Parse publish time
            try:
                if isinstance(published_at, str):
                    pub_dt = datetime.fromisoformat(
                        published_at.replace("Z", "+00:00")
                    )
                else:
                    pub_dt = published_at
            except (ValueError, TypeError):
                skipped += 1
                continue

            age_hours = (now - pub_dt).total_seconds() / 3600
            if age_hours < MIN_DELAY_HOURS:
                skipped += 1
                continue
            if age_hours > MAX_WARM_DAYS * 24:
                skipped += 1
                continue

            engagement = story.setdefault("engagement", {})

            for platform, post_data in published.items():
                post_id = post_data if isinstance(post_data, str) else post_data.get("id", "")
                if not post_id:
                    continue

                stats = platform_stats.setdefault(
                    platform, {"fetched": 0, "errors": 0},
                )

                try:
                    metrics = self._fetch_platform(platform, post_id, config)
                    if metrics:
                        engagement[platform] = {
                            "metrics": metrics,
                            "fetched_at": now.isoformat(),
                            "age_hours": round(age_hours, 1),
                        }
                        stats["fetched"] += 1
                        fetched += 1
                    else:
                        stats["errors"] += 1
                except Exception:
                    logger.exception(
                        "[FetchInsights] %s fetch failed for post %s",
                        platform, post_id,
                    )
                    stats["errors"] += 1
                    errors += 1

        logger.info(
            "[FetchInsights] %d fetched, %d skipped, %d errors | %s",
            fetched, skipped, errors,
            {p: s for p, s in platform_stats.items()},
        )

        context.setdefault("run_stats", {})["insights"] = {
            "fetched": fetched,
            "skipped": skipped,
            "errors": errors,
            "platforms": platform_stats,
        }

        return context

    def _fetch_platform(
        self,
        platform: str,
        post_id: str,
        config: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Dispatch to platform-specific fetcher."""
        fetchers = {
            "instagram": self._fetch_instagram,
            "youtube": self._fetch_youtube,
            "facebook": self._fetch_facebook,
            "x": self._fetch_twitter,
            "twitter": self._fetch_twitter,
        }
        fetcher = fetchers.get(platform)
        if not fetcher:
            logger.debug("[FetchInsights] No fetcher for platform: %s", platform)
            return None
        return fetcher(post_id, config)

    @staticmethod
    def _fetch_instagram(post_id: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch IG metrics via graph.facebook.com."""
        token = os.getenv("META_ACCESS_TOKEN", "")
        if not token:
            return None

        try:
            import requests
            # Basic metrics
            url = f"https://graph.facebook.com/v21.0/{post_id}"
            params = {
                "fields": "like_count,comments_count,media_type",
                "access_token": token,
            }
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning("[FetchInsights] IG %d: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()

            # Insights — Reels do NOT support 'impressions' (400 error)
            insights_url = f"https://graph.facebook.com/v21.0/{post_id}/insights"
            insights_params = {
                "metric": "reach,saved,shares,total_interactions",
                "access_token": token,
            }
            insights_resp = requests.get(insights_url, params=insights_params, timeout=15)
            insights = {}
            if insights_resp.status_code == 200:
                for item in insights_resp.json().get("data", []):
                    name = item.get("name", "")
                    values = item.get("values", [{}])
                    insights[name] = values[0].get("value", 0) if values else 0

            return {
                "likes": data.get("like_count", 0),
                "comments": data.get("comments_count", 0),
                "reach": insights.get("reach", 0),
                "saved": insights.get("saved", 0),
                "shares": insights.get("shares", 0),
            }
        except Exception:
            logger.exception("[FetchInsights] IG fetch error for %s", post_id)
            return None

    @staticmethod
    def _fetch_youtube(post_id: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch YT metrics via Data API v3."""
        api_key = os.getenv("YOUTUBE_API_KEY", "")
        if not api_key:
            return None

        try:
            import requests
            url = "https://www.googleapis.com/youtube/v3/videos"
            params = {
                "part": "statistics",
                "id": post_id,
                "key": api_key,
            }
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                return None
            items = resp.json().get("items", [])
            if not items:
                return None
            stats = items[0].get("statistics", {})
            return {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
            }
        except Exception:
            logger.exception("[FetchInsights] YT fetch error for %s", post_id)
            return None

    @staticmethod
    def _fetch_facebook(post_id: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch FB metrics via Graph API."""
        token = os.getenv("META_ACCESS_TOKEN", "")
        if not token:
            return None

        try:
            import requests
            url = f"https://graph.facebook.com/v21.0/{post_id}"
            params = {
                "fields": "shares,reactions.summary(total_count),comments.summary(total_count)",
                "access_token": token,
            }
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                return None
            data = resp.json()
            return {
                "shares": data.get("shares", {}).get("count", 0),
                "reactions": data.get("reactions", {}).get("summary", {}).get("total_count", 0),
                "comments": data.get("comments", {}).get("summary", {}).get("total_count", 0),
            }
        except Exception:
            logger.exception("[FetchInsights] FB fetch error for %s", post_id)
            return None

    @staticmethod
    def _fetch_twitter(post_id: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch X metrics via API v2."""
        bearer = os.getenv("X_BEARER_TOKEN", "")
        if not bearer:
            return None

        try:
            import requests
            url = f"https://api.twitter.com/2/tweets/{post_id}"
            params = {"tweet.fields": "public_metrics"}
            headers = {"Authorization": f"Bearer {bearer}"}
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                return None
            metrics = resp.json().get("data", {}).get("public_metrics", {})
            return {
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "impressions": metrics.get("impression_count", 0),
            }
        except Exception:
            logger.exception("[FetchInsights] X fetch error for %s", post_id)
            return None
