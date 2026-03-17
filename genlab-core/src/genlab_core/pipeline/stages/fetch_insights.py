"""Pipeline stage: Fetch post-publish engagement metrics.

Queries SharePoint Publishing_Analytics for PREVIOUSLY published posts
(6h-7d ago) and fetches engagement metrics from platform APIs.

Uses multi-window strategy:
  - FRESH: 6-48h after publish (first snapshot)
  - WARM:  2-7 days (growth tracking)

Writes metrics into context['run_stats']['insights'] and marks fetched
records in SharePoint via metrics_fetched timestamp.

Non-fatal: API failures are logged per-platform, never crash pipeline.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from genlab_core.http.circuit_breaker import (
    CircuitOpenError,
    get_circuit_breaker,
)

logger = logging.getLogger(__name__)

# Minimum hours after publish before fetching (API data delay)
MIN_DELAY_HOURS = 6
# Maximum age for "warm" window
MAX_WARM_DAYS = 7


class FetchInsights:
    """Fetch post-publish engagement metrics from platform APIs.

    Queries SharePoint Publishing_Analytics for posts published 6h-7d ago
    that haven't had metrics collected yet (metrics_fetched is empty).

    Reads: context['backlog_client'], context['niche_id'], context['niche_config']
    Writes: context['run_stats']['insights']
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        niche_id = context.get("niche_id", "")
        client = context.get("backlog_client")
        config = context.get("niche_config", {})

        if not client:
            logger.info("[FetchInsights] No backlog_client — skipping")
            return context

        now = datetime.now(UTC)
        fetched = 0
        skipped = 0
        errors = 0
        platform_stats: dict[str, dict[str, int]] = {}

        # Query Publishing_Analytics for posts in this niche
        try:
            formula = f"AND({{niche_id}}='{niche_id}')"
            records = client.publishing_analytics.all(formula=formula)
        except Exception:
            logger.exception("[FetchInsights] Failed to query Publishing_Analytics")
            context.setdefault("run_stats", {})["insights"] = {
                "fetched": 0, "skipped": 0, "errors": 1, "platforms": {},
            }
            return context

        for record in records:
            fields = record.get("fields", {})
            post_id = fields.get("post_id", "")
            platform = fields.get("platform", "")
            published_at = fields.get("published_at", "")
            metrics_fetched = fields.get("metrics_fetched", "")

            # Skip already fetched
            if metrics_fetched:
                skipped += 1
                continue

            # Skip if no post_id or platform
            if not post_id or not platform:
                skipped += 1
                continue

            # Parse publish time and check age
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

            # Fetch metrics
            stats = platform_stats.setdefault(
                platform, {"fetched": 0, "errors": 0},
            )
            try:
                metrics = self._fetch_platform(platform, post_id, config)
                if metrics:
                    # Mark as fetched in SharePoint
                    try:
                        client.publishing_analytics.update(
                            record["id"],
                            {"metrics_fetched": now.isoformat()},
                        )
                    except Exception:
                        logger.warning(
                            "[FetchInsights] Failed to mark %s as fetched",
                            post_id,
                        )
                    stats["fetched"] += 1
                    fetched += 1
                else:
                    skipped += 1
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
            {k: v for k, v in platform_stats.items() if v["fetched"] or v["errors"]},
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
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Dispatch to platform-specific fetcher through circuit breaker."""
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

        cb = get_circuit_breaker(platform)
        if cb is not None:
            try:
                return cb.call(fetcher, post_id, config)
            except CircuitOpenError:
                logger.warning(
                    "[FetchInsights] %s circuit open — skipping %s",
                    platform, post_id,
                )
                return None
        return fetcher(post_id, config)

    @staticmethod
    def _fetch_instagram(post_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
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
    def _fetch_youtube(post_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
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
    def _fetch_facebook(post_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
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
    def _fetch_twitter(post_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
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
