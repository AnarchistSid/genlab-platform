# Platform Client Migration Assessment: BB -> genlab-core

**Date:** 2026-03-17
**Status:** Research/Feasibility Assessment (NO code changes)

---

## Executive Summary

BlackboxBrief houses three legacy platform clients in `BlackboxBrief/execution/utils/` that were written before genlab-core had its own unified platform layer. genlab-core now has a **complete, protocol-based replacement** for all three clients in `genlab-core/src/genlab_core/platforms/`. The BB clients are only used within the BB package itself; no other channel imports them. Migration is primarily a BB-internal refactor to switch from its legacy clients to genlab-core's unified platform package, followed by deprecating the legacy files.

---

## 1. Client Inventory

### 1a. BB Legacy Clients (BlackboxBrief/execution/utils/)

#### `twitter_client.py` — TwitterClient
- **Imports:** `tweepy`, `threading`, `execution.utils.niche_credentials`
- **Exports:** `TwitterClient` class
- **Env vars:** `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` (via niche_credentials)
- **Capabilities:**
  - `upload_media(file_path)` — v1.1 chunked upload for video
  - `post_tweet(text, media_ids, reply_to)` — v2 create_tweet with rate-limit cooldown (thread-safe)
  - `post_thread(tweets, image_paths)` — reply chain with partial-failure handling
  - `health_check()` — `get_me()` call
- **Unique features:** Thread-safe rate-limit lock (`threading.Lock`); 1-hour cooldown on 429; per-call cross-thread rate-limit flag propagation in `publish_all_platforms.py`

#### `instagram_client.py` — InstagramPublisher
- **Imports:** `requests`, `os`, `time`, `dotenv`
- **Exports:** `InstagramPublisher` class, `PublishResult` dataclass
- **Env vars:** `META_ACCESS_TOKEN`, `META_IG_USER_ID`
- **Capabilities:**
  - `create_media_container(video_url, caption, cover_url, share_to_feed)` — REELS container creation
  - `check_container_status(container_id)` — polling
  - `publish_media(container_id)` — finalize
  - `publish_reel(...)` — convenience orchestrator (all 3 steps + adaptive poll interval)
- **Unique features:** Own `PublishResult` dataclass; `max_poll_seconds=600` default (vs 120 in genlab-core); adaptive poll interval with 1.5x slowdown after 30s

#### `youtube_client.py` — YouTubeClient
- **Imports:** `requests`, `google.oauth2.credentials`, `googleapiclient.discovery`, `googleapiclient.http.MediaFileUpload`, `execution.utils.niche_credentials`
- **Exports:** `YouTubeClient` class, `_resolve_youtube_credentials()` function
- **Env vars:** `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`, `{PREFIX}_YOUTUBE_REFRESH_TOKEN`, `{PREFIX}_YT_CHANNEL_ID`
- **Capabilities:**
  - `upload_short(video_path, title, ...)` — Shorts upload with `#Shorts` tag injection + quota gate
  - `upload_video(video_path, title, ...)` — regular video upload (strips `#Shorts`)
  - `create_community_post(text, image_paths)` — backstage API attempt + manual fallback
  - `post_comment(video_id, text)` — top-level comment on video
  - `update_video_metadata(video_id, title, description)` — snippet update
  - `verify_channel()` — cross-channel guard (expected vs actual channel ID)
  - `health_check()` — channel lookup
- **Unique features:** Quota gate (`YouTubeQuotaTracker`); `verify_channel()` cross-channel guard; community post (backstage API); comment posting; metadata update; per-chunk retry (3 attempts, exponential backoff)

### 1b. genlab-core Platform Clients (genlab-core/src/genlab_core/platforms/)

All implement 4 layered protocols: `Publisher`, `Engageable`, `Trackable`, `HealthCheckable`.

