# WS3: Engagement Engine Completion — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the engagement engine so it can auto-reply (high confidence) and queue for review (medium confidence) on all 5 niches across YouTube, Instagram, Facebook, X, and Threads.

**Architecture:** Implement 5 platform reply clients wrapping existing `platforms/` module. Add confidence-based routing to `comment_processor.py`. Install Detoxify. Create 6 new poller plists for CW/SR/FD.

**Tech Stack:** Python, YouTube Data API v3, Meta Graph API, X API v2, Threads API, Detoxify, Dramatiq, launchd

**Spec:** `docs/superpowers/specs/2026-03-17-ws3-engagement-engine-completion-design.md`

---

## Chunk 1: Install Detoxify + Platform Reply Clients

### Task 1: Install Detoxify

- [ ] **Step 1: Add detoxify to genlab-core dependencies**

```bash
cd /Users/anarchistsid/GenLab && uv add detoxify --package genlab-core
```

- [ ] **Step 2: Verify import**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core python -c "import detoxify; print('Detoxify:', detoxify.__version__)"
```

Expected: prints version number

- [ ] **Step 3: Commit**

```bash
git add genlab-core/pyproject.toml uv.lock
git commit -m "deps: add detoxify to genlab-core for toxicity gate"
```

### Task 2: Implement platform reply clients

**Files:**
- Create: `genlab-core/src/genlab_core/engagement/platform_clients/__init__.py`
- Create: `genlab-core/src/genlab_core/engagement/platform_clients/youtube_reply.py`
- Create: `genlab-core/src/genlab_core/engagement/platform_clients/instagram_reply.py`
- Create: `genlab-core/src/genlab_core/engagement/platform_clients/facebook_reply.py`
- Create: `genlab-core/src/genlab_core/engagement/platform_clients/twitter_reply.py`
- Create: `genlab-core/src/genlab_core/engagement/platform_clients/threads_reply.py`
- Test: `genlab-core/tests/engagement/test_platform_reply_clients.py`

- [ ] **Step 1: Write failing tests for reply client registry**

```python
# genlab-core/tests/engagement/test_platform_reply_clients.py
"""Tests for engagement platform reply clients."""
from unittest.mock import patch, MagicMock

from genlab_core.engagement.platform_clients import get_reply_client


class TestReplyClientRegistry:
    def test_youtube_client_exists(self):
        client = get_reply_client("youtube")
        assert client is not None
        assert hasattr(client, "post_reply")

    def test_instagram_client_exists(self):
        client = get_reply_client("instagram")
        assert hasattr(client, "post_reply")

    def test_twitter_client_exists(self):
        client = get_reply_client("twitter")
        assert hasattr(client, "post_reply")

    def test_facebook_client_exists(self):
        client = get_reply_client("facebook")
        assert hasattr(client, "post_reply")

    def test_threads_client_exists(self):
        client = get_reply_client("threads")
        assert hasattr(client, "post_reply")

    def test_unknown_platform_returns_none(self):
        client = get_reply_client("tiktok")
        assert client is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/engagement/test_platform_reply_clients.py -v --tb=short
```

- [ ] **Step 3: Implement reply client registry and all 5 clients**

Create `genlab-core/src/genlab_core/engagement/platform_clients/__init__.py`:

```python
"""Platform reply clients for the engagement engine.

Each client provides post_reply(comment_id, text, niche_id) -> bool.
"""
from __future__ import annotations
from typing import Optional, Protocol


class ReplyClient(Protocol):
    def post_reply(self, comment_id: str, text: str, niche_id: str) -> bool: ...


def get_reply_client(platform: str) -> Optional[ReplyClient]:
    """Return a reply client for the given platform, or None."""
    from genlab_core.engagement.platform_clients.youtube_reply import YouTubeReplyClient
    from genlab_core.engagement.platform_clients.instagram_reply import InstagramReplyClient
    from genlab_core.engagement.platform_clients.facebook_reply import FacebookReplyClient
    from genlab_core.engagement.platform_clients.twitter_reply import TwitterReplyClient
    from genlab_core.engagement.platform_clients.threads_reply import ThreadsReplyClient

    registry = {
        "youtube": YouTubeReplyClient,
        "instagram": InstagramReplyClient,
        "facebook": FacebookReplyClient,
        "twitter": TwitterReplyClient,
        "x": TwitterReplyClient,
        "threads": ThreadsReplyClient,
    }
    cls = registry.get(platform.lower())
    return cls() if cls else None
```

Create each client (pattern shown for YouTube — others follow same structure):

```python
# genlab-core/src/genlab_core/engagement/platform_clients/youtube_reply.py
"""YouTube comment reply client."""
from __future__ import annotations
import logging
import os
import json
import urllib.request

logger = logging.getLogger(__name__)


class YouTubeReplyClient:
    def post_reply(self, comment_id: str, text: str, niche_id: str) -> bool:
        """Reply to a YouTube comment using comments.insert API."""
        from genlab_core.publishing.niche_credentials import resolve_youtube_credentials

        creds = resolve_youtube_credentials(niche_id)
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
        # YouTube comment replies require OAuth, not just API key
        # For now use the niche's refresh token via google-auth
        access_token = creds.get("access_token", "")
        if not access_token:
            logger.warning("[YT Reply] No access_token for niche %s", niche_id)
            return False

        try:
            url = f"https://www.googleapis.com/youtube/v3/comments?part=snippet&key={api_key}"
            body = json.dumps({
                "snippet": {
                    "parentId": comment_id,
                    "textOriginal": text,
                }
            }).encode()
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            resp = urllib.request.urlopen(req, timeout=10)
            if resp.status in (200, 201):
                logger.info("[YT Reply] Posted reply to %s", comment_id)
                return True
            return False
        except Exception:
            logger.exception("[YT Reply] Failed to post reply to %s", comment_id)
            return False
