"""TikTok publisher — Content Posting API v2.

Uploads and publishes video content to TikTok using the FILE_UPLOAD path
(more reliable than PULL_FROM_URL). Handles chunked binary upload with
automatic token refresh.

IMPORTANT: TikTok requires a compliance audit before public posting.
Until TIKTOK_AUDIT_APPROVED=true, all posts default to SELF_ONLY.

Flow:
  1. Ensure access token is fresh (24hr lifetime)
  2. Query creator info for max video duration
  3. Init upload → get upload_url and publish_id
  4. Upload video in chunks (10MB each)
  5. Poll publish status until complete or failed

Auth env vars:
  TIKTOK_CLIENT_KEY       — app client key
  TIKTOK_CLIENT_SECRET    — app client secret
  TIKTOK_ACCESS_TOKEN     — user access token (24hr expiry)
  TIKTOK_REFRESH_TOKEN    — refresh token (1yr, rotates on every refresh)
  TIKTOK_TOKEN_ISSUED_AT  — ISO timestamp of last token refresh
  TIKTOK_AUDIT_APPROVED   — "true" once compliance audit passes

Usage:
    from genlab_core.publishing.tiktok_client import TikTokClient
    client = TikTokClient()
    result = client.publish_video("/tmp/rendered/video.mp4", "Check this out #gaming #fyp")
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

import requests

from genlab_core.settings import settings

logger = logging.getLogger(__name__)

TIKTOK_BASE_URL = "https://open.tiktokapis.com/v2"
TIKTOK_MAX_CAPTION = 2200
TIKTOK_TITLE_LIMIT = 150  # First 150 chars show before "more"


class TikTokClient:
    """Publishes video content to TikTok via the Content Posting API v2."""

    CHUNK_SIZE_BYTES = 10 * 1024 * 1024  # 10MB — within 5-64MB range

    def __init__(
        self,
        client_key: str | None = None,
        client_secret: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        audit_approved: bool | None = None,
    ):
        self._client_key = client_key or getattr(settings, "tiktok_client_key", None)
        self._client_secret = client_secret or getattr(settings, "tiktok_client_secret", None)
        self._access_token = access_token or getattr(settings, "tiktok_access_token", None)
        self._refresh_token = refresh_token or getattr(settings, "tiktok_refresh_token", None)

        if audit_approved is not None:
            self._audit_approved = audit_approved
        else:
            raw = getattr(settings, "tiktok_audit_approved", "false")
            self._audit_approved = str(raw).lower() == "true"

        if not self._access_token:
            raise ValueError(
                "TIKTOK_ACCESS_TOKEN not set. "
                "Configure it in .env or pass access_token to TikTokClient."
            )

    @property
    def _privacy_level(self) -> str:
        return "PUBLIC_TO_EVERYONE" if self._audit_approved else "SELF_ONLY"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def publish_video(
        self,
        video_path: str,
        caption: str,
        *,
        disable_duet: bool = False,
        disable_stitch: bool = False,
        disable_comment: bool = False,
    ) -> dict[str, Any]:
        """Upload and publish a video to TikTok.

        Returns {"publish_id": str, "status": str}.
        """
        # Step 1: Ensure access token is fresh
        self._ensure_token_fresh()

        # Step 2: Query creator info for max duration
        max_duration = self._query_max_duration()

        # Step 3: Validate video duration
        video_duration = self._get_video_duration(video_path)
        if video_duration and max_duration and video_duration > max_duration:
            raise ValueError(
                f"Video duration ({video_duration:.1f}s) exceeds TikTok max "
                f"({max_duration}s) for this creator account"
            )

        file_size = os.path.getsize(video_path)
        total_chunks = math.ceil(file_size / self.CHUNK_SIZE_BYTES)

        if len(caption) > TIKTOK_MAX_CAPTION:
            caption = caption[: TIKTOK_MAX_CAPTION - 1] + "…"
            logger.warning("[TIKTOK] Caption truncated to %d chars", TIKTOK_MAX_CAPTION)

        # Step 4: Init upload
        init_body = {
            "post_info": {
                "title": caption[:TIKTOK_TITLE_LIMIT],
                "description": caption,
                "privacy_level": self._privacy_level,
                "disable_duet": disable_duet,
                "disable_stitch": disable_stitch,
                "disable_comment": disable_comment,
                "video_cover_timestamp_ms": 1000,
                "brand_content_toggle": False,
                "brand_organic_toggle": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": self.CHUNK_SIZE_BYTES,
                "total_chunk_count": total_chunks,
            },
        }

        init_resp = requests.post(
            f"{TIKTOK_BASE_URL}/post/publish/video/init/",
            headers=self._headers(),
            json=init_body,
            timeout=30,
        )
        init_resp.raise_for_status()
        init_data = init_resp.json().get("data", {})
        publish_id = init_data.get("publish_id")
        upload_url = init_data.get("upload_url")

        if not publish_id or not upload_url:
            raise RuntimeError(
                f"TikTok init failed — missing publish_id or upload_url: {init_data}"
            )

        logger.info(
            "[TIKTOK] Init upload: publish_id=%s, %d chunks, privacy=%s",
            publish_id,
            total_chunks,
            self._privacy_level,
        )

        # Step 5: Upload chunks
        self._upload_chunks(upload_url, video_path)

        # Step 6: Poll for completion
        status = self._poll_status(publish_id)

        logger.info("[TIKTOK] Published: publish_id=%s, status=%s", publish_id, status)
        return {"publish_id": publish_id, "status": status}

    def _upload_chunks(self, upload_url: str, video_path: str) -> None:
        """Upload video file in chunks via PUT requests."""
        file_size = os.path.getsize(video_path)
        total_chunks = math.ceil(file_size / self.CHUNK_SIZE_BYTES)
        start_time = time.monotonic()

        with open(video_path, "rb") as f:
            for chunk_idx in range(total_chunks):
                chunk_start = chunk_idx * self.CHUNK_SIZE_BYTES
                chunk_data = f.read(self.CHUNK_SIZE_BYTES)
                chunk_end = chunk_start + len(chunk_data) - 1

                elapsed = time.monotonic() - start_time
                if elapsed > 2700:  # 45 minutes
                    logger.warning("[TIKTOK] Upload taking >45min — upload_url may expire at 1hr")

                resp = requests.put(
                    upload_url,
                    headers={
                        "Content-Range": f"bytes {chunk_start}-{chunk_end}/{file_size}",
                        "Content-Length": str(len(chunk_data)),
                        "Content-Type": "video/mp4",
                    },
                    data=chunk_data,
                    timeout=120,
                )
                resp.raise_for_status()

                rate_mbps = (chunk_end + 1) / (1024 * 1024) / elapsed if elapsed > 0 else 0
                logger.info(
                    "[TIKTOK] Chunk %d/%d uploaded (%.1f MB/s)",
                    chunk_idx + 1,
                    total_chunks,
                    rate_mbps,
                )

    def _poll_status(self, publish_id: str, max_wait_seconds: int = 300) -> str:
        """Poll until PUBLISH_COMPLETE, FAILED, or timeout."""
        poll_interval = 10
        elapsed = 0

        while elapsed < max_wait_seconds:
            resp = requests.post(
                f"{TIKTOK_BASE_URL}/post/publish/status/fetch/",
                headers=self._headers(),
                json={"publish_id": publish_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            status = data.get("status", "UNKNOWN")

            if status == "PUBLISH_COMPLETE":
                return status
            if status == "FAILED":
                error_code = data.get("fail_reason", "unknown")
                raise RuntimeError(f"TikTok publish failed: {error_code} (publish_id={publish_id})")

            logger.debug("[TIKTOK] Status: %s (elapsed %ds)", status, elapsed)
            time.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(
            f"TikTok publish timed out after {max_wait_seconds}s (publish_id={publish_id})"
        )

    def _ensure_token_fresh(self) -> None:
        """Refresh access token if it's approaching expiry (24hr lifetime)."""
        issued_at_str = getattr(settings, "tiktok_token_issued_at", None)
        if not issued_at_str:
            logger.debug("[TIKTOK] TIKTOK_TOKEN_ISSUED_AT not set — skipping refresh check")
            return

        try:
            issued_at = datetime.fromisoformat(str(issued_at_str))
        except (ValueError, TypeError):
            logger.warning("[TIKTOK] Cannot parse TIKTOK_TOKEN_ISSUED_AT: %s", issued_at_str)
            return

        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=UTC)

        age_hours = (datetime.now(UTC) - issued_at).total_seconds() / 3600
        if age_hours >= 23:
            logger.info("[TIKTOK] Access token is %.1f hours old — refreshing", age_hours)
            self._refresh_access_token()

    def _refresh_access_token(self) -> None:
        """Refresh using the refresh token. CRITICAL: TikTok rotates refresh tokens."""
        if not self._client_key or not self._client_secret:
            raise ValueError(
                "TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET required for token refresh"
            )
        if not self._refresh_token:
            raise ValueError("TIKTOK_REFRESH_TOKEN not set — cannot refresh access token")

        resp = requests.post(
            f"{TIKTOK_BASE_URL}/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": self._client_key,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token")

        if not new_access:
            redacted = {k: "***" if "token" in k.lower() else v for k, v in data.items()}
            raise RuntimeError(
                f"TikTok token refresh failed — no access_token in response: {redacted}"
            )

        self._access_token = new_access

        # CRITICAL: TikTok rotates refresh tokens on every refresh
        if new_refresh:
            self._refresh_token = new_refresh
            logger.info("[TIKTOK] Refresh token rotated — store the new one")
        else:
            logger.warning("[TIKTOK] No new refresh_token in response — keeping old one")

        logger.info("[TIKTOK] Access token refreshed successfully")

    def _query_max_duration(self) -> int | None:
        """Query creator info for max video post duration."""
        try:
            resp = requests.post(
                f"{TIKTOK_BASE_URL}/post/publish/creator_info/query/",
                headers=self._headers(),
                json={},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            data.get("creator_avatar_url") and data  # has data
            max_dur = data.get("max_video_post_duration_sec")
            if max_dur:
                logger.info("[TIKTOK] Creator max duration: %ds", max_dur)
            return max_dur
        except Exception as e:
            logger.warning("[TIKTOK] Failed to query creator info: %s", e)
            return None

    def _get_video_duration(self, video_path: str) -> float | None:
        """Get video duration in seconds using ffprobe."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_streams",
                    video_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None
            info = json.loads(result.stdout)
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video":
                    dur = stream.get("duration")
                    if dur:
                        return float(dur)
            return None
        except Exception as e:
            logger.warning("[TIKTOK] ffprobe failed: %s", e)
            return None

    def get_video_insights(self, video_id: str) -> dict[str, Any]:
        """Fetch performance metrics for a published video."""
        resp = requests.get(
            f"{TIKTOK_BASE_URL}/video/query/",
            headers=self._headers(),
            params={
                "fields": "id,title,create_time,cover_image_url,share_url,"
                "view_count,like_count,comment_count,share_count",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        videos = data.get("videos", [])
        return videos[0] if videos else {}
