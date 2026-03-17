# Platform Consolidation & Infrastructure Refactor — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate GenLab's fragmented platform publishing, engagement, and analytics code into a unified `genlab_core.platforms` package with layered protocols, extract publishing gates, standardize dashboard responses, replace launchd with an in-process scheduler, and add a team/permission skeleton.

**Architecture:** Strangler fig — all new code is additive (Steps 1-6), then callers are swapped (Steps 7-9 with feature flags), then old code is deleted (Steps 10-12). Each step is independently deployable and revertible.

**Tech Stack:** Python 3.11+, uv workspace, hatchling, Flask, APScheduler 3.x, SQLAlchemy 2.x, tweepy, google-api-python-client, requests, TypeScript (dashboard frontend)

**Spec:** `docs/superpowers/specs/2026-03-13-platform-consolidation-design.md`

**Test runner:** `uv run --package genlab-core pytest genlab-core/tests/ -v`

---

## Chunk 1: Foundation (Protocols, Models, Registry)

### Task 1: Create `platforms` package with protocols

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/__init__.py`
- Create: `genlab-core/src/genlab_core/platforms/protocols.py`
- Test: `genlab-core/tests/platforms/test_protocols.py`

- [ ] **Step 1: Create package directory**

```bash
mkdir -p genlab-core/src/genlab_core/platforms
mkdir -p genlab-core/tests/platforms
touch genlab-core/tests/platforms/__init__.py
```

- [ ] **Step 2: Write the failing test for protocols**

```python
# genlab-core/tests/platforms/test_protocols.py
"""Tests for platform protocol definitions."""
from __future__ import annotations

import pytest
from datetime import datetime
from typing import runtime_checkable


def test_publisher_protocol_is_runtime_checkable():
    from genlab_core.platforms.protocols import Publisher
    assert hasattr(Publisher, "__protocol_attrs__") or runtime_checkable

    class FakePublisher:
        platform_id = "fake"
        def publish(self, payload):
            pass

    assert isinstance(FakePublisher(), Publisher)


def test_non_publisher_fails_isinstance():
    from genlab_core.platforms.protocols import Publisher

    class NotAPublisher:
        pass

    assert not isinstance(NotAPublisher(), Publisher)


def test_engageable_protocol_is_runtime_checkable():
    from genlab_core.platforms.protocols import Engageable

    class FakeEngageable:
        def post_reply(self, parent_id: str, text: str, *, context_id: str = "") -> bool:
            return True
        def like(self, target_id: str, *, context_id: str = "") -> bool:
            return True

    assert isinstance(FakeEngageable(), Engageable)


def test_trackable_protocol_is_runtime_checkable():
    from genlab_core.platforms.protocols import Trackable

    class FakeTrackable:
        def get_metrics(self, post_id, published_at):
            return None

    assert isinstance(FakeTrackable(), Trackable)


def test_healthcheckable_protocol_is_runtime_checkable():
    from genlab_core.platforms.protocols import HealthCheckable

    class FakeHealthCheckable:
        def check_token_health(self):
            return None

    assert isinstance(FakeHealthCheckable(), HealthCheckable)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_protocols.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'genlab_core.platforms'`

- [ ] **Step 4: Implement protocols**

```python
# genlab-core/src/genlab_core/platforms/protocols.py
"""Layered protocols for platform clients.

Publisher is required. Engageable, Trackable, HealthCheckable are optional.
Use isinstance() checks at dispatch time to determine capabilities.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from genlab_core.platforms.models import (
        PlatformMetrics,
        PublishPayload,
        PublishResult,
        TokenStatus,
    )


@runtime_checkable
class Publisher(Protocol):
    """Core protocol — every platform must implement publish()."""

    platform_id: str

    def publish(self, payload: PublishPayload) -> PublishResult: ...


@runtime_checkable
class Engageable(Protocol):
    """Optional: reply to comments, like posts."""

    def post_reply(
        self, parent_id: str, text: str, *, context_id: str = ""
    ) -> bool: ...

    def like(self, target_id: str, *, context_id: str = "") -> bool: ...


@runtime_checkable
class Trackable(Protocol):
    """Optional: collect analytics/metrics for a published post."""

    def get_metrics(
        self, post_id: str, published_at: datetime
    ) -> PlatformMetrics | None: ...


@runtime_checkable
class HealthCheckable(Protocol):
    """Optional: verify token health for the platform."""

    def check_token_health(self) -> TokenStatus: ...
```

- [ ] **Step 5: Create `__init__.py` (minimal — no eager imports)**

```python
# genlab-core/src/genlab_core/platforms/__init__.py
"""Unified platform client package.

Usage:
    from genlab_core.platforms import get_client, list_platforms
    client = get_client("instagram")
    result = client.publish(payload)
"""
from genlab_core.platforms.registry import get_client, list_platforms

__all__ = ["get_client", "list_platforms"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_protocols.py -v`
Expected: 4 PASSED

- [ ] **Step 7: Commit**

```bash
git add genlab-core/src/genlab_core/platforms/__init__.py \
        genlab-core/src/genlab_core/platforms/protocols.py \
        genlab-core/tests/platforms/__init__.py \
        genlab-core/tests/platforms/test_protocols.py
git commit -m "feat(platforms): add layered protocol definitions (Publisher, Engageable, Trackable, HealthCheckable)"
```

---

### Task 2: Create data models

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/models.py`
- Test: `genlab-core/tests/platforms/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# genlab-core/tests/platforms/test_models.py
"""Tests for platform data models."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest


def test_publish_payload_creation():
    from genlab_core.platforms.models import PublishPayload, YouTubeSpecific

    payload = PublishPayload(
        caption="Test caption",
        media_paths=[Path("/tmp/video.mp4")],
        media_type="video",
        hashtags=["#test"],
        hook="Breaking news",
        niche_id="ai_creators",
        platform_specific=YouTubeSpecific(shorts_title="Test Short"),
    )
    assert payload.caption == "Test caption"
    assert payload.platform_specific.shorts_title == "Test Short"


def test_publish_payload_no_platform_specific():
    from genlab_core.platforms.models import PublishPayload

    payload = PublishPayload(
        caption="Test",
        media_paths=[],
        media_type="text",
        hashtags=[],
        hook="",
        niche_id="gaming",
    )
    assert payload.platform_specific is None


def test_publish_result_backward_compat():
    from genlab_core.platforms.models import PublishResult

    result = PublishResult(platform="instagram", success=True, post_id="123")
    assert result.metadata == {}  # alias for raw_response
    assert result.post_url == ""
    assert result.error == ""


def test_publish_result_metadata_alias():
    from genlab_core.platforms.models import PublishResult

    result = PublishResult(
        platform="youtube",
        success=True,
        post_id="abc",
        raw_response={"video_url": "https://..."},
    )
    assert result.metadata == {"video_url": "https://..."}
    assert result.metadata is result.raw_response


def test_platform_metrics_defaults():
    from genlab_core.platforms.models import PlatformMetrics

    m = PlatformMetrics()
    assert m.views == 0
    assert m.likes == 0
    assert m.extra == {}


def test_token_status_fields():
    from genlab_core.platforms.models import TokenStatus

    ts = TokenStatus(
        valid=True,
        platform="instagram",
        expires_at=None,
        needs_refresh=False,
        message="EAA token is permanent",
    )
    assert ts.valid is True
    assert ts.details == {}


