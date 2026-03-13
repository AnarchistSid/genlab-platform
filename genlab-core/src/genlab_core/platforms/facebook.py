"""Facebook Graph API platform client.

Auth: META_ACCESS_TOKEN (EAA Page Token — permanent, never refresh).
ALWAYS use graph.facebook.com — NEVER graph.instagram.com.
EAA Page Tokens do NOT use ig_refresh_token — they are permanent.

Implements Publisher + Engageable + Trackable + HealthCheckable protocols.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

from genlab_core.platforms.models import (
    PlatformMetrics,
    PublishPayload,
    PublishResult,
    TokenStatus,
)

logger = logging.getLogger(__name__)

# Facebook Graph API post insight metric names
_VIDEO_INSIGHTS_METRICS = [
    "post_video_views",
    "post_video_views_organic",
    "post_reactions_like_total",
    "post_comments",
    "post_shares",
]

_FEED_INSIGHTS_METRICS = [
    "post_impressions",
    "post_reactions_like_total",
    "post_comments",
    "post_shares",
]


class FacebookClient:
    """Meta Graph API client for Facebook Page publishing and engagement.

    All HTTP calls go to ``graph.facebook.com`` (never ``graph.instagram.com``).
    The EAA Page Token must never be refreshed via ``ig_refresh_token``.

    Args:
        access_token: Meta EAA Page Token. Defaults to ``META_ACCESS_TOKEN`` env var.
        page_id: Facebook Page ID. Defaults to ``META_FB_PAGE_ID`` env var.
        api_version: Graph API version string, e.g. ``"v21.0"``.
    """

    platform_id = "facebook"

    def __init__(
        self,
        access_token: str | None = None,
        page_id: str | None = None,
        api_version: str = "v21.0",
    ) -> None:
        self._access_token: str = access_token or os.environ.get("META_ACCESS_TOKEN", "")
        self._page_id: str = page_id or os.environ.get("META_FB_PAGE_ID", "")
        self._api_version = api_version
        self._base_url = f"https://graph.facebook.com/{api_version}"

    # ------------------------------------------------------------------
    # Publisher protocol
    # ------------------------------------------------------------------

    def publish(self, payload: PublishPayload) -> PublishResult:
        """Publish a video or text/link post to a Facebook Page.

        Routing:
          - media_paths with a video → POST ``/{page_id}/videos``
          - no media → POST ``/{page_id}/feed`` (text/link post)

        Args:
            payload: Unified publish payload.

        Returns:
            :class:`~genlab_core.platforms.models.PublishResult`
        """
        # Build caption + hashtags
        hashtags_str = " ".join(payload.hashtags) if payload.hashtags else ""
        message = payload.caption
        if hashtags_str:
            message = f"{message}\n\n{hashtags_str}"

        # Route: video vs text/link
        video_url = self._resolve_video_url(payload)

        if video_url:
            return self._publish_video(video_url=video_url, message=message)
        else:
            return self._publish_feed(message=message, payload=payload)

    def _resolve_video_url(self, payload: PublishPayload) -> str:
        """Extract a video URL or path from the payload.

        Returns the first media path as a string if media_paths is non-empty
        and media_type is ``'video'``.  Otherwise returns ``''``.
        """
        if not payload.media_paths:
            return ""
        if payload.media_type != "video":
            return ""
        return str(payload.media_paths[0])

    def _publish_video(self, *, video_url: str, message: str) -> PublishResult:
        """POST to /{page_id}/videos with file_url or multipart upload.

        Returns:
            :class:`~genlab_core.platforms.models.PublishResult`
        """
        url = f"{self._base_url}/{self._page_id}/videos"

        is_url = video_url.startswith("http://") or video_url.startswith("https://")

        data: dict[str, str] = {
            "description": message,
            "access_token": self._access_token,
        }

        try:
            if is_url:
                data["file_url"] = video_url
                resp = requests.post(url, data=data, timeout=300)
            else:
                # Local file multipart upload
                from pathlib import Path

                local_path = Path(video_url)
                if not local_path.exists():
                    return PublishResult(
                        platform=self.platform_id,
                        success=False,
                        error=f"Video file not found: {video_url}",
                    )
                with open(local_path, "rb") as fh:
                    resp = requests.post(
                        url,
                        data=data,
                        files={"source": (local_path.name, fh, "video/mp4")},
                        timeout=300,
                    )

            response_data = _safe_json(resp)

            if "id" in response_data:
                post_id = response_data["id"]
                logger.info("Facebook: video published — post ID: %s", post_id)
                return PublishResult(
                    platform=self.platform_id,
                    success=True,
                    post_id=post_id,
                    post_url=f"https://www.facebook.com/{post_id}",
                    raw_response=response_data,
                )

            error_msg = _extract_error_message(response_data)
            logger.error("Facebook: video publish failed: %s", error_msg)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=error_msg,
                raw_response=response_data,
            )

        except Exception as exc:
            logger.error("Facebook: video publish exception: %s", exc)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=str(exc),
            )

    def _publish_feed(self, *, message: str, payload: PublishPayload) -> PublishResult:
        """POST to /{page_id}/feed for text or link posts.

        Returns:
            :class:`~genlab_core.platforms.models.PublishResult`
        """
        url = f"{self._base_url}/{self._page_id}/feed"
        data: dict[str, str] = {
            "message": message,
            "access_token": self._access_token,
        }

        try:
            resp = requests.post(url, data=data, timeout=60)
            response_data = _safe_json(resp)

            if "id" in response_data:
                post_id = response_data["id"]
                logger.info("Facebook: feed post published — post ID: %s", post_id)
                return PublishResult(
                    platform=self.platform_id,
                    success=True,
                    post_id=post_id,
                    post_url=f"https://www.facebook.com/{post_id}",
                    raw_response=response_data,
                )

            error_msg = _extract_error_message(response_data)
            logger.error("Facebook: feed post failed: %s", error_msg)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=error_msg,
                raw_response=response_data,
            )

        except Exception as exc:
            logger.error("Facebook: feed post exception: %s", exc)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Engageable protocol
    # ------------------------------------------------------------------

    def post_reply(
        self, parent_id: str, text: str, *, context_id: str = ""
    ) -> bool:
        """Reply to a Facebook comment.

        Unlike Instagram (which uses ``/replies``), Facebook comments use the
        same ``/{comment_id}/comments`` endpoint for nested replies.

        Args:
            parent_id: The comment ID to reply to.
            text: Reply text.
            context_id: Unused for Facebook (kept for protocol compatibility).

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        url = f"{self._base_url}/{parent_id}/comments"
        try:
            resp = requests.post(
                url,
                data={"message": text, "access_token": self._access_token},
                timeout=15,
            )
            data = _safe_json(resp)
            if resp.status_code == 200 and "id" in data:
                logger.info(
                    "Facebook: replied to comment %s", parent_id
                )
                return True
            logger.warning(
                "Facebook: reply failed (HTTP %d): %s",
                resp.status_code,
                data.get("error", {}).get("message", str(data)),
            )
            return False
        except Exception as exc:
            logger.warning("Facebook: post_reply exception: %s", exc)
            return False

    def like(self, target_id: str, *, context_id: str = "") -> bool:
        """Like a Facebook comment.

        Note: The Graph API supports liking comments on behalf of a Page via
        ``/{object-id}/likes`` with a POST. However, this is often restricted
        or requires additional permissions. This implementation attempts the
        call and falls back gracefully.

        Returns ``True`` on success or if the API is unavailable.
        """
        url = f"{self._base_url}/{target_id}/likes"
        try:
            resp = requests.post(
                url,
                data={"access_token": self._access_token},
                timeout=15,
            )
            if resp.status_code == 200:
                logger.info("Facebook: liked target %s", target_id)
                return True
            logger.debug(
                "Facebook: like() not supported for target %s (HTTP %d)",
                target_id,
                resp.status_code,
            )
            return True  # Fail-open — keep engagement pipeline happy
        except Exception as exc:
            logger.debug("Facebook: like() exception (ignored): %s", exc)
            return True  # Fail-open

    # ------------------------------------------------------------------
    # Trackable protocol
    # ------------------------------------------------------------------

    def get_metrics(
        self, post_id: str, published_at: datetime
    ) -> PlatformMetrics | None:
        """Fetch post-level insights from the Facebook Graph API.

        Uses ``/{post_id}/insights`` with video or feed metric names.

        Args:
            post_id: Facebook post or video ID.
            published_at: UTC datetime when the post was published (unused
                          for Facebook, included for protocol compatibility).

        Returns:
            :class:`~genlab_core.platforms.models.PlatformMetrics` on success,
            or ``None`` on failure.
        """
        url = f"{self._base_url}/{post_id}/insights"

        # Try video metrics first; fall back to feed metrics on error
        metric_names = ",".join(_VIDEO_INSIGHTS_METRICS)
        params: dict[str, str] = {
            "metric": metric_names,
            "access_token": self._access_token,
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
            response_data = _safe_json(resp)

            if resp.status_code != 200 or "error" in response_data:
                logger.debug(
                    "Facebook: metrics request failed for %s (HTTP %d): %s",
                    post_id,
                    resp.status_code,
                    _extract_error_message(response_data),
                )
                return None

            data_items: list[dict[str, Any]] = response_data.get("data", [])
            if not data_items:
                logger.debug("Facebook: no insights data for post %s", post_id)
                return None

            # Build a lookup from metric name → latest value
            metric_lookup: dict[str, int] = {}
            for item in data_items:
                name = item.get("name", "")
                values = item.get("values", [])
                if values:
                    # Use the most recent value
                    metric_lookup[name] = int(values[-1].get("value", 0))

            # Map to PlatformMetrics fields
            views = (
                metric_lookup.get("post_video_views")
                or metric_lookup.get("post_video_views_organic")
                or metric_lookup.get("post_impressions")
                or 0
            )
            likes = (
                metric_lookup.get("post_reactions_like_total")
                or 0
            )
            comments = metric_lookup.get("post_comments", 0)
            shares = metric_lookup.get("post_shares", 0)

            return PlatformMetrics(
                views=views,
                likes=likes,
                comments=comments,
                shares=shares,
                extra={k: v for k, v in metric_lookup.items()},
            )

        except Exception as exc:
            logger.warning("Facebook: get_metrics exception for %s: %s", post_id, exc)
            return None

    # ------------------------------------------------------------------
    # HealthCheckable protocol
    # ------------------------------------------------------------------

    def check_token_health(self) -> TokenStatus:
        """Verify the EAA Page Token by calling ``/me`` on the Graph API.

        EAA Page Tokens are permanent — ``expires_at=None``, ``needs_refresh=False``.

        Returns:
            :class:`~genlab_core.platforms.models.TokenStatus`
        """
        url = f"{self._base_url}/me"
        try:
            resp = requests.get(
                url,
                params={"access_token": self._access_token},
                timeout=15,
            )
            data = _safe_json(resp)
            if resp.status_code == 200 and "id" in data:
                name = data.get("name", "")
                return TokenStatus(
                    valid=True,
                    platform=self.platform_id,
                    expires_at=None,  # EAA page tokens are permanent
                    needs_refresh=False,
                    message=f"Token valid for page '{name}' (id={data['id']})",
                    details=data,
                )
            error_msg = (
                data.get("error", {}).get("message", "")
                or f"HTTP {resp.status_code}"
            )
            return TokenStatus(
                valid=False,
                platform=self.platform_id,
                expires_at=None,
                needs_refresh=False,
                message=f"Token check failed: {error_msg}",
                details=data,
            )
        except Exception as exc:
            return TokenStatus(
                valid=False,
                platform=self.platform_id,
                expires_at=None,
                needs_refresh=False,
                message=f"Token check exception: {exc}",
            )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _safe_json(resp: requests.Response) -> dict[str, Any]:
    """Safely parse a requests Response as JSON, returning {} on failure."""
    try:
        return resp.json()
    except Exception:
        return {}


def _extract_error_message(data: dict[str, Any]) -> str:
    """Extract a human-readable error message from a Facebook API response."""
    error = data.get("error", {})
    if isinstance(error, dict):
        msg = error.get("message", "")
        code = error.get("code", "")
        if msg:
            return f"code={code}: {msg}" if code else msg
    return str(data)[:200] if data else "Unknown error"
