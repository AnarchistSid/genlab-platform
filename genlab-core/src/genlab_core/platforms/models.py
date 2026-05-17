"""Data models for the unified platform client package."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Union

import requests

logger = logging.getLogger(__name__)


def safe_json(resp: requests.Response) -> dict[str, Any]:
    """Safely parse a requests.Response as JSON.

    Returns ``{}`` on parse failure. Unlike the previous version, failures
    are now logged at WARNING level with the response status code and a
    truncated body snippet so malformed API responses don't vanish
    silently. Downstream callers still need to treat ``{}`` as a
    "failed-to-parse" signal.
    """
    try:
        return resp.json()
    except Exception as exc:
        logger.warning(
            "[safe_json] parse failure (status=%s, url=%s): %s | body=%r",
            getattr(resp, "status_code", "?"),
            getattr(resp, "url", "?"),
            exc,
            (getattr(resp, "text", "") or "")[:200],
        )
        return {}

# --- Platform-specific payload configs ---


@dataclass
class YouTubeSpecific:
    shorts_title: str = ""
    community_post_text: str = ""
    category_id: str = "28"
    privacy_status: str = "public"
    tags: list[str] = field(default_factory=list)


@dataclass
class TwitterSpecific:
    routing: Literal["single", "thread"] = "single"
    tweet_text: str = ""
    thread_tweets: list[dict] = field(default_factory=list)
    link_in_reply: bool = False


@dataclass
class InstagramSpecific:
    cover_url: str = ""
    share_to_feed: bool = True


@dataclass
class FacebookSpecific:
    pass


@dataclass
class ThreadsSpecific:
    pass


@dataclass
class TikTokSpecific:
    pass


PlatformSpecific = Union[
    YouTubeSpecific,
    TwitterSpecific,
    InstagramSpecific,
    FacebookSpecific,
    ThreadsSpecific,
    TikTokSpecific,
]


# --- Core models ---


@dataclass
class PublishPayload:
    """Input to Publisher.publish(). One per (blueprint, platform) pair."""

    caption: str
    media_paths: list[Path]
    media_type: Literal["video", "image", "text", "link"]
    hashtags: list[str]
    hook: str
    niche_id: str
    platform_specific: PlatformSpecific | None = None


@dataclass
class PublishResult:
    """Result from a single-platform publish attempt."""

    platform: str
    success: bool
    post_id: str = ""
    post_url: str = ""
    error: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def metadata(self) -> dict[str, Any]:
        """Alias for raw_response — used by new code."""
        return self.raw_response


@dataclass
class PlatformMetrics:
    """Metrics collected from a published post."""

    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenStatus:
    """Result from HealthCheckable.check_token_health()."""

    valid: bool
    platform: str
    expires_at: datetime | None
    needs_refresh: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)