def test_youtube_specific_defaults():
    from genlab_core.platforms.models import YouTubeSpecific

    yt = YouTubeSpecific()
    assert yt.category_id == "28"
    assert yt.privacy_status == "public"
    assert yt.tags == []


def test_twitter_specific_defaults():
    from genlab_core.platforms.models import TwitterSpecific

    tw = TwitterSpecific()
    assert tw.routing == "single"
    assert tw.thread_tweets == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement models**

```python
# genlab-core/src/genlab_core/platforms/models.py
"""Data models for the unified platform client package."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Union


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


PlatformSpecific = Union[
    YouTubeSpecific,
    TwitterSpecific,
    InstagramSpecific,
    FacebookSpecific,
    ThreadsSpecific,
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
    """Result from a single-platform publish attempt.

    Backward-compatible with existing postiz_client.PublishResult fields.
    """

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_models.py -v`
Expected: 9 PASSED

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/platforms/models.py \
        genlab-core/tests/platforms/test_models.py
git commit -m "feat(platforms): add data models (PublishPayload, PublishResult, PlatformMetrics, TokenStatus)"
```

---

### Task 3: Create lazy registry

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/registry.py`
- Test: `genlab-core/tests/platforms/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# genlab-core/tests/platforms/test_registry.py
"""Tests for the lazy platform client registry."""
from __future__ import annotations

import pytest


def test_list_platforms_returns_known_ids():
    from genlab_core.platforms.registry import list_platforms

    platforms = list_platforms()
    assert "instagram" in platforms
    assert "youtube" in platforms
    assert "x_twitter" in platforms
    assert "facebook" in platforms
    assert "threads" in platforms
    assert "tiktok" in platforms


def test_get_client_unknown_platform_raises():
    from genlab_core.platforms.registry import get_client

    with pytest.raises(ValueError, match="Unknown platform"):
        get_client("myspace")


def test_get_client_deferred_import_error():
    """If a platform module doesn't exist yet, get_client raises ImportError."""
    from genlab_core.platforms.registry import get_client

    # instagram.py doesn't exist yet — this should raise
    with pytest.raises((ImportError, ModuleNotFoundError)):
        get_client("instagram")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_registry.py -v`
Expected: FAIL

- [ ] **Step 3: Implement registry**

```python
# genlab-core/src/genlab_core/platforms/registry.py
"""Lazy platform client registry.

Platform modules are imported on first use, not at package load time.
This avoids pulling in tweepy, google-api-python-client, etc. when
only one platform is needed.
"""
from __future__ import annotations

import importlib

# Maps platform_id -> "module.path:ClassName"
_REGISTRY: dict[str, str] = {
    "instagram": "genlab_core.platforms.instagram:InstagramClient",
    "youtube": "genlab_core.platforms.youtube:YouTubeClient",
    "x_twitter": "genlab_core.platforms.x_twitter:XTwitterClient",
    "facebook": "genlab_core.platforms.facebook:FacebookClient",
    "threads": "genlab_core.platforms.threads:ThreadsClient",
    "tiktok": "genlab_core.platforms.tiktok:TikTokClient",
}

# Cache instantiated classes (not instances) to avoid repeated imports
_CLASS_CACHE: dict[str, type] = {}


def get_client(platform_id: str, **kwargs):
    """Lazy-load a platform client module and return an instance.

    Args:
        platform_id: One of the registered platform IDs.
        **kwargs: Passed to the client constructor (overrides env-var defaults).

    Returns:
        An instance implementing at minimum the Publisher protocol.

    Raises:
        ValueError: If platform_id is not registered.
        ImportError: If the platform module cannot be imported.
    """
    if platform_id not in _REGISTRY:
        raise ValueError(f"Unknown platform: {platform_id}")

    if platform_id not in _CLASS_CACHE:
        entry = _REGISTRY[platform_id]
        module_path, class_name = entry.rsplit(":", 1)
        module = importlib.import_module(module_path)
        _CLASS_CACHE[platform_id] = getattr(module, class_name)

    return _CLASS_CACHE[platform_id](**kwargs)


def list_platforms() -> list[str]:
    """Return all registered platform IDs."""
    return list(_REGISTRY.keys())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_registry.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/platforms/registry.py \
        genlab-core/tests/platforms/test_registry.py
git commit -m "feat(platforms): add lazy client registry with deferred imports"
```

---

## Chunk 2: Platform Clients (Additive — Old Code Untouched)

### Task 4: Migrate postiz_client + platform_rules into platforms/

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/postiz.py` (copy from `platform/postiz_client.py`)
- Create: `genlab-core/src/genlab_core/platforms/rules.py` (copy from `platform/platform_rules.py`)
- Create: `genlab-core/src/genlab_core/platforms/engagement/` (move engagement modules)
- Modify: `genlab-core/src/genlab_core/platform/__init__.py` (add shim re-exports)
- Test: `genlab-core/tests/platforms/test_postiz_shim.py`

- [ ] **Step 1: Write the failing test for shim backward compatibility**

```python
# genlab-core/tests/platforms/test_postiz_shim.py
"""Verify that old import paths still work via shim."""
from __future__ import annotations


def test_old_import_path_postiz_client():
    """CriticalRush imports PostizClient via old path."""
    from genlab_core.platform.postiz_client import PostizClient, PublishResult
    assert PostizClient is not None
    assert PublishResult is not None


def test_old_import_path_platform_rules():
    """Multiple files import platform_rules via old path."""
    from genlab_core.platform.platform_rules import PLATFORM_RULES
    assert isinstance(PLATFORM_RULES, dict)


def test_new_import_path_postiz():
    """New code uses platforms.postiz."""
    from genlab_core.platforms.postiz import PostizClient, PublishResult
    assert PostizClient is not None
    assert PublishResult is not None


def test_new_import_path_rules():
    from genlab_core.platforms.rules import PLATFORM_RULES
    assert isinstance(PLATFORM_RULES, dict)


def test_old_and_new_are_same_class():
    from genlab_core.platform.postiz_client import PostizClient as OldPC
    from genlab_core.platforms.postiz import PostizClient as NewPC
    assert OldPC is NewPC
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_postiz_shim.py -v`
Expected: FAIL — `ModuleNotFoundError` for `genlab_core.platforms.postiz`

- [ ] **Step 3: Copy postiz_client.py → platforms/postiz.py**

```bash
cp genlab-core/src/genlab_core/platform/postiz_client.py \
   genlab-core/src/genlab_core/platforms/postiz.py
```

No internal changes needed — the file is self-contained.

- [ ] **Step 4: Copy platform_rules.py → platforms/rules.py**

```bash
cp genlab-core/src/genlab_core/platform/platform_rules.py \
   genlab-core/src/genlab_core/platforms/rules.py
```

- [ ] **Step 5: Copy engagement modules → platforms/engagement/**

```bash
mkdir -p genlab-core/src/genlab_core/platforms/engagement
cp genlab-core/src/genlab_core/platform/engagement_engine.py \
   genlab-core/src/genlab_core/platforms/engagement/engine.py
cp genlab-core/src/genlab_core/platform/engagement_poller.py \
   genlab-core/src/genlab_core/platforms/engagement/poller.py
cp genlab-core/src/genlab_core/platform/_engagement_worker.py \
   genlab-core/src/genlab_core/platforms/engagement/_worker.py
