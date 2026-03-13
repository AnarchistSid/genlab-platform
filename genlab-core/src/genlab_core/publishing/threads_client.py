"""Threads publisher — Meta Threads API v1.0.

Publishes text, image, and video content to Threads using the same Meta
OAuth family as Instagram. Long-lived tokens expire in 60 days but are
indefinitely refreshable before expiry.

Flow (video):
  1. Create a media container (POST /{user_id}/threads)
  2. Wait 30 seconds for Meta to process the video
  3. Publish the container (POST /{user_id}/threads_publish)

Flow (text/image):
  1. Create a media container (POST /{user_id}/threads)
  2. Publish the container immediately (no processing wait)

Auth env vars:
  THREADS_ACCESS_TOKEN   — long-lived token (60-day expiry)
  THREADS_USER_ID        — numeric user ID
  THREADS_TOKEN_ISSUED_AT — ISO timestamp of last token issue/refresh

Usage:
    from genlab_core.publishing.threads_client import ThreadsClient
    client = ThreadsClient()
    result = client.publish_text(text="Check this out")
    result = client.publish_image(image_url="https://cdn.example.com/img.jpg", caption="Look!")
    result = client.publish_video(video_url="https://cdn.example.com/video.mp4", caption="Watch")
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from genlab_core.settings import settings

logger = logging.getLogger(__name__)

THREADS_MAX_CAPTION = 500
THREADS_PROCESSING_WAIT = 30  # seconds — Meta needs time to process video


class ThreadsClient:
    """Publishes text, image, and video content to Threads via the Meta Threads API v1."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        user_id: Optional[str] = None,
        api_version: str = "v1.0",
    ):
        self._access_token = access_token or getattr(settings, "threads_access_token", None)
        self._user_id = user_id or getattr(settings, "threads_user_id", None)
        self._api_version = api_version
        self._base_url = f"https://graph.threads.net/{self._api_version}"

        if not self._access_token:
            raise ValueError(
                "THREADS_ACCESS_TOKEN not set. "
                "Configure it in .env or pass access_token to ThreadsClient."
            )
        if not self._user_id:
            raise ValueError(
                "THREADS_USER_ID not set. "
                "Configure it in .env or pass user_id to ThreadsClient."
            )

    def publish_text(
        self,
        text: str,
        *,
        reply_control: str = "everyone",
    ) -> Dict[str, Any]:
        """Publish a text-only post to Threads.

        Args:
            text: Post text, max 500 characters.
            reply_control: "everyone" | "accounts_you_follow" | "mentioned_only"

        Returns:
            {"threads_id": str, "permalink": str}
        """
        if len(text) > THREADS_MAX_CAPTION:
            text = text[: THREADS_MAX_CAPTION - 1] + "…"
            logger.warning("[THREADS] Text truncated to %d chars", THREADS_MAX_CAPTION)

        # Step 1: Create text container
        container_resp = requests.post(
            f"{self._base_url}/{self._user_id}/threads",
            data={
                "media_type": "TEXT",
                "text": text,
                "reply_control": reply_control,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        container_resp.raise_for_status()
        container_id = container_resp.json()["id"]
        logger.info("[THREADS] Created text container %s", container_id)

        # Step 2: Publish — text containers don't need processing wait
        publish_resp = requests.post(
            f"{self._base_url}/{self._user_id}/threads_publish",
            data={
                "creation_id": container_id,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        publish_resp.raise_for_status()
        threads_id = publish_resp.json()["id"]

        permalink = self._get_permalink(threads_id)
        logger.info("[THREADS] Published text post %s — %s", threads_id, permalink)
        return {"threads_id": threads_id, "permalink": permalink}

    def publish_image(
        self,
        image_url: str,
        caption: str = "",
        *,
        reply_control: str = "everyone",
    ) -> Dict[str, Any]:
        """Publish an image post to Threads.

        Args:
            image_url: Public HTTPS URL to the image (JPEG/PNG).
            caption: Post text, max 500 characters.
            reply_control: "everyone" | "accounts_you_follow" | "mentioned_only"

        Returns:
            {"threads_id": str, "permalink": str}
        """
        if len(caption) > THREADS_MAX_CAPTION:
            caption = caption[: THREADS_MAX_CAPTION - 1] + "…"
            logger.warning("[THREADS] Caption truncated to %d chars", THREADS_MAX_CAPTION)

        # Step 1: Create image container
        container_resp = requests.post(
            f"{self._base_url}/{self._user_id}/threads",
            data={
                "media_type": "IMAGE",
                "image_url": image_url,
                "text": caption,
                "reply_control": reply_control,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        container_resp.raise_for_status()
        container_id = container_resp.json()["id"]
        logger.info("[THREADS] Created image container %s", container_id)

        # Step 2: Publish — image containers don't need processing wait
        publish_resp = requests.post(
            f"{self._base_url}/{self._user_id}/threads_publish",
            data={
                "creation_id": container_id,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        publish_resp.raise_for_status()
        threads_id = publish_resp.json()["id"]

        permalink = self._get_permalink(threads_id)
        logger.info("[THREADS] Published image post %s — %s", threads_id, permalink)
        return {"threads_id": threads_id, "permalink": permalink}

    def publish_video(
        self,
        video_url: str,
        caption: str,
        *,
        reply_control: str = "everyone",
    ) -> Dict[str, Any]:
        """Publish a video to Threads.

        Args:
            video_url: Public HTTPS URL to the rendered MP4.
            caption: Post text, max 500 characters.
            reply_control: "everyone" | "accounts_you_follow" | "mentioned_only"

        Returns:
            {"threads_id": str, "permalink": str}
        """
        if len(caption) > THREADS_MAX_CAPTION:
            caption = caption[: THREADS_MAX_CAPTION - 1] + "…"
            logger.warning("[THREADS] Caption truncated to %d chars", THREADS_MAX_CAPTION)

        # Step 1: Create media container
        container_resp = requests.post(
            f"{self._base_url}/{self._user_id}/threads",
            data={
                "media_type": "VIDEO",
                "video_url": video_url,
                "text": caption,
                "reply_control": reply_control,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        container_resp.raise_for_status()
        container_id = container_resp.json()["id"]
        logger.info("[THREADS] Created container %s", container_id)

        # Step 2: Wait for Meta to process the video
        # Meta always returns IN_PROGRESS during this window — polling is useless
        time.sleep(THREADS_PROCESSING_WAIT)

        # Step 3: Publish the container
        publish_resp = requests.post(
            f"{self._base_url}/{self._user_id}/threads_publish",
            data={
                "creation_id": container_id,
                "access_token": self._access_token,
            },
            timeout=30,
        )
        publish_resp.raise_for_status()
        threads_id = publish_resp.json()["id"]

        # Fetch permalink
        permalink = self._get_permalink(threads_id)

        logger.info("[THREADS] Published post %s — %s", threads_id, permalink)
        return {"threads_id": threads_id, "permalink": permalink}

    def get_post_insights(self, threads_id: str) -> Dict[str, Any]:
        """Fetch engagement metrics for a published post."""
        resp = requests.get(
            f"{self._base_url}/{threads_id}/insights",
            params={
                "metric": "views,likes,replies,reposts,quotes",
                "access_token": self._access_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json().get("data", [])

        metrics: Dict[str, int] = {}
        for item in raw:
            name = item.get("name", "")
            values = item.get("values", [{}])
            metrics[name] = values[0].get("value", 0) if values else 0

        return metrics

    def refresh_token(self) -> str:
        """Refresh the long-lived token before it expires. Returns new token."""
        resp = requests.get(
            f"https://graph.threads.net/refresh_access_token",
            params={
                "grant_type": "th_refresh_token",
                "access_token": self._access_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        new_token = data["access_token"]
        self._access_token = new_token
        logger.info("[THREADS] Token refreshed, expires in %s seconds", data.get("expires_in"))
        return new_token

    def _get_permalink(self, threads_id: str) -> str:
        """Fetch the permalink for a published Threads post."""
        resp = requests.get(
            f"{self._base_url}/{threads_id}",
            params={
                "fields": "permalink",
                "access_token": self._access_token,
            },
            timeout=10,
        )
        if resp.ok:
            return resp.json().get("permalink", "")
        return ""


class ThreadsTokenManager:
    """Monitors Threads token expiry and triggers proactive refresh.

    Threads long-lived tokens expire in 60 days but are refreshable
    indefinitely as long as you refresh before expiry. This manager
    refreshes at 45 days (15-day buffer) and alerts at 50 days.
    """

    REFRESH_AT_DAYS = 45
    WARN_AT_DAYS = 50
    CRITICAL_AT_DAYS = 58
    TOKEN_LIFETIME_DAYS = 60

    def __init__(self, client: Optional[ThreadsClient] = None):
        self._client = client

    def check_and_refresh(self) -> Dict[str, Any]:
        """Check token age and refresh if needed.

        Returns {"status": "ok"|"refreshed"|"warning"|"critical", "days_remaining": int}
        """
        issued_at_str = getattr(settings, "threads_token_issued_at", None)
        if not issued_at_str:
            logger.warning("[THREADS] THREADS_TOKEN_ISSUED_AT not set — token age unknown")
            return {"status": "unknown", "days_remaining": -1}

        try:
            issued_at = datetime.fromisoformat(issued_at_str)
        except (ValueError, TypeError):
            logger.warning("[THREADS] Cannot parse THREADS_TOKEN_ISSUED_AT: %s", issued_at_str)
            return {"status": "unknown", "days_remaining": -1}

        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)

        age_days = (datetime.now(timezone.utc) - issued_at).days
        days_remaining = self.TOKEN_LIFETIME_DAYS - age_days

        if age_days >= self.CRITICAL_AT_DAYS:
            logger.error(
                "[THREADS] Token expires in %d days — manual re-auth required immediately",
                days_remaining,
            )
            return {"status": "critical", "days_remaining": days_remaining}

        if age_days >= self.WARN_AT_DAYS:
            logger.warning(
                "[THREADS] Token expires in %d days — refresh recommended",
                days_remaining,
            )
            return {"status": "warning", "days_remaining": days_remaining}

        if age_days >= self.REFRESH_AT_DAYS:
            if self._client:
                self._client.refresh_token()
                logger.info("[THREADS] Token proactively refreshed at %d days old", age_days)
                return {"status": "refreshed", "days_remaining": self.TOKEN_LIFETIME_DAYS}
            else:
                logger.warning("[THREADS] Token is %d days old but no client for refresh", age_days)
                return {"status": "warning", "days_remaining": days_remaining}

        return {"status": "ok", "days_remaining": days_remaining}
