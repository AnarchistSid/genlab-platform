"""YouTube Analytics API client for Gen Lab.

Wraps YouTube Analytics Data API v2 (youtubeAnalytics).
Used by fetch_insights.py in BB and CR to collect post-publish performance.

Metrics: views, estimatedMinutesWatched, averageViewDuration,
averageViewPercentage, likes, comments, shares, subscribersGained.

Constraint: 2-3 day data lag. Skips videos < 48h old.
Quota: 10,000 units/day. Each query = 1 unit.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from genlab_core.ratelimit.token_bucket import TokenBucket

logger = logging.getLogger(__name__)


class YouTubeAnalyticsMetrics:
    """Analytics metrics for a single video at a single time window."""

    __slots__ = (
        "video_id", "collected_at", "views", "watch_minutes",
        "avg_view_duration_s", "avg_view_pct", "likes",
        "comments", "shares", "subscribers_gained", "window_label",
    )

    def __init__(
        self,
        video_id: str,
        collected_at: datetime,
        window_label: str = "unknown",
        views: int = 0,
        watch_minutes: float = 0.0,
        avg_view_duration_s: float = 0.0,
        avg_view_pct: float = 0.0,
        likes: int = 0,
        comments: int = 0,
        shares: int = 0,
        subscribers_gained: int = 0,
    ) -> None:
        self.video_id = video_id
        self.collected_at = collected_at
        self.window_label = window_label
        self.views = views
        self.watch_minutes = watch_minutes
        self.avg_view_duration_s = avg_view_duration_s
        self.avg_view_pct = avg_view_pct
        self.likes = likes
        self.comments = comments
        self.shares = shares
        self.subscribers_gained = subscribers_gained


class YouTubeAnalyticsClient:
    """Fetches video-level analytics from YouTube Analytics API.

    The service object is passed in by the caller (avoids OAuth flow in tests).
    """

    METRICS = (
        "views,estimatedMinutesWatched,averageViewDuration,"
        "averageViewPercentage,likes,comments,shares,subscribersGained"
    )

    MIN_AGE_HOURS = 48  # Data lag guard — non-negotiable

    def __init__(self, analytics_service: Any, channel_id: str) -> None:
        self._service = analytics_service
        self._channel_id = channel_id
        # Conservative: 1 req/sec burst, ~500/day headroom for Data API quota
        self._rate_limiter = TokenBucket(rate=1.0, burst=5)

    def fetch_video_metrics(
        self,
        video_id: str,
        published_at: datetime,
        window_label: str = "48h",
    ) -> YouTubeAnalyticsMetrics | None:
        """Fetch metrics for a single video.

        Returns None if video < 48h old, API fails, or no data returned.
        """
        now = datetime.now(UTC)
        pub_utc = published_at.replace(tzinfo=UTC) if published_at.tzinfo is None else published_at
        age_hours = (now - pub_utc).total_seconds() / 3600

        if age_hours < self.MIN_AGE_HOURS:
            logger.debug(
                "[yt_analytics] skip %s — too recent (%.1fh < %dh)",
                video_id, age_hours, self.MIN_AGE_HOURS,
            )
            return None

        start_date = pub_utc.strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        try:
            self._rate_limiter.acquire()
            response = (
                self._service.reports()
                .query(
                    ids=f"channel=={self._channel_id}",
                    startDate=start_date,
                    endDate=end_date,
                    metrics=self.METRICS,
                    filters=f"video=={video_id}",
                    dimensions="video",
                )
                .execute()
            )

            rows = response.get("rows", [])
            if not rows:
                logger.debug("[yt_analytics] no data for %s", video_id)
                return None

            row = rows[0]
            headers = [col.get("name") for col in response.get("columnHeaders", [])]
            data = dict(zip(headers, row))

            return YouTubeAnalyticsMetrics(
                video_id=video_id,
                collected_at=now,
                window_label=window_label,
                views=int(data.get("views", 0)),
                watch_minutes=float(data.get("estimatedMinutesWatched", 0)),
                avg_view_duration_s=float(data.get("averageViewDuration", 0)),
                avg_view_pct=float(data.get("averageViewPercentage", 0)),
                likes=int(data.get("likes", 0)),
                comments=int(data.get("comments", 0)),
                shares=int(data.get("shares", 0)),
                subscribers_gained=int(data.get("subscribersGained", 0)),
            )

        except Exception as e:
            logger.warning("[yt_analytics] fetch failed for %s: %s", video_id, e)
            return None

    @staticmethod
    def build_service(credentials_path: str) -> Any:
        """Build analytics service from OAuth2 credentials file."""
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(
                credentials_path,
                scopes=["https://www.googleapis.com/auth/yt-analytics.readonly"],
            )
            return build("youtubeAnalytics", "v2", credentials=creds)
        except Exception as e:
            logger.warning("[yt_analytics] service build failed: %s", e)
            return None