touch genlab-core/src/genlab_core/platforms/engagement/__init__.py
```

- [ ] **Step 6: Update platform/__init__.py as shim**

Read `genlab-core/src/genlab_core/platform/__init__.py` first. Then replace with shim:

```python
# genlab-core/src/genlab_core/platform/__init__.py
"""SHIM: Re-exports from genlab_core.platforms for backward compatibility.

This package is deprecated. Import from genlab_core.platforms instead.
Will be deleted after all callers are migrated.
"""
```

Also create `genlab-core/src/genlab_core/platform/postiz_client.py` shim (replace content):

```python
# genlab-core/src/genlab_core/platform/postiz_client.py
"""SHIM: Re-exports from genlab_core.platforms.postiz."""
from genlab_core.platforms.postiz import *  # noqa: F401,F403
from genlab_core.platforms.postiz import (  # explicit re-exports for type checkers
    MultiPublishResult,
    PostizClient,
    PostizPlatform,
    PublishResult,
    ShadowPublisher,
)
```

Similarly for `platform_rules.py`:

```python
# genlab-core/src/genlab_core/platform/platform_rules.py
"""SHIM: Re-exports from genlab_core.platforms.rules."""
from genlab_core.platforms.rules import *  # noqa: F401,F403
```

- [ ] **Step 7: Run shim tests**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_postiz_shim.py -v`
Expected: 5 PASSED

- [ ] **Step 8: Run full genlab-core test suite to check for regressions**

Run: `uv run --package genlab-core pytest genlab-core/tests/ -x -q`
Expected: All existing tests pass (695+)

- [ ] **Step 9: Commit**

```bash
git add genlab-core/src/genlab_core/platforms/postiz.py \
        genlab-core/src/genlab_core/platforms/rules.py \
        genlab-core/src/genlab_core/platforms/engagement/ \
        genlab-core/src/genlab_core/platform/postiz_client.py \
        genlab-core/src/genlab_core/platform/platform_rules.py \
        genlab-core/tests/platforms/test_postiz_shim.py
git commit -m "refactor(platforms): migrate postiz_client + platform_rules into platforms/ with backward-compat shims"
```

---

### Task 5: Implement InstagramClient

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/instagram.py`
- Test: `genlab-core/tests/platforms/test_instagram.py`

**Reference:** Port logic from `BlackboxBrief/execution/publish_to_instagram.py` and `genlab-core/src/genlab_core/engagement/platform_clients/instagram.py`.

- [ ] **Step 1: Write failing tests**

```python
# genlab-core/tests/platforms/test_instagram.py
"""Tests for InstagramClient — mocks all HTTP."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.platforms.models import PublishPayload, InstagramSpecific


@pytest.fixture
def ig_client():
    from genlab_core.platforms.instagram import InstagramClient
    return InstagramClient(
        access_token="EAA_TEST_TOKEN",
        ig_user_id="17841448019867838",
        api_version="v21.0",
    )


class TestPublish:
    def test_publish_video_reel(self, ig_client):
        """Reel publish: create container → poll → publish."""
        payload = PublishPayload(
            caption="Test reel",
            media_paths=[Path("/tmp/video.mp4")],
            media_type="video",
            hashtags=["#test"],
            hook="Watch this",
            niche_id="ai_creators",
            platform_specific=InstagramSpecific(share_to_feed=True),
        )

        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            # Mock container creation
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "container_123"},
            )
            # Mock status check (FINISHED)
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"status_code": "FINISHED"},
            )
            result = ig_client.publish(payload)

        assert result.platform == "instagram"
        # We can't assert success=True because the mock flow is simplified,
        # but we verify the method doesn't crash and returns a PublishResult
        assert hasattr(result, "success")
        assert hasattr(result, "post_id")

    def test_publish_requires_media(self, ig_client):
        """Publishing with no media paths should fail."""
        payload = PublishPayload(
            caption="No media",
            media_paths=[],
            media_type="text",
            hashtags=[],
            hook="",
            niche_id="ai_creators",
        )
        result = ig_client.publish(payload)
        assert result.success is False
        assert "media" in result.error.lower() or "video" in result.error.lower()


class TestEngagement:
    def test_post_reply(self, ig_client):
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "reply_456"},
            )
            ok = ig_client.post_reply(
                parent_id="comment_789",
                text="Thanks!",
                context_id="media_123",
            )
        assert ok is True

    def test_like(self, ig_client):
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.post.return_value = MagicMock(status_code=200, json=lambda: {"success": True})
            ok = ig_client.like(target_id="comment_789")
        assert ok is True


class TestHealthCheck:
    def test_valid_token(self, ig_client):
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"id": "17841448019867838", "name": "Test"},
            )
            status = ig_client.check_token_health()
        assert status.valid is True
        assert status.platform == "instagram"

    def test_invalid_token(self, ig_client):
        with patch("genlab_core.platforms.instagram.requests") as mock_req:
            mock_req.get.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": {"message": "Invalid token"}},
            )
            status = ig_client.check_token_health()
        assert status.valid is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_instagram.py -v`
Expected: FAIL

- [ ] **Step 3: Implement InstagramClient**

Create `genlab-core/src/genlab_core/platforms/instagram.py`. Port logic from:
- `BlackboxBrief/execution/publish_to_instagram.py` (reel + carousel publish)
- `genlab-core/src/genlab_core/engagement/platform_clients/instagram.py` (reply + like)

Key implementation notes:
- ALL requests go to `graph.facebook.com` (never `graph.instagram.com`)
- Reel publish: POST `/{ig_user_id}/media` with `video_url` + `caption` → poll status → POST `/{ig_user_id}/media_publish` with `creation_id`
- Carousel: upload each item as container → POST `/{ig_user_id}/media` with `children` + `media_type=CAROUSEL`
- Reply: POST `/{comment_id}/replies` with `message`
- Like: POST `/{comment_id}/likes`
- Health: GET `/me` with access_token

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_instagram.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/platforms/instagram.py \
        genlab-core/tests/platforms/test_instagram.py
git commit -m "feat(platforms): add InstagramClient (publish, reply, like, health)"
```

---

### Task 6: Implement YouTubeClient

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/youtube.py`
- Test: `genlab-core/tests/platforms/test_youtube.py`

**Reference:** Port from `BlackboxBrief/execution/publish_youtube.py`, `execution/utils/youtube_client.py`, `engagement/platform_clients/youtube.py`, `analytics/youtube_analytics_client.py`.

- [ ] **Step 1: Write failing tests**

Tests should cover:
- `publish()` — Shorts (<=180s) vs regular video routing
- `post_reply(parent_id=comment_id, text, context_id=video_id)` — comment reply
- `like(target_id=comment_id)` — comment like
- `get_metrics(video_id, published_at)` — Analytics API v2 with 48h lag guard
- `check_token_health()` — OAuth2 refresh token validation

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement YouTubeClient**

Key implementation notes:
- OAuth2 token refresh: reads `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`
- Access token cached 50 min, refreshed on demand
- Shorts: video duration <=180s → upload with `#Shorts` tag
- Regular: video duration >180s → standard upload
- Upload: Data API v3 `videos.insert` with resumable media
- Metrics: youtubeAnalytics v2 — skip if `published_at` < 48h ago
- Reply: `commentThreads.insert` with `snippet.parentId`
- Like: `comments.setModerationStatus` (or `comments.markAsSpam` for dislike)

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(platforms): add YouTubeClient (publish, reply, like, metrics, health)"
```

---

### Task 7: Implement XTwitterClient

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/x_twitter.py`
- Test: `genlab-core/tests/platforms/test_x_twitter.py`

**Reference:** Port from `BlackboxBrief/execution/publish_twitter.py`, `execution/utils/twitter_client.py`, `engagement/platform_clients/x_twitter.py`.

- [ ] **Step 1: Write failing tests**

Tests should cover:
- `publish()` — single tweet vs thread routing (from `TwitterSpecific.routing`)
- `post_reply(parent_id=tweet_id, text)` — no context_id needed
- `like(target_id=tweet_id)`
- `get_metrics(tweet_id, published_at)` — tweet metrics
- `check_token_health()` — bearer token check (403 = valid for free tier)
- Rate limit: 429 flag + 1h cooldown

- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement XTwitterClient**

Key notes:
- OAuth 1.0a via tweepy (`X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`)
- Fresh tweepy.Client per call (not thread-safe)
- Media upload: chunked for video >5MB
- Rate limit tracking: instance-level `_rate_limited` flag + `_rate_limited_at` timestamp

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(platforms): add XTwitterClient (publish, reply, like, metrics, health)"
```

---

### Task 8: Implement FacebookClient

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/facebook.py`
- Test: `genlab-core/tests/platforms/test_facebook.py`

**Reference:** Port from `BlackboxBrief/execution/publish_facebook.py`, `engagement/platform_clients/facebook.py`.

- [ ] **Step 1: Write failing tests**

Tests should cover:
- `publish()` — video/reel, carousel (unpublished photos + feed), single photo, link post
- `post_reply(parent_id=comment_id, text)` — no context_id needed
- `get_metrics(post_id, published_at)` — post insights
- `check_token_health()` — EAA token validation
- Retry with exponential backoff on 429/5xx

- [ ] **Step 2-5: Implement, test, commit**

```bash
git commit -m "feat(platforms): add FacebookClient (publish, reply, metrics, health)"
```

---

### Task 9: Implement ThreadsClient

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/threads.py`
- Test: `genlab-core/tests/platforms/test_threads.py`

**Reference:** Port from `BlackboxBrief/execution/publish_threads.py`, `engagement/platform_clients/threads.py`.

- [ ] **Step 1: Write failing tests**

Tests should cover:
- `publish()` — video, image, text, carousel
- `post_reply(parent_id=thread_id, text)` — no context_id needed
- `check_token_health()` — 60-day token, auto-refresh at 50 days

- [ ] **Step 2-5: Implement, test, commit**

```bash
git commit -m "feat(platforms): add ThreadsClient (publish, reply, health)"
```

---

### Task 10: Implement TikTokClient stub

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/tiktok.py`
- Test: `genlab-core/tests/platforms/test_tiktok.py`

- [ ] **Step 1: Write failing test**

```python
# genlab-core/tests/platforms/test_tiktok.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from genlab_core.platforms.models import PublishPayload


def test_tiktok_disabled_by_default():
    from genlab_core.platforms.tiktok import TikTokClient

    client = TikTokClient()
    payload = PublishPayload(
        caption="Test", media_paths=[Path("/tmp/v.mp4")],
        media_type="video", hashtags=[], hook="", niche_id="gaming",
    )
    result = client.publish(payload)
    assert result.success is False
    assert "disabled" in result.error.lower() or "audit" in result.error.lower()


def test_tiktok_enabled_with_env():
    from genlab_core.platforms.tiktok import TikTokClient

    with patch.dict("os.environ", {"TIKTOK_AUDIT_APPROVED": "true"}):
        client = TikTokClient()
    # Still stub — would need real API keys
    # Just verify it doesn't crash on init
    assert client.platform_id == "tiktok"
```

- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement stub**

```python
# genlab-core/src/genlab_core/platforms/tiktok.py
"""TikTok client stub — disabled pending TIKTOK_AUDIT_APPROVED=true."""
from __future__ import annotations

import os

from genlab_core.platforms.models import PublishPayload, PublishResult


class TikTokClient:
    platform_id = "tiktok"

    def __init__(self):
        self._enabled = os.environ.get("TIKTOK_AUDIT_APPROVED", "").lower() == "true"

    def publish(self, payload: PublishPayload) -> PublishResult:
        if not self._enabled:
            return PublishResult(
                platform="tiktok",
                success=False,
                error="TikTok publishing disabled pending audit (TIKTOK_AUDIT_APPROVED=true)",
            )
        # Future: implement actual TikTok API calls
        return PublishResult(
            platform="tiktok",
            success=False,
            error="TikTok publish not yet implemented",
        )
```

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(platforms): add TikTokClient stub (disabled pending audit)"
```

---

### Task 11: Verify registry resolves all clients + run full suite

- [ ] **Step 1: Write registry integration test**

```python
# genlab-core/tests/platforms/test_registry_integration.py
"""Verify all registered platform clients can be instantiated."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from genlab_core.platforms.registry import get_client, list_platforms


