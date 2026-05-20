"""Threads platform client — Meta Threads API v1.0.

Implements Publisher + Engageable + HealthCheckable protocols.

Auth env vars:
  THREADS_ACCESS_TOKEN      — long-lived Threads token (60-day expiry)
  THREADS_USER_ID           — numeric Threads user ID
  THREADS_TOKEN_ISSUED_AT   — ISO timestamp of last token issue/refresh
                               (used to determine needs_refresh)

Flow (video):
  1. Create a media container (POST /{user_id}/threads with media_type=VIDEO)
  2. Wait ~30 seconds for Meta to process the video
  3. Publish the container (POST /{user_id}/threads_publish?creation_id=...)

Flow (image or text):
  1. Create a media container
  2. Publish the container immediately (no processing wait)

Flow (reply):
  1. Create a reply container (POST /{user_id}/threads with reply_to_id)
  2. Publish the container

Threads long-lived tokens expire in 60 days.  needs_refresh is set True when
the token is ≥ 50 days old (readable from THREADS_TOKEN_ISSUED_AT).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from genlab_core.platforms.models import PublishPayload, PublishResult, TokenStatus, safe_json as _safe_json
from genlab_core.ratelimit.token_bucket import TokenBucket

logger = logging.getLogger(__name__)

# Threads long-lived token lifecycle constants
_TOKEN_LIFETIME_DAYS = 60
_NEEDS_REFRESH_AFTER_DAYS = 50

# Video processing: Meta needs ~30 s before threads_publish will succeed
_VIDEO_PROCESSING_WAIT = 30



# _safe_json imported from genlab_core.platforms.models


class ThreadsClient:
    """Meta Threads API v1.0 client.

    Implements Publisher + Engageable + HealthCheckable.

    Args:
        access_token: Threads long-lived token.
                      Defaults to ``THREADS_ACCESS_TOKEN`` env var.
        user_id:      Numeric Threads user ID.
                      Defaults to ``THREADS_USER_ID`` env var.
    """

    platform_id = "threads"

    def __init__(
        self,
        access_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._access_token: str = access_token or os.environ.get(
            "THREADS_ACCESS_TOKEN", ""
        )
        self._user_id: str = user_id or os.environ.get("THREADS_USER_ID", "")
        self._base_url = "https://graph.threads.net/v1.0"
        # Threads API: 250 posts/hr. Conservative rate limiter.
        self._rate_limiter = TokenBucket(rate=250 / 3600, burst=10)

    # ------------------------------------------------------------------
    # Publisher protocol
    # ------------------------------------------------------------------

    def publish(self, payload: PublishPayload) -> PublishResult:
        """Publish a post to Threads.

        Routing by ``payload.media_type``:
        - ``"video"``  → VIDEO container + 30 s wait + threads_publish
        - ``"image"``  → IMAGE container + threads_publish
        - ``"text"``   → TEXT container + threads_publish
        - No caption AND no media → failure result

        Returns a :class:`~genlab_core.platforms.models.PublishResult`.
        """
        self.refresh_token_if_needed()

        caption = payload.caption or ""
        media_paths = payload.media_paths or []
        media_type = payload.media_type or "text"

        # Must have at least some text content
        if not caption.strip() and not media_paths:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="No media and no caption — nothing to publish to Threads",
            )

        # Build hashtags into caption
        if payload.hashtags:
            hashtags_str = " ".join(payload.hashtags)
            caption = f"{caption}\n\n{hashtags_str}".strip()

        try:
            if media_type == "video" and media_paths:
                return self._publish_video(caption=caption, media_paths=media_paths)
            elif media_type == "image" and media_paths:
                return self._publish_image(caption=caption, media_paths=media_paths)
            else:
                # Text-only (or fallback when media_type mismatch)
                return self._publish_text(caption=caption)
        except Exception as exc:
            logger.error("Threads: publish error: %s", exc)
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
        """Reply to a Threads post.

        Creates a TEXT reply container with ``reply_to_id``, then publishes it.

        Args:
            parent_id:  The Threads post ID to reply to.
            text:       Reply text.
            context_id: Unused for Threads; kept for protocol compatibility.

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        try:
            container_id = self._create_container(
                media_type="TEXT",
                text=text,
                reply_to_id=parent_id,
            )
            if container_id is None:
                return False

            post_id = self._threads_publish(container_id=container_id)
            if post_id is None:
                return False

            logger.info(
                "Threads: posted reply to %s (reply_id=%s context=%s)",
                parent_id,
                post_id,
                context_id,
            )
            return True
        except Exception as exc:
            logger.warning("Threads: post_reply failed for %s: %s", parent_id, exc)
            return False

    def like(self, target_id: str, *, context_id: str = "") -> bool:
        """Like a Threads post.

        The Threads API does not currently expose a public endpoint for
        programmatic likes.  This is a no-op that returns ``True`` so the
        ``Engageable`` protocol check passes and reply functionality works.

        Args:
            target_id:  The Threads post/comment ID to like.
            context_id: Unused; kept for protocol compatibility.

        Returns:
            ``True`` always (no-op).
        """
        logger.debug(
            "Threads: like() called for %s (no-op — API unsupported)", target_id
        )
        return True

    # ------------------------------------------------------------------
    # HealthCheckable protocol
    # ------------------------------------------------------------------

    def check_token_health(self) -> TokenStatus:
        """Verify the access token by calling ``/me`` on the Threads API.

        Also checks ``THREADS_TOKEN_ISSUED_AT`` to determine if the token
        is approaching the 60-day expiry boundary.  If the token is ≥ 50
        days old, ``needs_refresh`` is set to ``True``.

        Returns:
            :class:`~genlab_core.platforms.models.TokenStatus`.
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
                needs_refresh = self._token_needs_refresh()
                return TokenStatus(
                    valid=True,
                    platform=self.platform_id,
                    expires_at=None,
                    needs_refresh=needs_refresh,
                    message=f"Token valid for account '{name}' (id={data['id']})",
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
    # Private publish helpers
    # ------------------------------------------------------------------

    def _publish_video(self, *, caption: str, media_paths: list) -> PublishResult:
        """VIDEO container → wait → threads_publish."""
        video_url = self._resolve_url(media_paths[0])

        container_id = self._create_container(
            media_type="VIDEO",
            video_url=video_url,
            text=caption,
        )
        if container_id is None:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="Threads: video container creation failed",
            )

        # Poll container status instead of fixed sleep
        container_status = self._poll_container(container_id, max_seconds=120)
        if container_status == "ERROR":
            return PublishResult(platform=self.platform_id, success=False, error="Threads container processing error")
        if container_status == "TIMEOUT":
            return PublishResult(platform=self.platform_id, success=False, error="Threads container processing timeout (120s)")

        post_id = self._threads_publish(container_id=container_id)
        if post_id is None:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="Threads: video threads_publish failed",
            )

        # Fetch real permalink
        post_url = f"https://www.threads.net/post/{post_id}"
        try:
            permalink_resp = requests.get(
                f"{self._base_url}/{post_id}",
                params={"fields": "permalink", "access_token": self._access_token},
                timeout=10,
            )
            if permalink_resp.ok:
                real_url = permalink_resp.json().get("permalink", "")
                if real_url:
                    post_url = real_url
        except Exception:
            pass

        logger.info("Threads: published video post %s — %s", post_id, post_url)
        return PublishResult(
            platform=self.platform_id,
            success=True,
            post_id=post_id,
            post_url=post_url,
        )

    def _publish_image(self, *, caption: str, media_paths: list) -> PublishResult:
        """IMAGE container → threads_publish (no wait needed)."""
        image_url = self._resolve_url(media_paths[0])

        container_id = self._create_container(
            media_type="IMAGE",
            image_url=image_url,
            text=caption,
        )
        if container_id is None:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="Threads: image container creation failed",
            )

        post_id = self._threads_publish(container_id=container_id)
        if post_id is None:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="Threads: image threads_publish failed",
            )

        post_url = self._get_permalink(post_id)
        logger.info("Threads: published image post %s — %s", post_id, post_url)
        return PublishResult(
            platform=self.platform_id,
            success=True,
            post_id=post_id,
            post_url=post_url,
        )

    def _publish_text(self, *, caption: str) -> PublishResult:
        """TEXT container → threads_publish."""
        container_id = self._create_container(
            media_type="TEXT",
            text=caption,
        )
        if container_id is None:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="Threads: text container creation failed",
            )

        post_id = self._threads_publish(container_id=container_id)
        if post_id is None:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="Threads: text threads_publish failed",
            )

        post_url = self._get_permalink(post_id)
        logger.info("Threads: published text post %s — %s", post_id, post_url)
        return PublishResult(
            platform=self.platform_id,
            success=True,
            post_id=post_id,
            post_url=post_url,
        )

    # ------------------------------------------------------------------
    # Private low-level API helpers
    # ------------------------------------------------------------------

    def _create_container(
        self,
        *,
        media_type: str,
        text: str = "",
        video_url: str = "",
        image_url: str = "",
        reply_to_id: str = "",
    ) -> str | None:
        """POST /{user_id}/threads to create a media container.

        Returns the container ID on success, ``None`` on failure.
        """
        self._rate_limiter.acquire()
        url = f"{self._base_url}/{self._user_id}/threads"
        data: dict[str, Any] = {
            "media_type": media_type,
            "access_token": self._access_token,
        }
        if text:
            data["text"] = text
        if video_url:
            data["video_url"] = video_url
        if image_url:
            data["image_url"] = image_url
        if reply_to_id:
            data["reply_to_id"] = reply_to_id

        try:
            resp = requests.post(url, data=data, timeout=30)
            payload = _safe_json(resp)
            if resp.ok and "id" in payload:
                logger.debug("Threads: created %s container %s", media_type, payload["id"])
                return payload["id"]
            error_msg = payload.get("error", {}).get("message", str(payload))
            logger.error("Threads: container creation failed (%s): %s", media_type, error_msg)
            return None
        except Exception as exc:
            logger.error("Threads: container request error: %s", exc)
            return None

    def _threads_publish(self, *, container_id: str) -> str | None:
        """POST /{user_id}/threads_publish to publish a container.

        Returns the published post ID on success, ``None`` on failure.
        """
        url = f"{self._base_url}/{self._user_id}/threads_publish"
        data = {
            "creation_id": container_id,
            "access_token": self._access_token,
        }
        try:
            resp = requests.post(url, data=data, timeout=30)
            payload = _safe_json(resp)
            if resp.ok and "id" in payload:
                return payload["id"]
            error_msg = payload.get("error", {}).get("message", str(payload))
            logger.error("Threads: threads_publish failed: %s", error_msg)
            return None
        except Exception as exc:
            logger.error("Threads: threads_publish request error: %s", exc)
            return None

    def _poll_container(self, container_id: str, max_seconds: int = 120) -> str:
        """Poll Threads container status until FINISHED, ERROR, or timeout."""
        consecutive_errors = 0
        for _ in range(max_seconds // 5):
            try:
                resp = requests.get(
                    f"{self._base_url}/{container_id}",
                    params={"fields": "status", "access_token": self._access_token},
                    timeout=10,
                )
                if resp.ok:
                    consecutive_errors = 0
                    status = resp.json().get("status", "")
                    if status == "FINISHED":
                        return "FINISHED"
                    if status == "ERROR":
                        logger.error(
                            "[Threads] container %s returned ERROR state",
                            container_id[:16],
                        )
                        return "ERROR"
                else:
                    logger.warning(
                        "[Threads] container poll HTTP %d for %s",
                        resp.status_code, container_id[:16],
                    )
            except Exception as exc:
                consecutive_errors += 1
                logger.warning(
                    "[Threads] container poll request error for %s (%d/3): %s",
                    container_id[:16], consecutive_errors, exc,
                )
                if consecutive_errors >= 3:
                    logger.error(
                        "[Threads] container poll gave up after 3 consecutive errors"
                    )
                    return "ERROR"
            time.sleep(5)  # uses module-level time import (mockable in tests)
        return "TIMEOUT"

    def _get_permalink(self, post_id: str) -> str:
        """Fetch the permalink for a published Threads post.

        Returns empty string on any failure (non-critical — post IS
        already published, the permalink is just for dashboard display).
        """
        try:
            resp = requests.get(
                f"{self._base_url}/{post_id}",
                params={"fields": "permalink", "access_token": self._access_token},
                timeout=10,
            )
            if resp.ok:
                return _safe_json(resp).get("permalink", "")
        except Exception as exc:
            logger.debug("[Threads] permalink fetch failed (non-critical): %s", exc)
        return ""

    def _resolve_url(self, path: Path | str) -> str:
        """Return a URL string from a media path.

        If the path is already an HTTP URL, return as-is.
        Otherwise upload to CDN first so the Threads API receives a public URL.
        """
        s = str(path)
        if s.startswith("http"):
            return s
        # If path is not an HTTP URL, upload to CDN first
        from genlab_core.platforms.cdn_upload import upload_to_cdn
        cdn_url = upload_to_cdn(Path(path), require_external=True)
        if cdn_url:
            return cdn_url
        # CDN upload failed — return path as-is (will fail at API level)
        return s

    def _token_needs_refresh(self) -> bool:
        """Return True if the token is >= 50 days old (60-day expiry).

        Reads ``THREADS_TOKEN_ISSUED_AT`` env var. Accepts both:
          - ISO 8601 datetime string ('2026-03-08T12:34:56+00:00')
          - Unix timestamp string ('1773047145')
        Historical .env entries used Unix timestamps; fromisoformat()
        chokes on them and silently returned False here, which is why
        the auto-refresh quietly stopped working in May 2026 (token
        expired 2026-05-08 with no refresh attempt).
        """
        issued_at_str = os.environ.get("THREADS_TOKEN_ISSUED_AT", "")
        if not issued_at_str:
            return False

        issued_at: datetime | None = None
        # 1) ISO 8601
        try:
            issued_at = datetime.fromisoformat(issued_at_str)
        except (ValueError, TypeError):
            pass
        # 2) Unix timestamp fallback
        if issued_at is None:
            try:
                issued_at = datetime.fromtimestamp(int(issued_at_str), tz=UTC)
            except (ValueError, TypeError):
                logger.warning(
                    "[Threads] Cannot parse THREADS_TOKEN_ISSUED_AT=%r as ISO or Unix timestamp — "
                    "auto-refresh disabled until format is fixed.",
                    issued_at_str[:40],
                )
                return False

        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=UTC)

        age_days = (datetime.now(UTC) - issued_at).days
        return age_days >= _NEEDS_REFRESH_AFTER_DAYS

    def refresh_token_if_needed(self) -> bool:
        """Refresh Threads long-lived token if expiring within 7 days.

        Returns True if token is valid (refreshed or not needed).
        Returns False if refresh failed.
        """
        if not hasattr(self, '_token_needs_refresh') or not self._token_needs_refresh():
            return True

        try:
            resp = requests.get(
                "https://graph.threads.net/refresh_access_token",
                params={
                    "grant_type": "th_refresh_token",
                    "access_token": self._access_token,
                },
                timeout=30,
            )
            if resp.ok:
                data = resp.json()
                new_token = data.get("access_token", "")
                if new_token:
                    self._access_token = new_token
                    issued_at = datetime.now(UTC).isoformat()
                    # In-process: refresh env so sibling modules see the new token
                    os.environ["THREADS_ACCESS_TOKEN"] = new_token
                    os.environ["THREADS_TOKEN_ISSUED_AT"] = issued_at
                    # Persist across process boundaries: write back to .env so
                    # subsequent pipeline oneshot runs don't re-read the stale
                    # token and trigger refresh every single time.
                    try:
                        from genlab_core.utils.env_writer import update_env_file
                        update_env_file({
                            "THREADS_ACCESS_TOKEN": new_token,
                            "THREADS_TOKEN_ISSUED_AT": issued_at,
                        })
                    except Exception as exc:
                        logger.warning(
                            "[Threads] Token refreshed but .env persistence failed: %s",
                            exc,
                        )
                    logger.info("[Threads] Token refreshed successfully (expires in %ds)", data.get("expires_in", 0))
                    return True
            logger.warning("[Threads] Token refresh failed: %s", resp.text[:200])
            return False
        except Exception as e:
            logger.warning("[Threads] Token refresh error: %s", e)
            return False