#### `x_twitter.py` — XTwitterClient
- Functionally equivalent to BB's `TwitterClient`
- Uses same tweepy OAuth 1.0a pattern
- Adds: `like()`, `get_metrics()` (tweet public_metrics), `check_token_health()` (handles 403 free-tier)
- Structured `PublishResult` output (vs raw tweet ID)
- Rate-limit cooldown pattern identical (1-hour window)
- Missing vs BB: Thread-safe `threading.Lock` (uses simple bool flag, same as BB's pattern on the non-lock path)

#### `instagram.py` — InstagramClient
- Functionally equivalent to BB's `InstagramPublisher`
- Same 3-step flow: container -> poll -> media_publish
- Adds: `post_reply()`, `like()` (no-op), `check_token_health()`
- Structured `PublishResult` output
- Different: `max_poll_seconds=120` default (BB: 600); no adaptive poll interval (uses fixed 5s/10s)

#### `youtube.py` — YouTubeClient
- Core upload equivalent to BB's `YouTubeClient.upload_short()`
- Adds: `post_reply()`, `like()` (no-op), `get_metrics()` (YouTube Analytics API v2 with 48h lag guard), `check_token_health()`
- Structured `PublishResult` output
- Missing vs BB: `upload_video()` (regular non-Short), `create_community_post()`, `post_comment()`, `update_video_metadata()`, `verify_channel()`, quota gate (`YouTubeQuotaTracker`), per-chunk retry logic

#### Also in genlab-core (no BB equivalent):
- `facebook.py` — FacebookClient (publish, reply, like, metrics, health)
- `threads.py` — ThreadsClient (publish, reply, health)
- `tiktok.py` — TikTokClient (stub/placeholder)
- `dispatcher.py` — `dispatch_many()` concurrent multi-platform dispatch
- `gatekeeper.py` — 7 composable gates (approval, format, schedule, score, media, cap, cooldown)
- `registry.py` — lazy `get_client(platform_id)` factory
- `models.py` — typed `PublishPayload`, `PublishResult`, `PlatformMetrics`, `TokenStatus`, platform-specific configs

### 1c. genlab-core Engagement Reply Clients (genlab-core/src/genlab_core/engagement/platform_clients/)

Separate, lightweight reply-only clients for the engagement engine:
- `youtube_reply.py` — YouTubeReplyClient
- `instagram_reply.py` — InstagramReplyClient
- `twitter_reply.py` — TwitterReplyClient (tweepy)
- `facebook_reply.py` — FacebookReplyClient
- `threads_reply.py` — ThreadsReplyClient

These are thin wrappers (~50 LOC each) that call a single API endpoint. The unified platform clients (`platforms/`) already have `post_reply()` as part of the `Engageable` protocol, so these engagement reply clients are technically redundant but serve the engagement engine's `ReplyClient` protocol (`post_reply(comment_id, text, niche_id)`).

---

## 2. Usage Analysis

### Who imports BB clients?

| File | Client Used | Context |
|------|------------|---------|
| `BB/execution/publish_all_platforms.py` | `TwitterClient` | Lines 1520, 1853 — create per-blueprint, pass to `publish_twitter_post()` |
| `BB/execution/publish_twitter.py` | `TwitterClient` | Lines 110, 217, 303 — fallback instantiation when `tw_client` not passed |
| `BB/execution/publish_to_instagram.py` | `InstagramPublisher` | Line 669 — standalone IG publish script |
| `BB/execution/publish_youtube.py` | `YouTubeClient` | Lines 241, 325, 385 — Short upload, community post, comment |
| `BB/execution/publish_single.py` | `TwitterClient` | Line 80 — single-blueprint publish |
| `BB/execution/fetch_audience_metrics.py` | `YouTubeClient` | Line 94 — demographics fetch |
| `BB/execution/fetch_youtube_demographics.py` | `YouTubeClient` | Line 138 — demographics |
| `BB/tests/test_twitter_client.py` | `TwitterClient` | 7 test methods |
| `BB/tests/test_publish_twitter.py` | `TwitterClient` | 3 test references |
| `dashboard/tests/test_p3_polish.py` | `YouTubeClient` | Lines 61, 155 — chunk retry tests (adds BB to sys.path) |

### Who imports BB clients from OTHER channels?

**Nobody.** No channel (CriticalRush, ClutchWire, SpliceReel, FrameDrift) imports from `execution.utils.*`. The `sys.path` hacks in those channels only add their own roots (for strategy imports). The only cross-package import is `dashboard/tests/test_p3_polish.py` which adds BlackboxBrief to `sys.path` for YouTube chunk retry tests.

### Who uses genlab-core platform clients?

The **canonical publisher** at `genlab-core/src/genlab_core/publishing/publish_all_platforms.py` exclusively uses genlab-core's `get_client()` registry. It imports no BB code at all.

---

## 3. Overlap Analysis

| Capability | BB Client | genlab-core Client | Gap |
|-----------|-----------|-------------------|-----|
| Twitter: post tweet | TwitterClient.post_tweet() | XTwitterClient._post_single_tweet() | Equivalent |
| Twitter: post thread | TwitterClient.post_thread() | XTwitterClient._publish_thread() | Equivalent |
| Twitter: media upload | TwitterClient.upload_media() | XTwitterClient._upload_media_paths() | Equivalent |
| Twitter: rate limit | threading.Lock + cooldown | bool flag + cooldown | BB has threading.Lock; genlab-core comment says "thread-unsafe by design" |
| Twitter: health check | TwitterClient.health_check() | XTwitterClient.check_token_health() | genlab-core returns typed TokenStatus |
| IG: publish reel | InstagramPublisher.publish_reel() | InstagramClient._publish_reel() | BB: 600s timeout, adaptive interval. GC: 120s, fixed interval |
| IG: container steps | 3 separate public methods | 3 private methods | BB exposes steps publicly; GC wraps in `publish()` |
| IG: reply | N/A | InstagramClient.post_reply() | BB has no reply support |
| IG: health check | N/A | InstagramClient.check_token_health() | BB has no health check |
| YT: upload short | YouTubeClient.upload_short() | YouTubeClient._upload_video() + publish() | Equivalent core logic |
| YT: upload regular | YouTubeClient.upload_video() | N/A | **Gap: genlab-core lacks regular video upload** |
| YT: community post | YouTubeClient.create_community_post() | N/A | **Gap: genlab-core lacks community posts** |
| YT: post comment | YouTubeClient.post_comment() | YouTubeClient.post_reply() | Different API endpoint (commentThreads vs comments) |
| YT: update metadata | YouTubeClient.update_video_metadata() | N/A | **Gap: genlab-core lacks metadata update** |
| YT: verify channel | YouTubeClient.verify_channel() | N/A | **Gap: genlab-core lacks cross-channel guard** |
| YT: quota gate | YouTubeQuotaTracker integration | N/A | **Gap: genlab-core lacks quota tracking** |
| YT: chunk retry | 3 attempts, exponential backoff | Single attempt per chunk | **Gap: genlab-core lacks per-chunk retry** |
| YT: health check | YouTubeClient.health_check() | YouTubeClient.check_token_health() | Equivalent |

---

## 4. What Can Be Moved As-Is

**Nothing can be moved as-is.** The BB clients use a different interface pattern (raw return types, BB-specific `execution.utils.niche_credentials` imports, own `PublishResult` dataclass) than the genlab-core protocol-based architecture (typed `PublishPayload` -> `PublishResult`, protocol conformance).

However, the genlab-core clients already cover 80-90% of the functionality. The migration is not "move BB clients to genlab-core" but rather "**fill genlab-core gaps, then switch BB to use genlab-core clients.**"

---

## 5. What Needs Refactoring (Gaps to Fill in genlab-core)

### Priority 1 — Required for BB migration

| Gap | Effort | Where |
|-----|--------|-------|
| YouTube `verify_channel()` — cross-channel guard | Small (20 LOC) | `genlab_core/platforms/youtube.py` |
| YouTube per-chunk retry (3 attempts, exp backoff) | Small (15 LOC) | `genlab_core/platforms/youtube.py._upload_video()` |
| YouTube `post_comment(video_id, text)` — top-level comment (different from reply) | Small (20 LOC) | `genlab_core/platforms/youtube.py` |
| IG poll timeout configurable (BB uses 600s vs GC 120s) | Trivial (constructor param) | `genlab_core/platforms/instagram.py` |

### Priority 2 — Nice to have, used by BB but not critical path

| Gap | Effort | Where |
|-----|--------|-------|
| YouTube `upload_video()` (regular, non-Short) | Medium (factor out `_upload_video` into Short vs Regular modes) | `genlab_core/platforms/youtube.py` |
| YouTube `create_community_post()` | Medium (backstage API is unofficial, may be dead code) | `genlab_core/platforms/youtube.py` |
| YouTube `update_video_metadata()` | Small (20 LOC) | `genlab_core/platforms/youtube.py` |
| YouTube quota gate integration | Small (conditional import, same pattern as BB) | `genlab_core/platforms/youtube.py` |

### Priority 3 — Structural cleanup after migration

| Task | Effort |
|------|--------|
| Update `dashboard/tests/test_p3_polish.py` to use genlab-core `YouTubeClient` | Small |
| Deprecate/remove `BB/execution/utils/twitter_client.py` | Trivial (after all callers switched) |
| Deprecate/remove `BB/execution/utils/instagram_client.py` | Trivial |
| Deprecate/remove `BB/execution/utils/youtube_client.py` | Trivial |
| Remove `BB/execution/utils/niche_credentials.py` shim | Trivial (already a shim pointing to genlab-core) |
| Consider merging engagement reply clients into platform clients | Medium (protocol change) |

---

## 6. What's Niche-Specific vs Truly Shared

### Truly Shared (already in genlab-core correctly)
- `niche_credentials.py` — credential resolution with cross-channel guard
- OAuth token refresh mechanics (YouTube, Threads)
- Rate-limit cooldown patterns (Twitter)
- Container lifecycle (Instagram, Threads)
- Protocol interfaces (`Publisher`, `Engageable`, `Trackable`, `HealthCheckable`)
- `PublishPayload` / `PublishResult` / `PlatformMetrics` / `TokenStatus` models

### BB-Specific (should remain in BB or be generalized carefully)
- `InstagramPublisher.publish_reel()` convenience method with BB-specific defaults — the genlab-core `InstagramClient.publish()` already provides the equivalent via `PublishPayload`
- `YouTubeClient.create_community_post()` — uses undocumented backstage API; may only work with BB's session cookies. Candidate for deprecation, not migration.
- `YouTubeQuotaTracker` — already in `genlab_core.monitoring.youtube_quota`, just not wired into the genlab-core YouTube client

### Not Niche-Specific But Architecturally Different
- BB's `publish_all_platforms.py` (1800+ LOC, monolithic, inline platform dispatch) vs genlab-core's `publish_all_platforms.py` (~300 LOC, delegates to registry + gatekeeper + dispatcher). The BB version is the legacy one still used by the daily_intel pipeline.

---

## 7. Dependency Chain (What Breaks If We Move)

### If we add gaps to genlab-core clients (safe):
- Nothing breaks. This is additive work on genlab-core.

### If we switch BB callers to genlab-core clients:

| BB File | Change Required | Risk |
|---------|----------------|------|
| `publish_all_platforms.py` (BB) | Replace `TwitterClient(niche_id=X)` with `get_client("x_twitter", **creds)` | **Medium** — BB's publish flow relies on `tw_client._rate_limited` bool for cross-thread rate-limit propagation. genlab-core's `XTwitterClient` has the same field but caller code needs adjustment. |
| `publish_twitter.py` | Replace `TwitterClient` instantiation with genlab-core client | **Low** — only used as fallback when `tw_client` not passed |
| `publish_to_instagram.py` | Replace `InstagramPublisher` with genlab-core `InstagramClient` | **Medium** — return type changes from `PublishResult` (dataclass with `.to_dict()`) to genlab-core `PublishResult` (different fields) |
| `publish_youtube.py` | Replace `YouTubeClient` with genlab-core client | **High** — uses `upload_short()`, `create_community_post()`, `post_comment()`, `verify_channel()` — several of which don't exist in genlab-core yet |
| `publish_single.py` | Replace `TwitterClient` | **Low** |
| `fetch_audience_metrics.py` | Uses `YouTubeClient._get_access_token()` directly | **Medium** — genlab-core `YouTubeClient._get_access_token()` exists but is private |
| `fetch_youtube_demographics.py` | Same as above | **Medium** |
| `tests/test_twitter_client.py` | Point to genlab-core `XTwitterClient` | **Low** — mock targets change |
| `tests/test_publish_twitter.py` | Update mock paths | **Low** |
| `dashboard/tests/test_p3_polish.py` | Remove BB sys.path hack, use genlab-core | **Low** |

---

## 8. Recommended Migration Order

### Phase 1: Fill genlab-core gaps (no BB changes)
1. Add `verify_channel()` to `genlab_core.platforms.youtube.YouTubeClient`
2. Add per-chunk retry to `_upload_video()`
3. Add `post_comment(video_id, text)` method
4. Make IG poll timeout configurable (constructor param with default 120)
5. Add YouTube `update_video_metadata()` method
6. Wire `YouTubeQuotaTracker` into genlab-core YouTube client (conditional import)

**Estimated effort:** 1-2 hours (all small/trivial additions)

### Phase 2: Switch BB Instagram (lowest risk)
1. Replace `InstagramPublisher` usage in `publish_to_instagram.py` with `genlab_core.platforms.instagram.InstagramClient`
2. Update `publish_all_platforms.py` Instagram path (already uses genlab-core in the `_dispatch_niche_aware` flow)
3. Deprecate `BB/execution/utils/instagram_client.py`

**Estimated effort:** 1 hour (return type adaptation, test updates)

### Phase 3: Switch BB Twitter (medium risk)
1. Replace `TwitterClient` in `publish_all_platforms.py` lines 1520/1853 with `get_client("x_twitter", **creds)`
2. Adapt rate-limit propagation (`tw_rate_limited` event) to use genlab-core client's `_rate_limited` flag
3. Update `publish_twitter.py` fallback instantiation
4. Update `publish_single.py`
5. Migrate `tests/test_twitter_client.py` to genlab-core
6. Deprecate `BB/execution/utils/twitter_client.py`

**Estimated effort:** 2 hours (rate-limit propagation is the tricky bit)

### Phase 4: Switch BB YouTube (highest risk, most gaps)
1. Requires Phase 1 completion (gaps filled)
2. Replace `YouTubeClient` in `publish_youtube.py` with genlab-core client
3. Update `fetch_audience_metrics.py` and `fetch_youtube_demographics.py` to use genlab-core's analytics capabilities or expose `_get_access_token()` via a public method
4. Decide on `create_community_post()` — deprecate or add to genlab-core (recommendation: deprecate, it uses an undocumented API)
5. Migrate `dashboard/tests/test_p3_polish.py` to genlab-core (remove BB sys.path hack)
6. Deprecate `BB/execution/utils/youtube_client.py`

**Estimated effort:** 3-4 hours (most complex, most callers)

### Phase 5: Cleanup
1. Remove deprecated BB client files
2. Remove `BB/execution/utils/niche_credentials.py` shim (callers import from genlab-core directly)
3. Assess whether engagement reply clients can be consolidated into platform clients (protocol alignment)

**Estimated effort:** 30 minutes

---

## 9. Total Estimated Effort

| Phase | Effort | Risk |
|-------|--------|------|
| Phase 1: Fill gaps | 1-2 hours | None (additive) |
| Phase 2: IG migration | 1 hour | Low |
| Phase 3: Twitter migration | 2 hours | Medium |
| Phase 4: YouTube migration | 3-4 hours | Medium-High |
| Phase 5: Cleanup | 30 min | None |
| **Total** | **~8-10 hours** | |

---

## 10. Key Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| BB's `publish_all_platforms.py` is 1800+ LOC monolith | Do NOT refactor it as part of this migration. Only swap client instantiation lines. |
| Twitter rate-limit propagation relies on `tw_client._rate_limited` field | genlab-core's `XTwitterClient` has identical field. Validate with test. |
| IG poll timeout difference (600 vs 120) could cause timeouts on slow videos | Make configurable in Phase 1. BB callers pass 600. |
| YouTube backstage community post API may be dead | Deprecate rather than migrate. If needed, add later. |
| Dashboard test depends on BB YouTube client via sys.path | Phase 4 removes this dependency. Low risk. |
| BB publish_to_instagram.py consumes own PublishResult.to_dict() | Adapt caller to use genlab-core PublishResult fields (success, post_id, error). |

---

## 11. Recommendation

**Proceed with migration.** The genlab-core platform package is mature, well-tested, and already covers the canonical publish path (`genlab-core/publishing/publish_all_platforms.py`). The BB legacy clients are an architecture debt item that creates confusion about which client is authoritative. The migration is straightforward (mostly import swaps) once the 6 small gaps in Phase 1 are filled.

Priority order: Instagram first (cleanest), Twitter second, YouTube last (most gaps). The entire migration can be done in a single sprint.