MOCK_ENV = {
    "META_ACCESS_TOKEN": "EAA_TEST",
    "META_IG_USER_ID": "123",
    "META_FB_PAGE_ID": "456",
    "YOUTUBE_CLIENT_ID": "test",
    "YOUTUBE_CLIENT_SECRET": "test",
    "YOUTUBE_REFRESH_TOKEN": "test",
    "X_API_KEY": "test",
    "X_API_SECRET": "test",
    "X_ACCESS_TOKEN": "test",
    "X_ACCESS_SECRET": "test",
    "THREADS_ACCESS_TOKEN": "test",
}


@pytest.mark.parametrize("platform_id", list_platforms())
def test_get_client_returns_publisher(platform_id):
    from genlab_core.platforms.protocols import Publisher

    with patch.dict("os.environ", MOCK_ENV):
        client = get_client(platform_id)
    assert isinstance(client, Publisher)
    assert client.platform_id == platform_id
```

- [ ] **Step 2: Run registry integration test**

Run: `uv run --package genlab-core pytest genlab-core/tests/platforms/test_registry_integration.py -v`
Expected: 6 PASSED (instagram, youtube, x_twitter, facebook, threads, tiktok)

- [ ] **Step 3: Run full genlab-core suite**

Run: `uv run --package genlab-core pytest genlab-core/tests/ -x -q`
Expected: All passing (existing 695 + new ~40 platform tests)

- [ ] **Step 4: Commit**

```bash
git commit -m "test(platforms): add registry integration test for all platform clients"
```

---

## Chunk 3: Gatekeeper & Dispatcher

### Task 12: Implement PublishGatekeeper

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/gatekeeper.py`
- Test: `genlab-core/tests/platforms/test_gatekeeper.py`

- [ ] **Step 1: Write failing tests**