```

Create similar files for instagram_reply.py (using `graph.facebook.com/{comment_id}/replies`), facebook_reply.py, twitter_reply.py (X API v2 `tweets` with `reply.in_reply_to_tweet_id`), threads_reply.py.

- [ ] **Step 4: Run tests to verify**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/engagement/test_platform_reply_clients.py -v --tb=short
```

Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/engagement/platform_clients/
git add genlab-core/tests/engagement/test_platform_reply_clients.py
git commit -m "feat(engagement): implement 5 platform reply clients (YT, IG, FB, X, Threads)

Each wraps the platform API with post_reply(comment_id, text, niche_id).
Registry provides get_reply_client(platform) lookup."
```

---

## Chunk 2: Confidence Routing + Niche Pollers

### Task 3: Add confidence-based routing to comment_processor

**Files:**
- Modify: `genlab-core/src/genlab_core/engagement/comment_processor.py`
- Test: `genlab-core/tests/engagement/test_confidence_routing.py`

- [ ] **Step 1: Write failing tests for confidence routing**

```python
# genlab-core/tests/engagement/test_confidence_routing.py
"""Tests for hybrid auto-reply confidence routing."""
from genlab_core.engagement.comment_processor import classify_reply_action

class TestConfidenceRouting:
    def test_high_confidence_safe_reply_auto(self):
        action = classify_reply_action(
            reply_text="thanks! glad you liked it",
            confidence=0.9, toxicity=0.05,
        )
        assert action == "auto"

    def test_medium_confidence_queued(self):
        action = classify_reply_action(
            reply_text="That's an interesting take, here's why...",
            confidence=0.7, toxicity=0.1,
        )
        assert action == "review"

    def test_low_confidence_discarded(self):
        action = classify_reply_action(
            reply_text="whatever",
            confidence=0.3, toxicity=0.1,
        )
        assert action == "discard"

    def test_high_toxicity_discarded(self):
        action = classify_reply_action(
            reply_text="great point!",
            confidence=0.95, toxicity=0.4,
        )
        assert action == "discard"

    def test_long_reply_not_auto(self):
        action = classify_reply_action(
            reply_text="A" * 150,  # >100 chars
            confidence=0.9, toxicity=0.05,
        )
        assert action == "review"  # Too long for auto
```

- [ ] **Step 2: Implement classify_reply_action**

Add to `comment_processor.py`:

```python
import re
from typing import Literal

SAFE_PATTERNS = [
    re.compile(r"^(thanks|thank you|glad you (liked|enjoyed)|appreciate)", re.I),
    re.compile(r"^(right\??|ikr|fr|exactly|so true|facts)", re.I),
    re.compile(r"^(check out|watch|subscribe|follow)", re.I),
    re.compile(r"^[\U0001F600-\U0001F9FF\s!]+$"),  # emoji-only
]

def _is_safe_reply(text: str) -> bool:
    if len(text) > 100:
        return False
    return any(p.search(text.strip()) for p in SAFE_PATTERNS)

def classify_reply_action(
    reply_text: str, confidence: float, toxicity: float,
) -> Literal["auto", "review", "discard"]:
    if toxicity >= 0.3:
        return "discard"
    if confidence < 0.5:
        return "discard"
    if confidence >= 0.85 and toxicity < 0.15 and _is_safe_reply(reply_text):
        return "auto"
    return "review"
```

Then wire it into `process_reply_event()` after persona generates the reply — dispatch to auto-post or queue based on action.

- [ ] **Step 3: Run tests**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/engagement/test_confidence_routing.py -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add genlab-core/src/genlab_core/engagement/comment_processor.py genlab-core/tests/engagement/test_confidence_routing.py
git commit -m "feat(engagement): add hybrid auto-reply confidence routing

auto (>=0.85 conf, <0.15 tox, safe pattern, <100 chars) → post immediately
review (>=0.5 conf, <0.3 tox) → queue for human approval
discard (low conf or high tox) → log and drop"
```

### Task 4: Create 6 new niche pollers

- [ ] **Step 1: Create plists for CW, SR, FD (YouTube + Twitter each)**

Follow existing pattern from `com.genlab.engagement.poller.youtube.gaming.plist`. Create 6 plists:
- `com.genlab.engagement.poller.youtube.sports.plist` (--niche sports)
- `com.genlab.engagement.poller.twitter.sports.plist` (--niche sports)
- `com.genlab.engagement.poller.youtube.movies.plist` (--niche movies)
- `com.genlab.engagement.poller.twitter.movies.plist` (--niche movies)
- `com.genlab.engagement.poller.youtube.anime.plist` (--niche anime)
- `com.genlab.engagement.poller.twitter.anime.plist` (--niche anime)

All with `KeepAlive: true`, `ENGAGEMENT_DISPATCH: true`.

- [ ] **Step 2: Load and verify**

```bash
for f in ~/Library/LaunchAgents/com.genlab.engagement.poller.*.plist; do
  launchctl load "$f" 2>/dev/null
done
launchctl list | grep engagement.poller | wc -l
```

Expected: 10 (was 4)

- [ ] **Step 3: Commit**

```bash
git add genlab-core/runbooks/com.genlab.engagement.poller.*
git commit -m "feat(engagement): add YouTube + Twitter pollers for sports, movies, anime

6 new plists. All 5 niches now have engagement polling coverage."
```
