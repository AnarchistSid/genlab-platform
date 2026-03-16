# WS3: Engagement Engine Completion (Hybrid Auto-Reply)

**Goal**: G4 Engagement Engine 48% → 70%
**Effort**: ~8h
**Dependencies**: None

## Problem

The engagement engine has a complete 8-step pipeline (detect → classify → toxicity → rate limit → persona → jitter → post → mark) but cannot send replies because `engagement/platform_clients/` is empty (0 files). Additionally:
- Detoxify is not installed in the workspace venv
- Only 2/5 niches have pollers (BB and CR; CW, SR, FD have none)
- No confidence-based routing (hybrid auto-reply vs approval queue)

## Design: Hybrid Auto-Reply

### Confidence tiers

| Tier | Condition | Action |
|---|---|---|
| **AUTO** | confidence >= 0.85 AND toxicity < 0.15 AND is_simple_reply | Post immediately |
| **REVIEW** | confidence >= 0.5 AND toxicity < 0.3 | Queue for human approval |
| **DISCARD** | confidence < 0.5 OR toxicity >= 0.3 | Log and discard |

`is_simple_reply` = reply contains no claims, no links, length < 100 chars, and matches safe patterns (acknowledgment, emoji, simple question response).

### Safe patterns for auto-reply

```python
SAFE_PATTERNS = [
    r"^(thanks|thank you|glad you (liked|enjoyed)|appreciate)",
    r"^(right\?|ikr|fr|exactly|so true|facts)",
    r"^(check out|watch|subscribe|follow)",  # CTA
    r"^[\U0001F600-\U0001F9FF\s]+$",  # emoji-only
]
```

## Changes

### 1. Install Detoxify

```bash
uv add detoxify --package genlab-core
```

### 2. Implement 5 platform reply clients — `engagement/platform_clients/`

Each client wraps the existing `platforms/` module with a reply-specific interface:

```python
# engagement/platform_clients/youtube_reply.py
class YouTubeReplyClient:
    def post_reply(self, comment_id: str, text: str, niche_id: str) -> bool:
        """Post a reply to a YouTube comment. Returns True on success."""

    def post_like(self, comment_id: str, niche_id: str) -> bool:
        """Like a comment."""
```

Files:
- `youtube_reply.py` — uses YouTube Data API v3 `comments.insert`
- `instagram_reply.py` — uses `graph.facebook.com` `{comment_id}/replies`
- `facebook_reply.py` — uses `graph.facebook.com` `{comment_id}/comments`
- `twitter_reply.py` — uses X API v2 `tweets` with `in_reply_to_tweet_id`
- `threads_reply.py` — uses Threads API `reply` endpoint
- `__init__.py` — registry mapping platform name → client class

### 3. Add confidence routing to `comment_processor.py`

Add `_classify_action()` method after persona generates reply:

```python
def _classify_action(self, reply_text: str, confidence: float,
                     toxicity: float) -> Literal["auto", "review", "discard"]:
    if toxicity >= 0.3:
        return "discard"
    if confidence < 0.5:
        return "discard"
    if confidence >= 0.85 and toxicity < 0.15 and self._is_safe(reply_text):
        return "auto"
    return "review"
```

For "review" action: write to `PendingReplies` SharePoint list with fields: `comment_id`, `platform`, `niche_id`, `original_comment`, `generated_reply`, `confidence`, `toxicity_score`, `status` (PENDING/APPROVED/REJECTED).

### 4. Add 6 niche pollers (YouTube + Twitter × 3 niches)

Create plists for CW, SR, FD:
- `com.genlab.engagement.poller.youtube.sports.plist`
- `com.genlab.engagement.poller.twitter.sports.plist`
- `com.genlab.engagement.poller.youtube.movies.plist`
- `com.genlab.engagement.poller.twitter.movies.plist`
- `com.genlab.engagement.poller.youtube.anime.plist`
- `com.genlab.engagement.poller.twitter.anime.plist`

### 5. Dashboard pending-replies endpoint

Add to dashboard server:
- `GET /api/v1/engagement/pending-replies` — list queued replies
- `POST /api/v1/engagement/pending-replies/{id}/approve` — approve and send
- `POST /api/v1/engagement/pending-replies/{id}/reject` — reject

## Files Modified/Created

| File | Change |
|---|---|
| `genlab-core/pyproject.toml` | Add detoxify dependency |
| `engagement/platform_clients/youtube_reply.py` | NEW |
| `engagement/platform_clients/instagram_reply.py` | NEW |
| `engagement/platform_clients/facebook_reply.py` | NEW |
| `engagement/platform_clients/twitter_reply.py` | NEW |
| `engagement/platform_clients/threads_reply.py` | NEW |
| `engagement/platform_clients/__init__.py` | NEW — registry |
| `engagement/comment_processor.py` | Add confidence routing |
| `genlab-core/runbooks/` | 6 new plist files |
| `dashboard/server/api/engagement.py` | NEW — pending replies API |
| `genlab-core/tests/engagement/` | Tests for reply clients + routing |

## Validation

- Detoxify imports without error: `python -c "import detoxify"`
- Reply client unit tests pass (mocked API calls)
- Comment processor routes high-confidence to "auto", medium to "review"
- `launchctl list | grep engagement.poller` shows 10 pollers (was 4)
- Dashboard `/api/v1/engagement/pending-replies` returns list

## Risks

- Auto-replying on live channels — mitigated by strict confidence threshold (0.85) + toxicity gate + safe pattern matching
- YouTube commentThreads 403 (existing issue) — pollers will log and retry, doesn't block the pipeline
- Rate limits per platform enforced by existing TokenBucket