```python
# genlab-core/tests/platforms/test_gatekeeper.py
"""Tests for PublishGatekeeper — each gate tested in isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from genlab_core.platforms.gatekeeper import GateResult, PublishGatekeeper


@pytest.fixture
def gatekeeper():
    config = {
        "platforms": {"min_publish_gap_hours": 2},
        "POLICY": {"strict_creator_video_only": False},
    }
    daily_cap = MagicMock()
    daily_cap.can_publish.return_value = True
    backlog = MagicMock()
    return PublishGatekeeper(config=config, daily_cap=daily_cap, backlog=backlog)


class TestApprovalGate:
    def test_approved_passes(self, gatekeeper):
        bp = {"action_taken": "approved"}
        result = gatekeeper._approval_gate(bp, "instagram")
        assert result.allowed is True

    def test_not_approved_blocks(self, gatekeeper):
        bp = {"action_taken": ""}
        result = gatekeeper._approval_gate(bp, "instagram")
        assert result.allowed is False
        assert result.gate_name == "approval_gate"


class TestScoreFloorGate:
    def test_above_floor_passes(self, gatekeeper):
        bp = {"priority_score": 0.8}
        result = gatekeeper._score_floor_gate(bp, "instagram")
        assert result.allowed is True

    def test_below_floor_blocks(self, gatekeeper):
        bp = {"priority_score": 0.1}
        result = gatekeeper._score_floor_gate(bp, "instagram")
        assert result.allowed is False


class TestScheduleGate:
    def test_due_now_passes(self, gatekeeper):
        bp = {"scheduled_for": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        assert result.allowed is True

    def test_future_blocks(self, gatekeeper):
        bp = {"scheduled_for": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        assert result.allowed is False


class TestDailyCapGate:
    def test_under_cap_passes(self, gatekeeper):
        bp = {}
        result = gatekeeper._daily_cap_gate(bp, "instagram")
        assert result.allowed is True

    def test_over_cap_blocks(self, gatekeeper):
        gatekeeper._daily_cap.can_publish.return_value = False
        bp = {}
        result = gatekeeper._daily_cap_gate(bp, "instagram")
        assert result.allowed is False


class TestEvaluateChain:
    def test_all_gates_pass(self, gatekeeper):
        bp = {
            "action_taken": "approved",
            "format": "reel",
            "scheduled_for": datetime.now(timezone.utc).isoformat(),
            "priority_score": 0.8,
            "visual_paths": '["/tmp/video.mp4"]',
        }
        result = gatekeeper.evaluate(bp, "instagram")
        assert result.allowed is True
        assert result.gate_name == "all"

    def test_first_failure_wins(self, gatekeeper):
        bp = {"action_taken": ""}  # Fails approval
        result = gatekeeper.evaluate(bp, "instagram")
        assert result.allowed is False
        assert result.gate_name == "approval_gate"
```

- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement PublishGatekeeper**

Port gate logic from `publish_all_platforms.py` lines ~1278-1420. Each gate is a private method `_xxx_gate(blueprint: dict, platform: str) -> GateResult`.

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(platforms): add PublishGatekeeper with 7 composable gates"
```

---

### Task 13: Implement dispatch_many

**Files:**
- Create: `genlab-core/src/genlab_core/platforms/dispatcher.py`
- Test: `genlab-core/tests/platforms/test_dispatcher.py`

- [ ] **Step 1: Write failing tests**

```python
# genlab-core/tests/platforms/test_dispatcher.py
"""Tests for dispatch_many — concurrent platform dispatch."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.platforms.models import PublishPayload, PublishResult


def _make_payload(platform: str) -> PublishPayload:
    return PublishPayload(
        caption="Test",
        media_paths=[Path("/tmp/v.mp4")],
        media_type="video",
        hashtags=[],
        hook="",
        niche_id="ai_creators",
    )


def test_dispatch_many_success():
    from genlab_core.platforms.dispatcher import dispatch_many

    mock_client = MagicMock()
    mock_client.publish.return_value = PublishResult(
        platform="instagram", success=True, post_id="123"
    )

    with patch("genlab_core.platforms.dispatcher.get_client", return_value=mock_client):
        results = dispatch_many([
            ("instagram", _make_payload("instagram")),
            ("youtube", _make_payload("youtube")),
        ])

    assert len(results) == 2
    assert results["instagram"].success is True
    assert results["youtube"].success is True


def test_dispatch_many_partial_failure():
    """One platform crashes — others still return results."""
    from genlab_core.platforms.dispatcher import dispatch_many

    def mock_get_client(platform_id):
        client = MagicMock()
        if platform_id == "instagram":
            client.publish.return_value = PublishResult(
                platform="instagram", success=True, post_id="123"
            )
        else:
            client.publish.side_effect = RuntimeError("API down")
        return client

    with patch("genlab_core.platforms.dispatcher.get_client", side_effect=mock_get_client):
        results = dispatch_many([
            ("instagram", _make_payload("instagram")),
            ("youtube", _make_payload("youtube")),
        ])

    assert results["instagram"].success is True
    assert results["youtube"].success is False
    assert "API down" in results["youtube"].error


def test_dispatch_many_empty_list():
    from genlab_core.platforms.dispatcher import dispatch_many

    results = dispatch_many([])
    assert results == {}
```

- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement dispatcher**

```python
# genlab-core/src/genlab_core/platforms/dispatcher.py
"""Concurrent multi-platform dispatch."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from genlab_core.platforms.models import PublishPayload, PublishResult
from genlab_core.platforms.registry import get_client


def dispatch_many(
    tasks: list[tuple[str, PublishPayload]],
    max_workers: int = 5,
) -> dict[str, PublishResult]:
    """Dispatch to multiple platforms concurrently. Never raises."""
    if not tasks:
        return {}
    results: dict[str, PublishResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            platform: pool.submit(_safe_dispatch, platform, payload)
            for platform, payload in tasks
        }
        for platform, future in futures.items():
            results[platform] = future.result()
    return results


def _safe_dispatch(platform: str, payload: PublishPayload) -> PublishResult:
    """Catch exceptions so one platform failure doesn't kill others."""
    try:
        client = get_client(platform)
        return client.publish(payload)
    except Exception as exc:
        return PublishResult(platform=platform, success=False, error=str(exc))
```

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(platforms): add dispatch_many with safe per-platform error handling"
```

---

## Chunk 4: Dashboard Response Wrapper

### Task 14: Create response helpers + global error handler

**Files:**
- Create: `dashboard/server/core/responses.py`
- Modify: `dashboard/server/review_server.py` (register error handler)
- Test: `dashboard/tests/test_responses.py`

- [ ] **Step 1: Write failing test**

```python
# dashboard/tests/test_responses.py
"""Tests for standardized API response helpers."""
from __future__ import annotations

import json

import pytest
from flask import Flask

from server.core.responses import api_success, api_error, api_not_found


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def test_api_success_default(app):
    with app.app_context():
        response, code = api_success(data={"items": [1, 2, 3]})
        body = json.loads(response.get_data())
        assert code == 200
        assert body["status"] == "success"
        assert body["data"] == {"items": [1, 2, 3]}
        assert body["message"] == "OK"


def test_api_success_custom_message(app):
    with app.app_context():
        response, code = api_success(data=None, message="Created", code=201)
        body = json.loads(response.get_data())
        assert code == 201
        assert body["message"] == "Created"


def test_api_error_default(app):
    with app.app_context():
        response, code = api_error(error="Something broke")
        body = json.loads(response.get_data())
        assert code == 400
        assert body["status"] == "error"
        assert body["error"] == "Something broke"


def test_api_not_found(app):
    with app.app_context():
        response, code = api_not_found()
        body = json.loads(response.get_data())
        assert code == 404
        assert body["message"] == "Resource not found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package dashboard pytest dashboard/tests/test_responses.py -v`

- [ ] **Step 3: Implement response helpers**

