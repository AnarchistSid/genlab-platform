"""Thin ThreadsClient adapter — delegates to platforms.threads.ThreadsClient.

Kept for backward compatibility with channel strategy publishing.py files
that call ThreadsClient.publish_text() / publish_image() / publish_video().

The canonical implementation is genlab_core.platforms.threads.ThreadsClient.
"""
from __future__ import annotations

from genlab_core.platforms.threads import ThreadsClient as _PlatformThreadsClient

# Re-export constants for backward compatibility
THREADS_MAX_CAPTION = 500
THREADS_PROCESSING_WAIT = 30


class ThreadsClient:
    """Wrapper providing publish_text/image/video convenience methods."""

    def __init__(self, access_token: str | None = None, user_id: str | None = None, **kwargs):
        self._client = _PlatformThreadsClient(access_token=access_token, user_id=user_id)

    def publish_text(self, text: str, **kwargs) -> dict:
        result = self._client._publish_text(caption=text)
        return {"threads_id": result.post_id, "permalink": result.post_url}

    def publish_image(self, image_url: str, caption: str = "", **kwargs) -> dict:
        result = self._client._publish_image(caption=caption, media_paths=[image_url])
        return {"threads_id": result.post_id, "permalink": result.post_url}

    def publish_video(self, video_url: str, caption: str = "", **kwargs) -> dict:
        result = self._client._publish_video(caption=caption, media_paths=[video_url])
        return {"threads_id": result.post_id, "permalink": result.post_url}

    def refresh_token(self) -> str:
        self._client.refresh_token_if_needed()
        return self._client._access_token


class ThreadsTokenManager:
    """Token manager — delegates to platforms.threads refresh logic."""

    REFRESH_AT_DAYS = 45
    TOKEN_LIFETIME_DAYS = 60

    def __init__(self, client: ThreadsClient | None = None):
        self._client = client

    def check_and_refresh(self) -> dict:
        if self._client and self._client._client._token_needs_refresh():
            self._client._client.refresh_token_if_needed()
            return {"status": "refreshed", "days_remaining": self.TOKEN_LIFETIME_DAYS}
        return {"status": "ok", "days_remaining": -1}
