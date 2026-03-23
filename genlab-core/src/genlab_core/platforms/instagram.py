"""Instagram Graph API platform client.

Auth: META_ACCESS_TOKEN (EAA Page Token — permanent, never refresh).
ALWAYS use graph.facebook.com — NEVER graph.instagram.com.

Implements Publisher + Engageable + HealthCheckable protocols.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import requests

from genlab_core.platforms.models import (
    InstagramSpecific,
    PublishPayload,
    PublishResult,
    TokenStatus,
    safe_json as _safe_json,
)

logger = logging.getLogger(__name__)

# Poll config (kept short for tests; real use is fine because it's mocked)
_DEFAULT_MAX_POLL_SECONDS = 120
_POLL_INTERVAL_INITIAL = 5
_POLL_INTERVAL_SLOW = 10
_POLL_SLOWDOWN_AFTER = 30


class InstagramClient:
    """Meta Graph API client for Instagram publishing and engagement.

    All HTTP calls go to ``graph.facebook.com`` (never ``graph.instagram.com``).
    The EAA Page Token must never be refreshed via ``ig_refresh_token``.

    Args:
        access_token: Meta EAA Page Token. Defaults to ``META_ACCESS_TOKEN`` env var.
        ig_user_id: Instagram Business Account ID (FB-scoped).
                    Defaults to ``META_IG_USER_ID`` env var.
        api_version: Graph API version string, e.g. ``"v21.0"``.
        max_poll_seconds: Maximum seconds to wait for container processing.
                          BB uses 600; default is 120.
    """

    platform_id = "instagram"

    def __init__(
        self,
        access_token: str | None = None,
        ig_user_id: str | None = None,
        api_version: str = "v21.0",
        max_poll_seconds: int = _DEFAULT_MAX_POLL_SECONDS,
    ) -> None:
        self._access_token: str = access_token or os.environ.get("META_ACCESS_TOKEN", "")
        self._ig_user_id: str = ig_user_id or os.environ.get("META_IG_USER_ID", "")
        self._api_version = api_version
        self._base_url = f"https://graph.facebook.com/{api_version}"
        self._max_poll_seconds = max_poll_seconds
        self._last_error: str = ""  # Captures detailed error from internal methods

    # ------------------------------------------------------------------
    # Publisher protocol
    # ------------------------------------------------------------------

    def publish(self, payload: PublishPayload) -> PublishResult:
        """Publish a Reel to Instagram.

        Flow:
          1. Validate that at least one media path was supplied.
          2. The caller is expected to have already uploaded the video to a
             public CDN and provided the URL; if ``media_paths`` contains a
             raw local path that starts with ``http`` it is used as-is.
             Otherwise we look for a ``video_url`` hint in
             ``payload.platform_specific`` or fall back to the first path's
             string representation so tests/callers that pass a URL as a
             ``Path`` still work.
          3. Create the REELS container.
          4. Poll the container status until ``FINISHED`` (or ``PUBLISHED``).
          5. POST to ``media_publish``.

        Returns a :class:`~genlab_core.platforms.models.PublishResult`.
        """
        if not payload.media_paths:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="No media paths provided — video is required for Instagram Reels",
            )

        # Resolve the video URL. Instagram requires a public HTTPS URL.
        # If the path is a local file, upload to temp CDN first.
        first_path = payload.media_paths[0]
        video_url = str(first_path)

        if not video_url.startswith("http"):
            from genlab_core.platforms.cdn_upload import upload_to_cdn
            cdn_url = upload_to_cdn(video_url)
            if not cdn_url:
                tunnel = os.environ.get("CLOUDFLARE_TUNNEL_URL", "")
                from pathlib import Path as _Path
                exists = _Path(video_url).exists() if video_url else False
                return PublishResult(
                    platform=self.platform_id,
                    success=False,
                    error=(
                        f"CDN upload failed for Instagram"
                        f" (file_exists={exists}, tunnel={'set' if tunnel else 'unset'},"
                        f" path={video_url[-60:]})"
                    ),
                )
            video_url = cdn_url

        # Build caption with hashtags (avoid duplication — caption may already
        # contain inline hashtags from the writing stage)
        caption = payload.caption
        if payload.hashtags:
            existing_tags = set(t.lower() for t in re.findall(r"#\w+", caption))
            new_tags = [t for t in payload.hashtags if t.lower() not in existing_tags]
            if new_tags:
                caption = f"{caption}\n\n{' '.join(new_tags)}"
        caption = caption[:2200]

        # Platform-specific options
        ig_specific = payload.platform_specific
        share_to_feed: bool = True
        cover_url: str = ""
        if isinstance(ig_specific, InstagramSpecific):
            share_to_feed = ig_specific.share_to_feed
            cover_url = ig_specific.cover_url

        self._last_error = ""
        post_id = self._publish_reel(
            video_url=video_url,
            caption=caption,
            share_to_feed=share_to_feed,
            cover_url=cover_url,
            max_poll_seconds=self._max_poll_seconds,
        )

        if post_id is None:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=self._last_error or "Instagram Reel publish failed — unknown error",
            )

        # Fetch the real permalink (numeric Graph IDs don't resolve as /p/ URLs)
        real_url = f"https://www.instagram.com/reel/{post_id}/"
        try:
            permalink_resp = self._graph_get(f"/{post_id}", params={"fields": "permalink"})
            if permalink_resp and "permalink" in permalink_resp:
                real_url = permalink_resp["permalink"]
        except Exception:
            pass  # fall back to numeric ID URL

        return PublishResult(
            platform=self.platform_id,
            success=True,
            post_id=post_id,
            post_url=real_url,
        )

    # ------------------------------------------------------------------
    # Engageable protocol
    # ------------------------------------------------------------------

    def post_reply(
        self, parent_id: str, text: str, *, context_id: str = ""
    ) -> bool:
        """Reply to an Instagram comment.

        Args:
            parent_id: The comment ID to reply to.
            text: Reply text.
            context_id: Optional media ID (used for logging only).

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        url = f"{self._base_url}/{parent_id}/replies"
        try:
            resp = requests.post(
                url,
                data={"message": text, "access_token": self._access_token},
                timeout=15,
            )
            data = _safe_json(resp)
            if resp.status_code == 200 and "id" in data:
                logger.info(
                    "Instagram: replied to comment %s (media=%s)", parent_id, context_id
                )
                return True
            logger.warning(
                "Instagram: reply failed (HTTP %d): %s",
                resp.status_code,
                data.get("error", {}).get("message", str(data)),
            )
            return False
        except Exception as exc:
            logger.warning("Instagram: post_reply exception: %s", exc)
            return False

    def like(self, target_id: str, *, context_id: str = "") -> bool:
        """Like an Instagram comment.

        Note: Meta's Graph API does not support liking comments on behalf
        of a Page using EAA tokens.  This is a no-op that always returns
        ``True`` to keep the engagement pipeline happy.
        """
        logger.debug(
            "Instagram: like() is a no-op (API unsupported for EAA tokens) — target=%s",
            target_id,
        )
        return True

    # ------------------------------------------------------------------
    # HealthCheckable protocol
    # ------------------------------------------------------------------

    def check_token_health(self) -> TokenStatus:
        """Verify the access token by calling ``/me`` on the Graph API.

        Returns:
            :class:`~genlab_core.platforms.models.TokenStatus` with
            ``valid=True`` when the token is accepted.
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
    # Channel verification
    # ------------------------------------------------------------------

    def verify_channel(self) -> bool:
        """Verify the IG Business Account exists and is accessible.

        Makes a lightweight GET to ``/{ig_user_id}?fields=id,username`` to
        confirm the token + account ID combination is valid.  Useful as a
        pre-flight check before attempting a publish.

        Returns:
            ``True`` if the account is accessible, ``False`` otherwise.
        """
        url = f"{self._base_url}/{self._ig_user_id}"
        try:
            resp = requests.get(
                url,
                params={
                    "fields": "id,username",
                    "access_token": self._access_token,
                },
                timeout=15,
            )
            data = _safe_json(resp)
            if resp.status_code == 200 and "id" in data:
                username = data.get("username", "")
                logger.info(
                    "Instagram: channel verified — id=%s username=%s",
                    data["id"],
                    username,
                )
                return True
            error_msg = (
                data.get("error", {}).get("message", "")
                or f"HTTP {resp.status_code}"
            )
            logger.error(
                "Instagram: channel verification failed: %s", error_msg
            )
            return False
        except Exception as exc:
            logger.error("Instagram: channel verification exception: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _publish_reel(
        self,
        *,
        video_url: str,
        caption: str,
        share_to_feed: bool = True,
        cover_url: str = "",
        max_poll_seconds: int = _DEFAULT_MAX_POLL_SECONDS,
    ) -> str | None:
        """Three-step reel publish: create container → poll → publish.

        Returns the published post ID on success, or ``None`` on failure.
        """
        creation_id = self._create_reel_container(
            video_url=video_url,
            caption=caption,
            share_to_feed=share_to_feed,
            cover_url=cover_url,
        )
        if creation_id is None:
            return None

        already_published = self._poll_container_status(
            creation_id=creation_id,
            max_poll_seconds=max_poll_seconds,
        )
        if already_published is None:
            # Timed out or error
            return None

        if already_published:
            # Container transitioned to PUBLISHED during polling; skip media_publish
            logger.info("Reel already PUBLISHED during polling — skipping media_publish")
            return creation_id

        return self._media_publish(creation_id=creation_id)

    def _create_reel_container(
        self,
        *,
        video_url: str,
        caption: str,
        share_to_feed: bool,
        cover_url: str,
    ) -> str | None:
        """POST to /{ig_user_id}/media to create the REELS container.

        Returns the creation_id on success, ``None`` on failure.
        """
        url = f"{self._base_url}/{self._ig_user_id}/media"
        data: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": str(share_to_feed).lower(),
            "access_token": self._access_token,
        }
        if cover_url:
            data["cover_url"] = cover_url

        try:
            resp = requests.post(url, data=data, timeout=60)
            payload = _safe_json(resp)
            if "id" in payload:
                logger.info("Reel container created: %s", payload["id"])
                return payload["id"]
            error_msg = payload.get("error", {}).get("message", str(payload))
            self._last_error = f"Container creation failed: {error_msg}"
            logger.error("Reel container creation failed: %s", error_msg)
            return None
        except Exception as exc:
            self._last_error = f"Container request error: {exc}"
            logger.error("Reel container request error: %s", exc)
            return None

    def _poll_container_status(
        self,
        *,
        creation_id: str,
        max_poll_seconds: int,
    ) -> bool | None:
        """Poll container status until FINISHED or timeout.

        Returns:
            ``True``  — container is already PUBLISHED (skip media_publish).
            ``False`` — container is FINISHED (proceed to media_publish).
            ``None``  — timed out or encountered an ERROR status.
        """
        status_url = f"{self._base_url}/{creation_id}"
        status_params = {
            "fields": "status_code,status",
            "access_token": self._access_token,
        }
        poll_start = time.time()
        poll_interval = _POLL_INTERVAL_INITIAL

        while True:
            elapsed = time.time() - poll_start
            if elapsed > max_poll_seconds:
                self._last_error = f"Container polling timed out after {max_poll_seconds}s"
                logger.error(
                    "Reel container polling timed out after %ds (container=%s)",
                    max_poll_seconds,
                    creation_id,
                )
                return None

            try:
                resp = requests.get(status_url, params=status_params, timeout=30)
                data = _safe_json(resp)
                status_code = data.get("status_code", "UNKNOWN")

                if status_code == "FINISHED":
                    logger.info(
                        "Reel container processing complete (%.0fs): %s",
                        elapsed,
                        creation_id,
                    )
                    return False  # Proceed to media_publish

                if status_code == "PUBLISHED":
                    return True  # Already live, skip media_publish

                if status_code == "ERROR":
                    # status field contains human-readable error detail
                    error_detail = data.get("status", "")
                    self._last_error = f"Container processing error: {error_detail or data}"
                    logger.error(
                        "Reel container processing error: %s (detail: %s)",
                        data, error_detail,
                    )
                    return None

                # IN_PROGRESS or UNKNOWN — keep waiting
                logger.debug(
                    "Reel container status=%s (%.0fs elapsed)", status_code, elapsed
                )
            except Exception as exc:
                logger.warning("Reel status poll error (retrying): %s", exc)

            time.sleep(poll_interval)
            if elapsed > _POLL_SLOWDOWN_AFTER:
                poll_interval = _POLL_INTERVAL_SLOW

    def _media_publish(self, *, creation_id: str) -> str | None:
        """POST to /{ig_user_id}/media_publish to finalise the reel.

        Returns the published post ID on success, ``None`` on failure.
        """
        url = f"{self._base_url}/{self._ig_user_id}/media_publish"
        data = {
            "creation_id": creation_id,
            "access_token": self._access_token,
        }
        try:
            resp = requests.post(url, data=data, timeout=60)
            payload = _safe_json(resp)
            if "id" in payload:
                logger.info("Reel published — post ID: %s", payload["id"])
                return payload["id"]
            error_msg = payload.get("error", {}).get("message", str(payload))
            self._last_error = f"media_publish failed: {error_msg}"
            logger.error("Reel media_publish failed: %s", error_msg)
            return None
        except Exception as exc:
            self._last_error = f"media_publish request error: {exc}"
            logger.error("Reel media_publish request error: %s", exc)
            return None

    def _graph_get(self, path: str, params: dict | None = None) -> dict:
        """GET request to the Graph API. Returns parsed JSON or empty dict."""
        url = f"{self._base_url}{path}"
        all_params = {"access_token": self._access_token}
        if params:
            all_params.update(params)
        try:
            resp = requests.get(url, params=all_params, timeout=15)
            return _safe_json(resp)
        except Exception:
            return {}


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

# _safe_json imported from genlab_core.platforms.models