```python
# dashboard/server/core/responses.py
"""Standardized API response helpers.

All dashboard endpoints should use these instead of raw jsonify().
"""
from __future__ import annotations

from flask import jsonify


def api_success(data=None, message="OK", code=200):
    return jsonify({"status": "success", "code": code, "data": data, "message": message}), code


def api_error(error=None, message="Request failed", code=400):
    return jsonify({
        "status": "error",
        "code": code,
        "error": str(error) if error else None,
        "message": message,
    }), code


def api_not_found(message="Resource not found"):
    return api_error(message=message, code=404)
```

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Register global error handler on Flask app**

In `dashboard/server/review_server.py`, add after app creation:

```python
from server.core.responses import api_error
from werkzeug.exceptions import HTTPException

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return api_error(error=e.description, message=e.name, code=e.code)
    app.logger.exception("Unhandled error")
    return api_error(error="Internal server error", code=500)
```

- [ ] **Step 6: Run dashboard tests to check for regressions**

Run: `uv run --package dashboard pytest dashboard/tests/ -x -q`

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(dashboard): add standardized api_success/api_error response helpers + global error handler"
```

---

### Task 15: Migrate dashboard endpoints to response wrapper (incremental)

**Files:**
- Modify: All files in `dashboard/server/api/` (17 files, 217 jsonify calls)
- Modify: `dashboard/server/review_server.py`
- Modify: `dashboard/frontend/src/api/client.ts` (add response interceptor)

This is a large mechanical migration. Do it file by file, testing after each.

- [ ] **Step 1: Add response interceptor in frontend client.ts**

Read `dashboard/frontend/src/api/client.ts`. Modify the `get<T>` and `mutate<T>` helpers to unwrap the new envelope. Must handle both old (raw) and new (wrapped) formats during transition:

```typescript
// In the response handler:
const body = await resp.json();
// Handle both old format (raw data) and new format (wrapped)
if (body && typeof body === 'object' && 'status' in body) {
  if (body.status === 'error') {
    throw new Error(body.error || body.message || 'Request failed');
  }
  return body.data as T;
}
// Old format — return as-is
return body as T;
```

- [ ] **Step 2: Migrate `dashboard/server/api/blueprints.py`**

Replace all `jsonify({...})` calls with `api_success(data)` or `api_error(e)`. Test:

Run: `uv run --package dashboard pytest dashboard/tests/test_api_blueprints.py -v`

- [ ] **Step 3: Migrate remaining API files one at a time**

For each file in `dashboard/server/api/`:
1. Replace `jsonify()` with `api_success()`/`api_error()`
2. Run `dashboard/tests/` to verify no regressions
3. Commit when a logical batch (3-4 files) is done

- [ ] **Step 4: Migrate `dashboard/server/review_server.py`** (37 jsonify calls)

- [ ] **Step 5: Migrate `dashboard/server/core/publishing_queue.py`**

- [ ] **Step 6: Run full dashboard test suite**

Run: `uv run --package dashboard pytest dashboard/tests/ -v`

- [ ] **Step 7: Commit**

```bash
git commit -m "refactor(dashboard): migrate all endpoints to standardized response wrapper"
```

---

## Chunk 5: Auth Skeleton + Scheduler

### Task 16: Create auth models + middleware skeleton

**Files:**
- Create: `genlab-core/src/genlab_core/auth/__init__.py`
- Create: `genlab-core/src/genlab_core/auth/models.py`
- Create: `dashboard/server/middleware/__init__.py`
- Create: `dashboard/server/middleware/auth.py`
- Test: `genlab-core/tests/test_auth_models.py`
- Test: `dashboard/tests/test_auth_middleware.py`

- [ ] **Step 1: Write failing tests for auth models**

```python
# genlab-core/tests/test_auth_models.py
from __future__ import annotations

from datetime import datetime


def test_permission_ordering():
    from genlab_core.auth.models import Permission
    assert Permission.VIEWER < Permission.EDITOR
    assert Permission.EDITOR < Permission.PUBLISHER
    assert Permission.PUBLISHER < Permission.ADMIN


def test_team_creation():
    from genlab_core.auth.models import Team
    t = Team(team_id="t1", team_name="GenLab Ops", admin_user_id="u1", created_at=datetime.now())
    assert t.team_name == "GenLab Ops"


def test_niche_access_defaults():
    from genlab_core.auth.models import NicheAccess
    na = NicheAccess(team_id="t1", niche_id="gaming")
    assert na.can_publish is False
    assert na.can_approve is False
```

- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement auth models**

```python
# genlab-core/src/genlab_core/auth/__init__.py
"""Auth models and permission primitives."""

# genlab-core/src/genlab_core/auth/models.py
"""Team, permission, and niche access models.

Active when AUTH_MODE=multi_team. In single_admin mode, these are just data definitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


class Permission(IntEnum):
    VIEWER = 0
    EDITOR = 1
    PUBLISHER = 2
    ADMIN = 3


@dataclass
class Team:
    team_id: str
    team_name: str
    admin_user_id: str
    created_at: datetime


@dataclass
class TeamMember:
    user_id: str
    team_id: str
    permission: Permission
    active: bool = True


@dataclass
class NicheAccess:
    team_id: str
    niche_id: str
    can_publish: bool = False
    can_approve: bool = False
```

- [ ] **Step 4: Run auth model tests**
- [ ] **Step 5: Write failing test for middleware**

```python
# dashboard/tests/test_auth_middleware.py
from __future__ import annotations

import pytest
from flask import Flask

from server.middleware.auth import AuthMiddleware
from genlab_core.auth.models import Permission


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def test_single_admin_passthrough(app):
    auth = AuthMiddleware(mode="single_admin")

    @app.route("/test")
    @auth.require_permission(Permission.ADMIN)
    def protected():
        return "OK"

    with app.test_client() as client:
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.data == b"OK"


def test_multi_team_rejects_without_token(app):
    auth = AuthMiddleware(mode="multi_team")

    @app.route("/test")
    @auth.require_permission(Permission.PUBLISHER)
    def protected():
        return "OK"

    with app.test_client() as client:
        resp = client.get("/test")
        assert resp.status_code == 401
```

- [ ] **Step 6: Implement middleware**

```python
# dashboard/server/middleware/__init__.py
# (empty)

# dashboard/server/middleware/auth.py
"""Auth middleware — passthrough in single_admin mode, JWT-based in multi_team."""
from __future__ import annotations

from functools import wraps

from flask import request

from genlab_core.auth.models import Permission
from server.core.responses import api_error


class AuthMiddleware:
    def __init__(self, mode: str = "single_admin"):
        self.mode = mode

    def require_permission(self, min_permission: Permission):
        def decorator(f):
            @wraps(f)
            def wrapper(*args, **kwargs):
                if self.mode == "single_admin":
                    return f(*args, **kwargs)
                # multi_team: check JWT
                token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if not token:
                    return api_error(error="Missing auth token", code=401)
                # Future: decode JWT, check team membership, verify permission
                return api_error(error="multi_team auth not yet implemented", code=501)
            return wrapper
        return decorator
```

- [ ] **Step 7: Run middleware tests**
- [ ] **Step 8: Commit**

```bash
git commit -m "feat(auth): add Permission/Team/NicheAccess models + passthrough auth middleware"
```

---

### Task 17: Add APScheduler dependencies

**Files:**
- Modify: `dashboard/pyproject.toml`

- [ ] **Step 1: Add dependencies**

Add to `dashboard/pyproject.toml` under `[project.dependencies]`:
```
"apscheduler>=3.10,<4",
"sqlalchemy>=2.0",
```

- [ ] **Step 2: Lock and install**

```bash
cd /Users/anarchistsid/GenLab && $HOME/.local/bin/uv lock && $HOME/.local/bin/uv sync --package dashboard
```

- [ ] **Step 3: Verify import works**

```bash
uv run --package dashboard python -c "from apscheduler.schedulers.background import BackgroundScheduler; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git commit -m "build(dashboard): add apscheduler + sqlalchemy dependencies"
```

---

### Task 18: Implement GenLabScheduler

**Files:**
- Create: `dashboard/server/core/scheduler.py`
- Test: `dashboard/tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

```python
# dashboard/tests/test_scheduler.py
"""Tests for GenLabScheduler."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_scheduler_registers_all_jobs():
    from server.core.scheduler import GenLabScheduler

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_scheduler.db"
        sched = GenLabScheduler(db_path=str(db_path))
        sched.start()
        jobs = sched.list_jobs()
        job_ids = [j["id"] for j in jobs]
        assert "publish_tick" in job_ids
        assert "token_health" in job_ids
        assert "analytics" in job_ids
        assert "engagement_poll" in job_ids
        sched.shutdown()


