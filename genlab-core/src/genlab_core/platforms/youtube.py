"""YouTube Data API v3 platform client.

Implements Publisher + Engageable + Trackable + HealthCheckable protocols.

Auth: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN (OAuth2).
Upload: YouTube Data API v3 videos.insert (resumable, via googleapiclient).
Reply: /comments insert with parentId.
Like: no-op (YouTube Data API does not support liking comments programmatically).
Metrics: YouTube Analytics API v2 (48h data lag guard).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

from genlab_core.platforms.models import (
    PlatformMetrics,
    PublishPayload,
    PublishResult,
    TokenStatus,
    YouTubeSpecific,
)

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://www.googleapis.com/youtube/v3"
_ANALYTICS_BASE = "https://youtubeanalytics.googleapis.com/v2"

# Data lag guard — YouTube Analytics has a 48-hour propagation delay
_MIN_AGE_HOURS = 48

# Token TTL — refresh before the 60-min expiry
_TOKEN_TTL_SECONDS = 3000  # 50 minutes

# YouTube title limit
_TITLE_MAX_LEN = 100

# Analytics metrics requested
_ANALYTICS_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,"
    "averageViewPercentage,likes,comments,shares,subscribersGained"
)

# Lazy imports to avoid hard dependency on google-api-python-client at import time
try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    _GOOGLE_LIBS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _GOOGLE_LIBS_AVAILABLE = False
    Credentials = None  # type: ignore[assignment,misc]
    build = None  # type: ignore[assignment]
    MediaFileUpload = None  # type: ignore[assignment]


class YouTubeClient:
    """YouTube Data API v3 client implementing the unified platform protocols.

    Implements: Publisher, Engageable, Trackable, HealthCheckable.

    Args:
        client_id: OAuth2 client ID. Defaults to ``YOUTUBE_CLIENT_ID`` env var.
        client_secret: OAuth2 client secret. Defaults to ``YOUTUBE_CLIENT_SECRET``.
        refresh_token: OAuth2 refresh token. Defaults to ``YOUTUBE_REFRESH_TOKEN``.
    """

    platform_id = "youtube"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        *,
        expected_channel_id: str | None = None,
        enable_quota_gate: bool = True,
    ) -> None:
        self._client_id: str = client_id or os.environ.get("YOUTUBE_CLIENT_ID", "")
        self._client_secret: str = client_secret or os.environ.get(
            "YOUTUBE_CLIENT_SECRET", ""
        )
        self._refresh_token: str = refresh_token or os.environ.get(
            "YOUTUBE_REFRESH_TOKEN", ""
        )
        self._access_token: str | None = None
        self._token_refreshed_at: float = 0.0
        self._TOKEN_TTL_SECONDS: int = _TOKEN_TTL_SECONDS

        # Cross-channel guard: expected channel ID for verification
        self._expected_channel_id: str = expected_channel_id or ""

        # Quota gate (conditional import — graceful if monitoring module unavailable)
        self._quota_tracker: Any = None
        if enable_quota_gate:
            try:
                from genlab_core.monitoring.youtube_quota import YouTubeQuotaTracker

                self._quota_tracker = YouTubeQuotaTracker()
            except (ImportError, Exception) as exc:
                logger.debug("YouTube quota tracker unavailable: %s", exc)

        # Cached googleapiclient service objects
        self._data_service: Any = None
        self._analytics_service: Any = None

    # ------------------------------------------------------------------
    # Internal: OAuth2 token management
    # ------------------------------------------------------------------

    def _is_token_expired(self) -> bool:
        """Return True if cached token is absent or older than TTL."""
        if self._token_refreshed_at == 0.0:
            return True
        return (time.monotonic() - self._token_refreshed_at) >= self._TOKEN_TTL_SECONDS

    def _get_access_token(self) -> str:
        """Return a cached OAuth2 access token, refreshing if stale."""
        if self._access_token and not self._is_token_expired():
            return self._access_token

        resp = requests.post(
            _TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        self._token_refreshed_at = time.monotonic()
        return self._access_token

    def _get_data_service(self) -> Any:
        """Build / cache the YouTube Data API v3 service object."""
        if self._data_service is not None and not self._is_token_expired():
            return self._data_service

        creds = Credentials(
            token=None,
            refresh_token=self._refresh_token,
            client_id=self._client_id,
            client_secret=self._client_secret,
            token_uri=_TOKEN_URL,
        )
        self._data_service = build("youtube", "v3", credentials=creds)
        self._token_refreshed_at = time.monotonic()
        return self._data_service

    def _get_analytics_service(self) -> Any:
        """Build / cache the YouTube Analytics API v2 service object."""
        if self._analytics_service is not None and not self._is_token_expired():
            return self._analytics_service

        creds = Credentials(
            token=None,
            refresh_token=self._refresh_token,
            client_id=self._client_id,
            client_secret=self._client_secret,
            token_uri=_TOKEN_URL,
        )
        self._analytics_service = build("youtubeAnalytics", "v2", credentials=creds)
        self._token_refreshed_at = time.monotonic()
        return self._analytics_service

    def _get_channel_id(self, token: str) -> str | None:
        """Fetch the authenticated user's channel ID via raw HTTP."""
        try:
            resp = requests.get(
                f"{_API_BASE}/channels",
                params={"part": "id", "mine": "true"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            if items:
                return items[0]["id"]
        except Exception as exc:
            logger.warning("YouTube: failed to fetch channel ID: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Channel verification (cross-channel guard)
    # ------------------------------------------------------------------

    def verify_channel(self) -> bool:
        """Verify the OAuth token resolves to the expected brand channel.

        When ``expected_channel_id`` is set (from the constructor or
        ``{PREFIX}_YT_CHANNEL_ID`` env var), this confirms the refresh token
        is bound to the correct channel.  Prevents cross-publishing when
        tokens are misconfigured.

        Returns:
            ``True`` if verified (or if no expected channel is configured).
            ``False`` if the token resolves to a different channel.
        """
        if not self._expected_channel_id:
            return True  # No expectation set — skip verification

        try:
            token = self._get_access_token()
        except Exception as exc:
            logger.error(
                "YouTube: channel verification failed — cannot get access token: %s",
                exc,
            )
            return False

        actual = self._get_channel_id(token)
        if not actual:
            logger.error(
                "YouTube: channel verification failed — could not resolve channel ID"
            )
            return False

        if actual != self._expected_channel_id:
            logger.error(
                "YouTube: CHANNEL MISMATCH — token resolves to %s but expected %s. "
                "Refusing to publish to wrong channel.",
                actual,
                self._expected_channel_id,
            )
            return False

        logger.info("YouTube: channel verified — %s", actual)
        return True

    # ------------------------------------------------------------------
    # Publisher protocol
    # ------------------------------------------------------------------

    def publish(self, payload: PublishPayload) -> PublishResult:
        """Upload a video as a YouTube Short (or regular video).

        Args:
            payload: Contains media paths, caption, hook, hashtags, and optional
                     :class:`~genlab_core.platforms.models.YouTubeSpecific` config.

        Returns:
            :class:`~genlab_core.platforms.models.PublishResult`.
        """
        if not payload.media_paths:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="No media paths provided — video is required for YouTube upload",
            )

        video_path = payload.media_paths[0]

        # Build title: prefer YouTubeSpecific.shorts_title > hook > caption
        yt_specific = payload.platform_specific
        title = ""
        category_id = "28"
        privacy = "public"
        extra_tags: list[str] = []

        if isinstance(yt_specific, YouTubeSpecific):
            title = yt_specific.shorts_title.strip()
            category_id = yt_specific.category_id or "28"
            privacy = yt_specific.privacy_status or "public"
            extra_tags = list(yt_specific.tags)

        if not title:
            title = payload.hook.strip() if payload.hook else ""
        if not title:
            title = (payload.caption.split("\n")[0].strip() if payload.caption else "")
        if not title:
            title = "YouTube Short"

        # Ensure #Shorts tag for algorithm classification
        if "#shorts" not in title.lower():
            title = f"{title} #Shorts"

        # Truncate to YouTube's 100-char limit
        title = title[:_TITLE_MAX_LEN]

        # Build description from caption + hashtags
        hashtags_str = " ".join(payload.hashtags) if payload.hashtags else ""
        description_parts = []
        if payload.caption:
            description_parts.append(payload.caption.strip())
        if hashtags_str:
            description_parts.append(hashtags_str)
        description = "\n\n".join(description_parts)[:5000]

        # Tags from hashtags field
        tags = [h.lstrip("#") for h in payload.hashtags if h.startswith("#")]
        tags.extend(extra_tags)
        if "Shorts" not in tags:
            tags.append("Shorts")

        # Quota gate — refuse upload if near daily limit
        if self._quota_tracker is not None:
            if not self._quota_tracker.can_upload():
                qs = self._quota_tracker.status()
                logger.error(
                    "YouTube quota gate blocked upload: %d/%d units (%d uploads today)",
                    qs["used"],
                    10_000,
                    qs["upload_count"],
                )
                return PublishResult(
                    platform=self.platform_id,
                    success=False,
                    error=f"YouTube quota near limit ({qs['used']}/10000 units)",
                )

        try:
            video_id = self._upload_video(
                video_path=str(video_path),
                title=title,
                description=description,
                tags=tags,
                category_id=category_id,
                privacy=privacy,
            )
        except Exception as exc:
            logger.error("YouTube: upload failed: %s", exc)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=f"Upload failed: {exc}",
            )

        if video_id is None:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="YouTube upload returned no video ID",
            )

        # Record quota usage after successful upload
        if self._quota_tracker is not None:
            try:
                self._quota_tracker.record("upload")
            except Exception as exc:
                logger.warning("YouTube quota recording failed (non-fatal): %s", exc)

        return PublishResult(
            platform=self.platform_id,
            success=True,
            post_id=video_id,
            post_url=f"https://youtube.com/shorts/{video_id}",
            raw_response={"video_id": video_id, "title": title},
        )

    def _upload_video(
        self,
        *,
        video_path: str,
        title: str,
        description: str,
        tags: list[str],
        category_id: str,
        privacy: str,
    ) -> str | None:
        """Perform the resumable upload via YouTube Data API v3.

        Returns the video ID on success, raises on failure.
        """
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,
        )

        service = self._get_data_service()
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        # Resumable upload loop with per-chunk retry (3 attempts, exponential backoff)
        MAX_CHUNKS = 200
        CHUNK_RETRIES = 3
        response = None
        for _chunk_num in range(MAX_CHUNKS):
            for chunk_attempt in range(CHUNK_RETRIES):
                try:
                    status, response = request.next_chunk()
                    break  # chunk succeeded
                except Exception as chunk_exc:
                    if chunk_attempt == CHUNK_RETRIES - 1:
                        raise  # exhausted retries — propagate
                    wait = 2 ** chunk_attempt
                    logger.warning(
                        "YouTube chunk upload retry %d/%d in %ds: %s",
                        chunk_attempt + 1,
                        CHUNK_RETRIES,
                        wait,
                        chunk_exc,
                    )
                    time.sleep(wait)
            if response is not None:
                break
            if status:
                pct = int(status.progress() * 100) if callable(status.progress) else 0
                logger.info("YouTube upload progress: %d%%", pct)
        else:
            raise RuntimeError(f"YouTube upload stalled after {MAX_CHUNKS} chunks")

        if response is None:
            raise RuntimeError("YouTube upload completed with no response")

        video_id = response.get("id", "")
        if not video_id:
            raise RuntimeError(f"YouTube upload response missing 'id': {response}")

        logger.info("YouTube video uploaded: %s", video_id)
        return video_id

    # ------------------------------------------------------------------
    # Engageable protocol
    # ------------------------------------------------------------------

    def post_reply(
        self, parent_id: str, text: str, *, context_id: str = ""
    ) -> bool:
        """Post a reply to a YouTube comment.

        Args:
            parent_id: The comment ID to reply to.
            text: Reply text.
            context_id: Optional video ID (used for logging only).

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        try:
            token = self._get_access_token()
            resp = requests.post(
                f"{_API_BASE}/comments",
                params={"part": "snippet"},
                json={
                    "snippet": {
                        "parentId": parent_id,
                        "textOriginal": text,
                    }
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            resp.raise_for_status()
            comment_id = resp.json().get("id", "")
            logger.info(
                "YouTube: reply posted to comment %s on video %s (reply id=%s)",
                parent_id,
                context_id,
                comment_id,
            )
            return True
        except Exception as exc:
            logger.warning(
                "YouTube: failed to post reply to comment %s: %s", parent_id, exc
            )
            return False

    def post_comment(self, video_id: str, text: str) -> str | None:
        """Post a top-level comment on a YouTube video.

        This uses the ``commentThreads.insert`` endpoint (different from
        ``post_reply`` which uses ``comments.insert``).  Useful for posting
        first-reply comments with links or pinned engagement questions.

        Args:
            video_id: YouTube video ID to comment on.
            text: Comment text.

        Returns:
            Comment thread ID on success, ``None`` on failure.
        """
        try:
            token = self._get_access_token()
            resp = requests.post(
                f"{_API_BASE}/commentThreads",
                params={"part": "snippet"},
                json={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {"textOriginal": text},
                        },
                    }
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            comment_id = data.get("id", "")
            logger.info(
                "YouTube: top-level comment posted on %s (thread id=%s)",
                video_id,
                comment_id,
            )
            return comment_id
        except Exception as exc:
            logger.warning(
                "YouTube: failed to post comment on video %s: %s", video_id, exc
            )
            return None

    def like(self, target_id: str, *, context_id: str = "") -> bool:
        """No-op: YouTube Data API does not support liking comments programmatically.

        Always returns ``True`` to keep the engagement pipeline happy.
        """
        logger.debug(
            "YouTube: like() is a no-op (API unsupported for comment likes) — target=%s",
            target_id,
        )
        return True

    # ------------------------------------------------------------------
    # Trackable protocol
    # ------------------------------------------------------------------

    def get_metrics(
        self, video_id: str, published_at: datetime
    ) -> PlatformMetrics | None:
        """Fetch video-level analytics from the YouTube Analytics API.

        Returns ``None`` if the video was published < 48h ago (data lag guard),
        the API call fails, or no data is available yet.

        Args:
            video_id: YouTube video ID.
            published_at: UTC datetime when the video was published.

        Returns:
            :class:`~genlab_core.platforms.models.PlatformMetrics` or ``None``.
        """
        now = datetime.now(timezone.utc)
        pub_utc = (
            published_at.replace(tzinfo=timezone.utc)
            if published_at.tzinfo is None
            else published_at
        )
        age_hours = (now - pub_utc).total_seconds() / 3600

        if age_hours < _MIN_AGE_HOURS:
            logger.debug(
                "YouTube: skip metrics for %s — too recent (%.1fh < %dh)",
                video_id,
                age_hours,
                _MIN_AGE_HOURS,
            )
            return None

        start_date = pub_utc.strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        try:
            # Fetch channel ID to scope analytics query
            token = self._get_access_token()
            channel_id = self._get_channel_id(token)
            if not channel_id:
                logger.warning(
                    "YouTube: cannot fetch metrics — channel ID unavailable"
                )
                return None

            service = self._get_analytics_service()
            response = (
                service.reports()
                .query(
                    ids=f"channel=={channel_id}",
                    startDate=start_date,
                    endDate=end_date,
                    metrics=_ANALYTICS_METRICS,
                    filters=f"video=={video_id}",
                    dimensions="video",
                )
                .execute()
            )

            rows = response.get("rows", [])
            if not rows:
                logger.debug("YouTube: no analytics data for %s", video_id)
                return None

            row = rows[0]
            headers = [col.get("name") for col in response.get("columnHeaders", [])]
            data = dict(zip(headers, row))

            watch_minutes = float(data.get("estimatedMinutesWatched", 0))
            avg_view_duration_s = float(data.get("averageViewDuration", 0))
            avg_view_pct = float(data.get("averageViewPercentage", 0))
            subscribers_gained = int(data.get("subscribersGained", 0))

            return PlatformMetrics(
                views=int(data.get("views", 0)),
                likes=int(data.get("likes", 0)),
                comments=int(data.get("comments", 0)),
                shares=int(data.get("shares", 0)),
                extra={
                    "watch_minutes": watch_minutes,
                    "avg_view_duration_s": avg_view_duration_s,
                    "avg_view_pct": avg_view_pct,
                    "subscribers_gained": subscribers_gained,
                },
            )

        except Exception as exc:
            logger.warning(
                "YouTube: failed to fetch metrics for %s: %s", video_id, exc
            )
            return None

    # ------------------------------------------------------------------
    # Video metadata management
    # ------------------------------------------------------------------

    def update_video_metadata(
        self,
        video_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Update the title and/or description of an existing video.

        Fetches the current snippet first to preserve fields that are not
        being changed, then issues a ``videos.update`` PUT request.

        Args:
            video_id: YouTube video ID.
            title: New title (max 100 chars).  ``None`` = no change.
            description: New description (max 5000 chars).  ``None`` = no change.

        Returns:
            Dict with ``video_id`` and ``title`` on success, ``None`` on failure.
        """
        if title is None and description is None:
            logger.warning("YouTube: update_video_metadata called with no changes")
            return None

        try:
            token = self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Fetch current snippet to preserve unchanged fields
            get_resp = requests.get(
                f"{_API_BASE}/videos",
                params={"part": "snippet", "id": video_id},
                headers=headers,
                timeout=30,
            )
            get_resp.raise_for_status()
            items = get_resp.json().get("items", [])
            if not items:
                logger.warning(
                    "YouTube: video %s not found for metadata update", video_id
                )
                return None

            snippet = items[0]["snippet"]
            if title is not None:
                snippet["title"] = title[:_TITLE_MAX_LEN]
            if description is not None:
                snippet["description"] = description[:5000]

            update_resp = requests.put(
                f"{_API_BASE}/videos",
                params={"part": "snippet"},
                json={"id": video_id, "snippet": snippet},
                headers=headers,
                timeout=30,
            )
            update_resp.raise_for_status()
            logger.info("YouTube: video %s metadata updated", video_id)
            return {"video_id": video_id, "title": snippet["title"]}
        except Exception as exc:
            logger.warning(
                "YouTube: metadata update failed for %s: %s", video_id, exc
            )
            return None

    # ------------------------------------------------------------------
    # HealthCheckable protocol
    # ------------------------------------------------------------------

    def check_token_health(self) -> TokenStatus:
        """Verify OAuth2 credentials by attempting a token refresh.

        Returns:
            :class:`~genlab_core.platforms.models.TokenStatus` with
            ``valid=True`` when the refresh succeeds.
        """
        try:
            self._access_token = None  # Force a fresh refresh
            self._token_refreshed_at = 0.0
            self._get_access_token()
            return TokenStatus(
                valid=True,
                platform=self.platform_id,
                expires_at=None,  # Tokens auto-refresh; no fixed expiry tracked
                needs_refresh=False,
                message="YouTube OAuth2 token refreshed successfully",
            )
        except Exception as exc:
            return TokenStatus(
                valid=False,
                platform=self.platform_id,
                expires_at=None,
                needs_refresh=True,
                message=f"YouTube token refresh failed: {exc}",
            )
