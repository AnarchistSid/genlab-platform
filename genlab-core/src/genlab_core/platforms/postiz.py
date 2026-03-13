"""Postiz REST API wrapper for multi-platform social publishing.

Replaces bespoke per-platform publish clients (instagram_client.py,
youtube_client.py, twitter_client.py, etc.) with a single ~300 LOC
REST wrapper that talks to a self-hosted Postiz instance.

Postiz handles all OAuth/token management internally. GenLab only
needs a single API key to schedule content across all connected
social accounts.

Usage:
    from genlab_core.platforms.postiz import PostizClient

    client = PostizClient()
    result = client.publish(
        integration_id="ig_criticalrush",
        text="Check out this gaming news!",
        media_url="https://cdn.example.com/video.mp4",
        platform="instagram",
    )
"""
from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from genlab_core.http.retry import retry
from genlab_core.settings import settings

logger = logging.getLogger(__name__)
_shadow_log = logging.getLogger("genlab.shadow_eval")

# Postiz API timeout (seconds) — publishing can be slow (video upload)
_DEFAULT_TIMEOUT = 120.0

# Polling interval and max attempts for async publish status checks
_POLL_INTERVAL = 5.0
_POLL_MAX_ATTEMPTS = 24  # 24 * 5s = 2 minutes max wait


class PostizPlatform(str, Enum):
    """Platforms supported by Postiz."""

    INSTAGRAM = "instagram"
    YOUTUBE = "youtube"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    THREADS = "threads"
    TIKTOK = "tiktok"
    LINKEDIN = "linkedin"


@dataclass
class PublishResult:
    """Result from a single-platform publish attempt."""

    platform: str
    success: bool
    post_id: str = ""
    post_url: str = ""
    error: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MultiPublishResult:
    """Aggregated result from publishing to multiple platforms."""

    results: List[PublishResult] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return bool(self.results) and all(r.success for r in self.results)

    @property
    def any_succeeded(self) -> bool:
        return any(r.success for r in self.results)

    @property
    def failed(self) -> List[PublishResult]:
        return [r for r in self.results if not r.success]

    @property
    def succeeded(self) -> List[PublishResult]:
        return [r for r in self.results if r.success]