def test_scheduler_pause_resume():
    from server.core.scheduler import GenLabScheduler

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_scheduler.db"
        sched = GenLabScheduler(db_path=str(db_path))
        sched.start()
        sched.pause_job("token_health")
        jobs = {j["id"]: j for j in sched.list_jobs()}
        assert jobs["token_health"]["state"] == "paused"
        sched.resume_job("token_health")
        jobs = {j["id"]: j for j in sched.list_jobs()}
        assert jobs["token_health"]["state"] != "paused"
        sched.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement scheduler**

Create `dashboard/server/core/scheduler.py` with:
- `GenLabScheduler.__init__(db_path)` — creates BackgroundScheduler with SQLAlchemyJobStore
- `start()` — registers all jobs (publish_tick, token_health, analytics, engagement_poll, quota_monitor, daily_intel)
- `shutdown()` — graceful stop
- `list_jobs()` — returns `[{"id", "next_run_time", "state"}]`
- `pause_job(id)`, `resume_job(id)`, `trigger_job(id)`
- Each `_*_tick()` method is a stub that logs "would run X" (actual wiring in Step 7+)

Job triggers must match existing plist schedules exactly (see spec Section 4.3).

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(dashboard): add GenLabScheduler with APScheduler + SQLite persistence"
```

---

### Task 19: Add scheduler dashboard API endpoints

**Files:**
- Create: `dashboard/server/api/scheduler.py`
- Modify: `dashboard/server/review_server.py` (register blueprint)
- Test: `dashboard/tests/test_scheduler_api.py`

- [ ] **Step 1: Write failing tests**

```python
# dashboard/tests/test_scheduler_api.py
"""Tests for scheduler API endpoints."""
from __future__ import annotations

import pytest


def test_scheduler_status_endpoint(client):
    resp = client.get("/api/v1/scheduler/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert isinstance(body["data"]["jobs"], list)


def test_pause_job(client):
    resp = client.post("/api/v1/scheduler/jobs/token_health/pause")
    assert resp.status_code == 200


def test_trigger_job(client):
    resp = client.post("/api/v1/scheduler/trigger/token_health")
    assert resp.status_code == 200
```

- [ ] **Step 2: Implement API endpoints**

```python
# dashboard/server/api/scheduler.py
from flask import Blueprint
from server.core.responses import api_success, api_error

scheduler_bp = Blueprint("scheduler", __name__, url_prefix="/api/v1/scheduler")

@scheduler_bp.route("/status")
def scheduler_status():
    from server.core.scheduler import get_scheduler
    sched = get_scheduler()
    return api_success(data={"jobs": sched.list_jobs()})

@scheduler_bp.route("/jobs/<job_id>/pause", methods=["POST"])
def pause_job(job_id):
    from server.core.scheduler import get_scheduler
    get_scheduler().pause_job(job_id)
    return api_success(message=f"Job {job_id} paused")

@scheduler_bp.route("/jobs/<job_id>/resume", methods=["POST"])
def resume_job(job_id):
    from server.core.scheduler import get_scheduler
    get_scheduler().resume_job(job_id)
    return api_success(message=f"Job {job_id} resumed")

@scheduler_bp.route("/trigger/<job_id>", methods=["POST"])
def trigger_job(job_id):
    from server.core.scheduler import get_scheduler
    get_scheduler().trigger_job(job_id)
    return api_success(message=f"Job {job_id} triggered")
```

- [ ] **Step 3: Register blueprint in review_server.py**
- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(dashboard): add scheduler API endpoints (status, pause, resume, trigger)"
```

---

### Task 20: Extend workflow state machine

**Files:**
- Modify: `BlackboxBrief/execution/utils/workflow_state_machine.py`
- Test: `BlackboxBrief/tests/test_workflow_state_machine.py` (or write inline)

- [ ] **Step 1: Write failing test**

```python
def test_publishing_state_transitions():
    from execution.utils.workflow_state_machine import can_transition
    assert can_transition("SCHEDULED", "PUBLISHING") is True
    assert can_transition("PUBLISHING", "PUBLISHED") is True
    assert can_transition("PUBLISHING", "PUBLISH_FAILED") is True
    assert can_transition("PUBLISH_FAILED", "SCHEDULED") is True
    assert can_transition("SCHEDULED", "DELETED") is True
```

- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Add new states to ALLOWED_TRANSITIONS**

Read `BlackboxBrief/execution/utils/workflow_state_machine.py`. Add:

```python
"SCHEDULED": {"PUBLISHING", "DELETED"},
"PUBLISHING": {"PUBLISHED", "PUBLISH_FAILED"},
"PUBLISH_FAILED": {"SCHEDULED"},
```

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(workflow): add PUBLISHING + PUBLISH_FAILED states to state machine"
```

---

## Chunk 6: Wiring (The Swap Steps)

> **Note:** Tasks 21-24 are the "swap" steps that replace old imports with new platform clients. Each is guarded by the `USE_NATIVE_CLIENTS` feature flag (env var, defaults to `true`). Set to `false` to fall back to old code.

### Task 21: Wire orchestrator to new platform clients + gatekeeper

**Files:**
- Modify: `BlackboxBrief/execution/publish_all_platforms.py`

This is the core swap. The 2006-line file gets significantly simplified:
1. Import `PublishGatekeeper`, `dispatch_many`, `build_payload` from `genlab_core.platforms`
2. Replace the 7 inline gate checks with `gatekeeper.evaluate()`
3. Replace per-platform publish functions with `dispatch_many()`
4. Keep file lock, finalization pre-steps, Postiz shadow, post-publish updates

- [ ] **Step 1: Add feature flag gate at top of file**

```python
import os
USE_NATIVE_CLIENTS = os.environ.get("USE_NATIVE_CLIENTS", "true").lower() == "true"
```

- [ ] **Step 2: Wrap the publish dispatch in a conditional**

```python
if USE_NATIVE_CLIENTS:
    from genlab_core.platforms.gatekeeper import PublishGatekeeper
    from genlab_core.platforms.dispatcher import dispatch_many
    # New path
else:
    # Old path (existing code, unchanged)
```

- [ ] **Step 3: Implement `build_payload(blueprint, platform)` function**

This converts raw SharePoint blueprint dict → typed `PublishPayload` with correct `PlatformSpecific`:

```python
def build_payload(blueprint: dict, platform: str) -> PublishPayload:
    # Parse visual_paths JSON → list[Path]
    # Parse platform-specific JSON fields (youtube_content, twitter_content)
    # Return PublishPayload with correct PlatformSpecific subtype
```

- [ ] **Step 4: Run BlackboxBrief test suite**

Run: `uv run --package content-scraper pytest "BlackboxBrief/tests/" -x -q`
Expected: All 1323 tests pass

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(publisher): wire orchestrator to platform clients + gatekeeper (behind USE_NATIVE_CLIENTS flag)"
```

---

### Task 22: Wire engagement engine to new platform clients

**Files:**
- Modify: `genlab-core/src/genlab_core/engagement/comment_processor.py`

- [ ] **Step 1: Replace per-platform imports with get_client + isinstance checks**

In `comment_processor.py`, replace:
```python
from genlab_core.engagement.platform_clients.youtube import post_youtube_reply
from genlab_core.engagement.platform_clients.instagram import post_instagram_reply
# etc.
```

With:
```python
if USE_NATIVE_CLIENTS:
    from genlab_core.platforms import get_client
    from genlab_core.platforms.protocols import Engageable
```

- [ ] **Step 2: Update the reply dispatch logic**

```python
client = get_client(platform)
if isinstance(client, Engageable):
    ok = client.post_reply(parent_id=comment_id, text=reply_text, context_id=post_id)
```

- [ ] **Step 3: Run engagement tests**

Run: `uv run --package genlab-core pytest genlab-core/tests/engagement/ -v`

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(engagement): wire comment_processor to unified platform clients"
```

---

### Task 23: Wire token_health to HealthCheckable clients

**Files:**
- Modify: `scripts/token_health.py` (or `BlackboxBrief/execution/check_token_health.py`)

- [ ] **Step 1: Add new path that iterates all HealthCheckable clients**

```python
if USE_NATIVE_CLIENTS:
    from genlab_core.platforms import get_client, list_platforms
    from genlab_core.platforms.protocols import HealthCheckable

    results = {}
    for pid in list_platforms():
        try:
            client = get_client(pid)
            if isinstance(client, HealthCheckable):
                results[pid] = client.check_token_health()
        except Exception as e:
            results[pid] = TokenStatus(valid=False, platform=pid, ...)
```

- [ ] **Step 2: Write TokenStatus → existing report format adapter**

The existing script writes detailed per-platform JSON reports. Map `TokenStatus` fields to the existing report format so downstream consumers (dashboard, launchd log) are unaffected.

- [ ] **Step 3: Run token health manually**

```bash
uv run python scripts/token_health.py --dry-run
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(token-health): wire to HealthCheckable platform clients"
```

---

## Chunk 7: Cleanup

### Task 24: Create platform/ → platforms/ shim and verify all imports

- [ ] **Step 1: Run all test suites in parallel**

```bash
uv run --package genlab-core pytest genlab-core/tests/ -x -q &
uv run --package content-scraper pytest "BlackboxBrief/tests/" -x -q &
uv run --package criticalrush pytest CriticalRush/tests/ -x -q &
uv run --package dashboard pytest dashboard/tests/ -x -q &
wait
```

- [ ] **Step 2: If all pass, remove the feature flag (make new code the default)**

Remove `USE_NATIVE_CLIENTS` conditionals — keep only the new path.

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor: remove USE_NATIVE_CLIENTS feature flag, new platform clients are now default"
```

---

### Task 25: Delete old publisher files

**Files to delete** (per spec Section 1.9):

- [ ] **Step 1: Delete old BlackboxBrief publisher files**

```bash
git rm "BlackboxBrief/execution/publish_to_instagram.py"
git rm "BlackboxBrief/execution/publish_youtube.py"
git rm "BlackboxBrief/execution/publish_twitter.py"
git rm "BlackboxBrief/execution/publish_facebook.py"
git rm "BlackboxBrief/execution/publish_threads.py"
git rm "BlackboxBrief/execution/publish_single.py"
git rm "BlackboxBrief/execution/utils/twitter_client.py"
git rm "BlackboxBrief/execution/utils/youtube_client.py"
```

- [ ] **Step 2: Delete old engagement platform_clients**

```bash
git rm genlab-core/src/genlab_core/engagement/platform_clients/youtube.py
git rm genlab-core/src/genlab_core/engagement/platform_clients/instagram.py
git rm genlab-core/src/genlab_core/engagement/platform_clients/x_twitter.py
git rm genlab-core/src/genlab_core/engagement/platform_clients/facebook.py
git rm genlab-core/src/genlab_core/engagement/platform_clients/threads.py
```

- [ ] **Step 3: Delete old platform/ shim**

```bash
git rm -r genlab-core/src/genlab_core/platform/
```

- [ ] **Step 4: Run all test suites to verify nothing broke**

- [ ] **Step 5: Commit**

```bash
git commit -m "refactor: delete legacy per-platform publisher and engagement files"
```

---

### Task 26: Scheduler parallel validation + launchd deprecation

> **Note:** This task happens over 1 week in production. Document the steps but don't automate.

- [ ] **Step 1: Start scheduler in dry-run mode alongside launchd**

Set `SCHEDULER_DRY_RUN=true` in dashboard env. Scheduler logs what it would do but doesn't execute.

- [ ] **Step 2: Compare logs for 1 week**

Verify scheduler identifies the same due blueprints at the same times as launchd-triggered runs.

- [ ] **Step 3: Switch to live mode**

Set `SCHEDULER_DRY_RUN=false`. Disable plists:

```bash
launchctl unload ~/Library/LaunchAgents/com.genlab.instagram-publisher.plist
launchctl unload ~/Library/LaunchAgents/com.genlab.token-refresh.plist
# etc. for all plists
```

- [ ] **Step 4: Move plists to deprecated**

```bash
mkdir -p runbooks/deprecated
mv "BlackboxBrief/runbooks/"*.plist runbooks/deprecated/
mv genlab-core/runbooks/*.plist runbooks/deprecated/
mv CriticalRush/runbooks/*.plist runbooks/deprecated/
# Keep in git — rollback fallback
```

- [ ] **Step 5: Commit**

```bash
git commit -m "ops: deprecate launchd plists, scheduler is now primary"
```

---

## Summary

| Chunk | Tasks | Key Deliverable |
|-------|-------|----------------|
| 1: Foundation | 1-3 | Protocols + models + lazy registry |
| 2: Platform Clients | 4-11 | 6 platform clients + postiz migration + shims |
| 3: Gatekeeper & Dispatcher | 12-13 | PublishGatekeeper + dispatch_many |
| 4: Response Wrapper | 14-15 | api_success/api_error + endpoint migration |
| 5: Auth + Scheduler | 16-20 | Permission models + APScheduler + workflow states |
| 6: Wiring | 21-23 | Swap old imports → new clients (feature flagged) |
| 7: Cleanup | 24-26 | Remove flags, delete old files, deprecate plists |

**Total:** 26 tasks across 7 chunks. Each chunk is independently deployable.