class PostizClient:
    """REST client for a self-hosted Postiz instance.

    Wraps the Postiz API for scheduling and publishing content.
    All platform-specific OAuth/token management is handled by Postiz.

    Args:
        base_url: Postiz server URL. Defaults to settings.postiz_url.
        api_key: Postiz API key. Defaults to settings.postiz_api_key.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self._base_url = (base_url or settings.postiz_url).rstrip("/")
        self._api_key = api_key or settings.postiz_api_key
        if not self._api_key:
            raise ValueError(
                "Postiz API key required. Set POSTIZ_API_KEY env var "
                "or pass api_key to PostizClient()."
            )
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        """Lazy-init httpx client with auth headers."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> PostizClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Integration discovery
    # ------------------------------------------------------------------

    @retry(max_attempts=3, backoff=2.0, exceptions=(httpx.HTTPError,))
    def list_integrations(self) -> List[Dict[str, Any]]:
        """List all connected social accounts in Postiz.

        Returns a list of integration dicts, each containing at least:
          - id: integration identifier
          - name: display name
          - provider: platform name (instagram, youtube, etc.)
        """
        resp = self._get_client().get("/api/integrations")
        resp.raise_for_status()
        return resp.json()

    def get_integration_by_provider(
        self, provider: str
    ) -> Optional[Dict[str, Any]]:
        """Find the first integration matching a provider name."""
        provider = provider.lower()
        for integration in self.list_integrations():
            if integration.get("providerIdentifier", "").lower() == provider:
                return integration
            if integration.get("provider", "").lower() == provider:
                return integration
        return None

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    @retry(max_attempts=2, backoff=3.0, exceptions=(httpx.HTTPError,))
    def publish(
        self,
        integration_id: str,
        text: str,
        *,
        media_url: str | None = None,
        media_path: str | None = None,
        platform: str = "",
        extra: Dict[str, Any] | None = None,
    ) -> PublishResult:
        """Publish content to a single platform via Postiz.

        Args:
            integration_id: Postiz integration ID for the target account.
            text: Post text/caption.
            media_url: Public URL for media (image/video).
            media_path: Local file path for media upload.
            platform: Platform name for logging/result tracking.
            extra: Additional platform-specific fields (title, description, etc.)

        Returns:
            PublishResult with success status and post details.
        """
        platform = platform or "unknown"

        payload: Dict[str, Any] = {
            "integrationId": integration_id,
            "content": text,
            "type": "now",  # publish immediately
        }

        if media_url:
            payload["media"] = [{"url": media_url}]
        elif media_path:
            payload["media"] = [{"path": media_path}]

        if extra:
            payload.update(extra)

        try:
            resp = self._get_client().post("/api/posts", json=payload)
            resp.raise_for_status()
            data = resp.json()

            post_id = str(data.get("id", ""))
            post_url = data.get("url", "")

            logger.info(
                "[POSTIZ] Published to %s: post_id=%s",
                platform, post_id,
            )
            return PublishResult(
                platform=platform,
                success=True,
                post_id=post_id,
                post_url=post_url,
                raw_response=data,
            )

        except httpx.HTTPStatusError as exc:
            error_body = ""
            try:
                error_body = exc.response.text
            except Exception:
                pass
            error_msg = f"HTTP {exc.response.status_code}: {error_body}"
            logger.warning(
                "[POSTIZ] Publish failed for %s: %s", platform, error_msg,
            )
            return PublishResult(
                platform=platform,
                success=False,
                error=error_msg,
            )

    def publish_multi(
        self,
        integration_ids: Dict[str, str],
        text: str,
        *,
        media_url: str | None = None,
        media_path: str | None = None,
        platform_overrides: Dict[str, Dict[str, Any]] | None = None,
    ) -> MultiPublishResult:
        """Publish content to multiple platforms in sequence.

        Args:
            integration_ids: Mapping of platform name → Postiz integration ID.
                Example: {"instagram": "abc123", "youtube": "def456"}
            text: Base post text/caption.
            media_url: Public URL for media.
            media_path: Local file path for media.
            platform_overrides: Per-platform extra fields.
                Example: {"youtube": {"title": "My Video", "description": "..."}}

        Returns:
            MultiPublishResult aggregating all platform results.
        """
        overrides = platform_overrides or {}
        multi = MultiPublishResult()

        for platform, integration_id in integration_ids.items():
            extra = overrides.get(platform)
            result = self.publish(
                integration_id=integration_id,
                text=text,
                media_url=media_url,
                media_path=media_path,
                platform=platform,
                extra=extra,
            )
            multi.results.append(result)

        succeeded = len(multi.succeeded)
        failed = len(multi.failed)
        logger.info(
            "[POSTIZ] Multi-publish complete: %d succeeded, %d failed",
            succeeded, failed,
        )
        return multi

    # ------------------------------------------------------------------
    # Post management
    # ------------------------------------------------------------------

    @retry(max_attempts=2, backoff=2.0, exceptions=(httpx.HTTPError,))
    def get_post(self, post_id: str) -> Dict[str, Any]:
        """Get post details by ID."""
        resp = self._get_client().get(f"/api/posts/{post_id}")
        resp.raise_for_status()
        return resp.json()

    @retry(max_attempts=2, backoff=2.0, exceptions=(httpx.HTTPError,))
    def delete_post(self, post_id: str) -> bool:
        """Delete a scheduled or published post."""
        resp = self._get_client().delete(f"/api/posts/{post_id}")
        resp.raise_for_status()
        return True

    # ------------------------------------------------------------------
    # Media upload
    # ------------------------------------------------------------------

    @retry(max_attempts=2, backoff=3.0, exceptions=(httpx.HTTPError,))
    def upload_media(self, file_path: str) -> Dict[str, Any]:
        """Upload a media file to Postiz and return the media object.

        Returns dict with at least:
          - id: media identifier
          - url: public URL for the uploaded file
        """
        client = self._get_client()
        with open(file_path, "rb") as f:
            # Use multipart upload — drop Content-Type header for this request
            resp = client.post(
                "/api/media",
                files={"file": f},
                headers={"Content-Type": None},  # let httpx set multipart boundary
            )
        resp.raise_for_status()
        data = resp.json()
        logger.info("[POSTIZ] Uploaded media: %s", data.get("id", ""))
        return data

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Check if the Postiz server is reachable."""
        try:
            resp = self._get_client().get("/api/user")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


class ShadowPublisher:
    """Run existing publisher and Postiz in parallel for comparison.

    Shadow mode: the primary publish function runs first. If it succeeds,
    Postiz also publishes the same content. Shadow failures are logged
    but never affect the primary outcome.

    Gated by POSTIZ_SHADOW_MODE=true env var. Automatically disables
    if Postiz is unreachable.
    """

    def __init__(self, postiz_client: PostizClient | None = None) -> None:
        import os
        self._enabled = os.environ.get("POSTIZ_SHADOW_MODE", "false").lower() == "true"
        self._postiz: PostizClient | None = None

        if self._enabled:
            try:
                self._postiz = postiz_client or PostizClient()
                if not self._postiz.health_check():
                    logger.warning("[shadow] Postiz unreachable — shadow mode disabled")
                    self._enabled = False
            except Exception as e:
                logger.warning("[shadow] PostizClient init failed: %s — shadow mode disabled", e)
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def publish_with_shadow(
        self,
        primary_fn,
        *,
        platform: str = "",
        caption: str = "",
        media_path: str | None = None,
        content_id: str = "",
        integration_id: str = "",
        **primary_kwargs,
    ) -> dict:
        """Call primary_fn(**primary_kwargs) and optionally shadow via Postiz.

        Returns {"primary": <primary_result>, "shadow": <PublishResult|None>}
        """
        # Always run primary
        primary_result = primary_fn(**primary_kwargs)

        shadow_result: PublishResult | None = None
        if self._enabled and self._postiz and integration_id:
            try:
                shadow_result = self._postiz.publish(
                    integration_id=integration_id,
                    text=caption,
                    media_path=media_path,
                    platform=platform,
                )
                if shadow_result.success:
                    logger.info("[shadow] %s: post_id=%s content=%s",
                                platform, shadow_result.post_id, content_id)
                else:
                    logger.warning("[shadow] %s failed: %s", platform, shadow_result.error)
            except Exception as e:
                logger.warning("[shadow] %s exception: %s", platform, e)

        # Structured comparison log for shadow evaluation
        _shadow_log.info(_json.dumps({
            "event": "shadow_compare",
            "platform": platform,
            "content_id": content_id,
            "primary_ok": primary_result is not None,
            "shadow_ok": shadow_result.success if shadow_result else None,
            "shadow_post_id": shadow_result.post_id if shadow_result else None,
            "shadow_error": shadow_result.error if shadow_result and not shadow_result.success else None,
        }))

        return {"primary": primary_result, "shadow": shadow_result}
