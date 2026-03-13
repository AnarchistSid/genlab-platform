# Platform Consolidation & Infrastructure Refactor

**Date:** 2026-03-13
**Status:** Draft (v2 — addresses 22 review issues)
**Scope:** 5 refactors — platform client consolidation, gatekeeper extraction, response wrapper, scheduler replacement, team/permission skeleton

---

## Problem Statement

GenLab's publishing infrastructure is fragmented across 12+ files with no shared interface. The same platform appears in 2-3 locations (publishing, engagement, analytics) with duplicated auth patterns. The 2006-line `publish_all_platforms.py` mixes validation, orchestration, and execution. CriticalRush has a separate 65K-byte publisher using Postiz. The dashboard has inconsistent API responses, macOS-only scheduling via launchd, and no path to multi-operator access.

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Sequencing | Strangler fig | Build new alongside old, swap references incrementally. No big bang. |
| Protocol design | Layered protocols | Base `Publisher` + optional `Engageable`, `Trackable`, `HealthCheckable`. Pythonic, scales to partial-capability platforms. |
| Package location | `genlab-core/platforms/` | Single shared library. All niches and dashboard import from one place. Merges existing `genlab_core/platform/` (singular) into `genlab_core/platforms/` (plural). See Section 1.7. |
| Scheduler | Replace launchd with APScheduler | Portable, single control plane, persistent job store, API-visible. |
| Team model | Schema + middleware skeleton | Foundation for multi-operator. Defaults to `single_admin` passthrough — zero behavior change until activated. |

---

## Section 1: Layered Protocols & Platform Clients

### 1.1 Package Naming — Resolving `platform/` vs `platforms/`

**Existing:** `genlab_core/platform/` (singular) contains:
- `postiz_client.py` — PostizClient, PublishResult, ShadowPublisher
- `platform_rules.py` — platform constraints (video duration, caption length)
- `engagement_engine.py`, `engagement_poller.py`, `_engagement_worker.py`

**Decision:** Merge `platform/` INTO `platforms/` (plural). The new package subsumes the old:
- `postiz_client.py` → `platforms/postiz.py` (renamed, re-exported via `__init__.py` for backward compat). Both `PublishResult` and `MultiPublishResult` are re-exported.
- `platform_rules.py` → `platforms/rules.py`
- Engagement modules → `platforms/engagement/` sub-package (preserves separation)
- Old `platform/__init__.py` becomes a **shim** that re-exports from `platforms/` during migration. Deleted in cleanup step.

### 1.2 Protocols

File: `genlab_core/platforms/protocols.py`

Four `@runtime_checkable` Protocol classes:

- **`Publisher`** (required) — `platform_id: str`, `publish(payload: PublishPayload) -> PublishResult`
- **`Engageable`** (optional) — `post_reply(parent_id: str, text: str, *, context_id: str = "") -> bool`, `like(target_id: str, *, context_id: str = "") -> bool`
- **`Trackable`** (optional) — `get_metrics(post_id: str, published_at: datetime) -> PlatformMetrics | None`
- **`HealthCheckable`** (optional) — `check_token_health() -> TokenStatus`

**Engageable signature rationale (Issue #6):** The existing platform clients have heterogeneous reply signatures:
- YouTube: `post_youtube_reply(video_id, parent_id, text)` — needs video_id + parent comment_id
- Instagram: `post_instagram_reply(media_id, comment_id, text)` — needs media_id + comment_id
- X/Twitter: `post_x_reply(tweet_id, text)` — needs only tweet_id
- Facebook: `post_facebook_reply(comment_id, text)` — needs only comment_id
- Threads: `post_threads_reply(thread_id, text)` — needs only thread_id

**Unified signature:** `post_reply(parent_id: str, text: str, *, context_id: str = "") -> bool`
- `parent_id` = the thing being replied to (comment_id for IG/FB/Threads, tweet_id for X, parent comment_id for YouTube)
- `context_id` = optional parent container (video_id for YouTube, media_id for Instagram). Empty string when not needed (X, FB, Threads).

Every platform client implements `Publisher`. Additional protocols are opt-in per platform:

| Platform | Publisher | Engageable | Trackable | HealthCheckable |
|----------|-----------|------------|-----------|-----------------|
| Instagram | Yes | Yes (reply, like) | Yes (via Meta Insights) | Yes (EAA permanent) |
| YouTube | Yes | Yes (reply, like) | Yes (Analytics API) | Yes (refresh token) |
| X/Twitter | Yes | Yes (reply, like) | Yes (tweet metrics) | Yes (bearer check) |
| Facebook | Yes | Yes (reply only) | Yes (post insights) | Yes (EAA permanent) |
| Threads | Yes | Yes (reply only) | No (API limited) | Yes (60-day token) |
| TikTok | Stub only | No | No | Stub (pending TIKTOK_AUDIT_APPROVED) |

**TikTok (Issue #7):** Included as a stub client. `publish()` returns `PublishResult(success=False, error="TikTok disabled pending audit")` unless `TIKTOK_AUDIT_APPROVED=true`. No engagement or metrics support yet.

### 1.3 Data Models

File: `genlab_core/platforms/models.py`

**PublishResult (Issue #3):** Reuse and extend the existing `PublishResult` from `genlab_core/platform/postiz_client.py`. The existing dataclass has: `platform`, `success`, `post_id`, `post_url`, `error`, `raw_response`. We add `metadata: dict` as an alias for `raw_response` via property, and keep backward compatibility.

```python
@dataclass
class PublishPayload:
    caption: str
    media_paths: list[Path]
    media_type: Literal["video", "image", "text", "link"]
    hashtags: list[str]
    hook: str
    niche_id: str
    platform_specific: PlatformSpecific | None = None  # Typed per-platform config, or None if not needed

@dataclass
class PublishResult:
    """Extended from existing postiz_client.PublishResult. Backward-compatible."""
    platform: str
    success: bool
    post_id: str = ""
    post_url: str = ""
    error: str = ""
    raw_response: dict = field(default_factory=dict)

    @property
    def metadata(self) -> dict:
        return self.raw_response

@dataclass
class PlatformMetrics:
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    extra: dict = field(default_factory=dict)  # avg_view_pct, subs_gained, etc.

@dataclass
class TokenStatus:
    valid: bool
    platform: str
    expires_at: datetime | None
    needs_refresh: bool
    message: str
    details: dict = field(default_factory=dict)  # Preserves per-platform report data
```

**PlatformSpecific (Issue #20):** Typed per-platform payload configs instead of raw dict:

```python
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
    thread_tweets: list[dict] = field(default_factory=list)  # [{"position": N, "text": "..."}]
    link_in_reply: bool = False

@dataclass
class InstagramSpecific:
    cover_url: str = ""
    share_to_feed: bool = True

@dataclass
class FacebookSpecific:
    pass  # No platform-specific config needed beyond caption + media

@dataclass
class ThreadsSpecific:
    pass

PlatformSpecific = YouTubeSpecific | TwitterSpecific | InstagramSpecific | FacebookSpecific | ThreadsSpecific
```

### 1.4 Registry

File: `genlab_core/platforms/registry.py`

- `register(platform_id)` — class decorator, adds to `_REGISTRY` dict
- `get_client(platform_id, **kwargs)` — factory, returns instance
- `list_platforms()` — returns registered platform IDs

**Import strategy (Issue #21):** Lazy registration. `platforms/__init__.py` does NOT eagerly import all platform modules. Instead:

```python
# platforms/__init__.py
_REGISTRY: dict[str, str] = {
    "instagram": "genlab_core.platforms.instagram:InstagramClient",
    "youtube": "genlab_core.platforms.youtube:YouTubeClient",
    "x_twitter": "genlab_core.platforms.x_twitter:XTwitterClient",
    "facebook": "genlab_core.platforms.facebook:FacebookClient",
    "threads": "genlab_core.platforms.threads:ThreadsClient",
    "tiktok": "genlab_core.platforms.tiktok:TikTokClient",
}

def get_client(platform_id: str, **kwargs):
    """Lazy-load platform module on first use."""
    entry = _REGISTRY.get(platform_id)
    if entry is None:
        raise ValueError(f"Unknown platform: {platform_id}")
    module_path, class_name = entry.rsplit(":", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(**kwargs)
```

This avoids importing tweepy, google-api-python-client, etc. at package load time.

### 1.5 Per-Platform Clients

One file per platform in `genlab_core/platforms/`:

- `instagram.py` — `InstagramClient`: Reel publish (container model), carousel, reply, like, Meta Insights metrics, EAA token health. Uses `graph.facebook.com` exclusively (never `graph.instagram.com`).
- `youtube.py` — `YouTubeClient`: Shorts upload (<=180s), regular video upload, comment reply, like, Analytics API v2 metrics (48h lag guard), OAuth2 refresh token health.
- `x_twitter.py` — `XTwitterClient`: Single tweet, thread, media upload (chunked for video), reply, like, tweet metrics, OAuth 1.0a via tweepy. Rate limit tracking (429 flag + 1h cooldown).
- `facebook.py` — `FacebookClient`: Video/reel, carousel (unpublished photos + feed post), single photo, link post, comment reply, post insights, EAA token health.
- `threads.py` — `ThreadsClient`: Video, image, text, carousel, reply, 60-day token health with auto-refresh at 50 days.
- `tiktok.py` — `TikTokClient`: Stub only. Returns disabled error unless `TIKTOK_AUDIT_APPROVED=true`.
- `postiz.py` — Migrated from `platform/postiz_client.py`. PostizClient, ShadowPublisher preserved as-is.

Each client:
- Reads credentials from env vars in `__init__` (with explicit params as override)
- Handles its own retry logic (exponential backoff on transient errors)
- Logs via standard `logging` module

### 1.6 Postiz Integration (Issue #8)

**PostizClient** remains as an alternative publishing path, NOT replaced by native platform clients. CriticalRush uses Postiz as its primary publisher. Content Scraper uses it in shadow mode.

The relationship:
- **Native platform clients** (`InstagramClient`, `YouTubeClient`, etc.) — direct API calls, full control
- **PostizClient** — SaaS aggregator, simpler but less control (no carousel, limited video options)

Both implement `Publisher` protocol. The orchestrator can use either:
```python
# Direct native (Content Scraper default)
client = get_client("instagram")

# Via Postiz (CriticalRush default)
client = get_client("postiz")  # PostizClient wraps all platforms
```

ShadowPublisher continues to work unchanged — it publishes via Postiz in parallel for comparison.

### 1.7 CriticalRush Publishing (Issue #5)

**Scope:** CriticalRush's `publish_gaming_content.py` (65K bytes) will NOT be rewritten in this refactor. It already uses `PostizClient` from genlab-core and has its own bespoke fallback logic.

**What changes for CR:**
- Import path: `from genlab_core.platform.postiz_client import ...` → `from genlab_core.platforms.postiz import ...` (shim handles transition)
- CR can optionally adopt native clients for fallback paths in a future sprint

### 1.8 Migration from Old Code

**Strangler fig approach:**
1. Build new clients. They wrap the same API calls as existing code.
2. Old files remain untouched initially.
3. Orchestrator switches from `from execution.publish_to_instagram import publish_reel` to `get_client("instagram").publish(payload)`.
4. Engagement engine switches from `from genlab_core.engagement.platform_clients.instagram import post_instagram_reply` to `get_client("instagram").post_reply(...)`.
5. `publish_single.py` (Issue #4) migrated alongside orchestrator — it imports from `publish_all_platforms.py`.
6. Once all callers migrated, old files are deleted.

### 1.9 What Gets Deleted After Migration

- `Content Scraper/execution/publish_to_instagram.py`
- `Content Scraper/execution/publish_youtube.py`
- `Content Scraper/execution/publish_twitter.py`
- `Content Scraper/execution/publish_facebook.py`
- `Content Scraper/execution/publish_threads.py`
- `Content Scraper/execution/publish_single.py` (Issue #4 — depends on old orchestrator)
- `Content Scraper/execution/utils/twitter_client.py`
- `Content Scraper/execution/utils/youtube_client.py`
- `genlab-core/src/genlab_core/engagement/platform_clients/youtube.py`
- `genlab-core/src/genlab_core/engagement/platform_clients/instagram.py`
- `genlab-core/src/genlab_core/engagement/platform_clients/x_twitter.py`
- `genlab-core/src/genlab_core/engagement/platform_clients/facebook.py`
- `genlab-core/src/genlab_core/engagement/platform_clients/threads.py`
- `genlab-core/src/genlab_core/platform/` (singular — replaced by shim, then deleted)

---

## Section 2: Gatekeeper Extraction

### 2.1 PublishGatekeeper

File: `genlab_core/platforms/gatekeeper.py`

Extracts the 7 publishing gates from `publish_all_platforms.py` into a composable, testable class.

**Input type (Issue #15):** The gatekeeper operates on raw blueprint dicts (SharePoint field names: `visual_paths`, `action_taken`, `priority_score`, etc.), NOT on `PublishPayload`. The payload conversion happens AFTER the gatekeeper approves. This avoids impedance mismatch — the gatekeeper checks preconditions on raw data, the dispatcher works with typed payloads.

```python
@dataclass
class GateResult:
    allowed: bool
    reason: str
    gate_name: str

class PublishGatekeeper:
    def __init__(self, config: dict, daily_cap: DailyCapEnforcer, backlog):
        ...
        self._gates = [
            self._approval_gate,
            self._strict_creator_video_gate,
            self._schedule_gate,
            self._score_floor_gate,
            self._video_only_gate,
            self._gap_guard,
            self._daily_cap_gate,
        ]

    def evaluate(self, blueprint: dict, platform: str) -> GateResult:
        """Run all gates in priority order. First rejection wins."""
        for gate in self._gates:
            result = gate(blueprint, platform)
            if not result.allowed:
                return result
        return GateResult(allowed=True, reason="passed", gate_name="all")
```

### 2.2 Gate Definitions

Each gate is a private method with signature `(blueprint: dict, platform: str) -> GateResult`:

1. **approval_gate** — `action_taken == 'approved'` (bypassed by `SKIP_APPROVAL_GATE` env var)
2. **strict_creator_video_gate** — Format must be "reel" if `POLICY.strict_creator_video_only`
3. **schedule_gate** — `scheduled_for <= now + lookahead_minutes` (default 15 min)
4. **score_floor_gate** — `priority_score >= MIN_PRIORITY_SCORE` (env var, default 0.3)
5. **video_only_gate** — Local MP4 file must exist (checks `visual_paths` JSON field from blueprint)
6. **gap_guard** — Minimum hours since last publish on this platform (from config)
7. **daily_cap_gate** — Delegates to `DailyCapEnforcer.can_publish(platform)`

### 2.3 Publish Strategy (Issue #9)

The existing `publish_strategy` (`best_effort` vs `all_or_nothing`) is handled by the orchestrator, NOT the gatekeeper or dispatcher. Logic:

```python
# In orchestrator (publish_all_platforms.py)
results = dispatcher.dispatch_many(tasks)

if config["publish_strategy"] == "all_or_nothing":
    if any(not r.success for r in results.values()):
        # Mark ALL as failed, rollback successful posts if possible
        status = "FAILED"
    else:
        status = "PUBLISHED"
elif config["publish_strategy"] == "best_effort":
    # Mark PUBLISHED if at least 1 success
    status = "PUBLISHED" if any(r.success for r in results.values()) else "FAILED"
```

### 2.4 Dispatch Function (Issue #19)

**Simplified:** `dispatch_many` is a function, not a class. The `concurrent` flag is unnecessary indirection.

```python
# genlab_core/platforms/dispatcher.py
def dispatch_many(
    tasks: list[tuple[str, PublishPayload]],
    max_workers: int = 5,
) -> dict[str, PublishResult]:
    """Dispatch to multiple platforms concurrently. Never raises — returns error results."""
    results: dict[str, PublishResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            p: pool.submit(_safe_dispatch, p, payload)
            for p, payload in tasks
        }
        for platform, future in futures.items():
            results[platform] = future.result()  # _safe_dispatch never raises
    return results

def _safe_dispatch(platform: str, payload: PublishPayload) -> PublishResult:
    """Issue #12: Catch exceptions so one platform failure doesn't kill others."""
    try:
        client = get_client(platform)
        return client.publish(payload)
    except Exception as exc:
        return PublishResult(platform=platform, success=False, error=str(exc))
```

### 2.5 Simplified Orchestrator

`publish_all_platforms.py` shrinks from ~2006 lines to ~600 lines:
- `_main_locked()` fetches due blueprints (file lock preserved)
- **Finalization pre-steps preserved (Issue #13):** Before publish tick, the orchestrator still runs the pipeline finalization steps (review, adapt, render, validate) that were previously triggered by the launchd plist. These are NOT part of the gatekeeper — they're upstream pipeline stages.
- For each blueprint: iterate enabled platforms → `gatekeeper.evaluate(bp, platform)` → if allowed → `build_payload(bp, platform)` produces a **per-platform** `PublishPayload` with the correct `PlatformSpecific` subtype (e.g., `YouTubeSpecific` for YouTube, `TwitterSpecific` for X). Collect approved `(platform, payload)` tuples → `dispatch_many(tasks)`.
- Publish strategy applied after dispatch results
- Post-publish: update blueprint status, create PendingFeedbackTask, update Publishing_Analytics, log results
- Postiz shadow publish (if `POSTIZ_SHADOW_MODE=true`) fires in a background thread after native publish

---

## Section 3: Dashboard Response Wrapper

### 3.1 Response Helpers

File: `dashboard/server/core/responses.py`

```python
def api_success(data=None, message="OK", code=200):
    return jsonify({"status": "success", "code": code, "data": data, "message": message}), code

def api_error(error=None, message="Request failed", code=400):
    return jsonify({"status": "error", "code": code, "error": str(error) if error else None,
                    "message": message}), code

def api_not_found(message="Resource not found"):
    return api_error(message=message, code=404)
```

### 3.2 Global Error Handler

Registered on Flask app:
- `HTTPException` → `api_error(code=e.code)`
- Unhandled `Exception` → `api_error(code=500)` + log traceback

### 3.3 Migration (Issue #10)

**Backend:** All 17 files with `jsonify` across `dashboard/server/` (including `review_server.py` which has 37 calls, plus all files in `dashboard/server/api/`) switch to `api_success(data)` / `api_error(e)`. 217 total `jsonify` calls. Current response shapes vary:
- Some return `{"data": ...}` — data field preserved, wrapped in new envelope
- Some return `{"error": "..."}` — error field preserved in new envelope
- Some return raw arrays — wrapped in `api_success(data=array)`

**Frontend:** `dashboard/frontend/src/api/client.ts` (helpers `get<T>` and `mutate<T>`) currently reads `resp.json()` directly. Migration:
- Add a response interceptor that unwraps `body.data` on success, throws on `body.status === "error"`
- This is a single change in `client.ts`, not per-endpoint
- Existing `body.error` checks continue to work (field name preserved in error responses)

**Migration is incremental:** endpoints can be switched one at a time. The interceptor handles both old (raw) and new (wrapped) formats during transition.

---

## Section 4: In-Process Scheduler

### 4.1 Technology

APScheduler `BackgroundScheduler` with `SQLAlchemyJobStore` (SQLite at `~/.genlab/scheduler.db`).

**New dependencies (Issue #11):** Add to `dashboard/pyproject.toml`:
- `apscheduler>=3.10,<4` (3.x is stable; 4.x is a rewrite with different API)
- `sqlalchemy>=2.0` (required by `SQLAlchemyJobStore`)

**Process architecture:** The scheduler runs as a **separate process** from the Flask dashboard (not inside the eventlet-patched WSGI server). The dashboard starts it on boot via `subprocess.Popen` and communicates via the shared SQLite job store. The dashboard API endpoints (`/api/v1/scheduler/*`) read job state from SQLite and send control signals via APScheduler's `modify_job` / `pause_job` APIs (SQLite-backed, no IPC needed).

- Persistent: jobs survive process restarts
- Coalescing: missed jobs fire once on recovery (not N times)
- Max instances: 1 per job (prevents overlapping runs)

### 4.2 Schedule Status — Extending Existing State Machine (Issue #16)

**Existing** workflow states in `workflow_state_machine.py`: INTAKE → VALIDATED → INTEL_READY → RESEARCHED → DRAFTED → VISUAL_READY → SCHEDULED → PUBLISHED → ANALYZED → ARCHIVED.

**Decision:** Do NOT create a parallel schedule status enum. Instead, extend the existing state machine with two new states:

```
VISUAL_READY → SCHEDULED → PUBLISHING → PUBLISHED
                                      → PUBLISH_FAILED
```

New states:
- `PUBLISHING` (replaces proposed `RUNNING`) — blueprint is currently being dispatched to platforms
- `PUBLISH_FAILED` (replaces proposed `FAILED`) — dispatch failed, can retry

Add transitions to `ALLOWED_TRANSITIONS`:
```python
"SCHEDULED": ["PUBLISHING", "DELETED"],      # DELETED already exists in state machine
"PUBLISHING": ["PUBLISHED", "PUBLISH_FAILED"],
"PUBLISH_FAILED": ["SCHEDULED"],             # retry path
```

**Note:** Uses existing `DELETED` state instead of introducing `CANCELLED`. `DELETED` already has defined semantics and transitions in the state machine.

### 4.3 GenLabScheduler

File: `dashboard/server/core/scheduler.py`

**Job mapping to existing launchd plists (Issue #13):**

| Job ID | Trigger | Replaces Plist | Pre-steps |
|--------|---------|----------------|-----------|
| `publish_tick` | Fixed-time cron (matching current plist schedule: 11:50, 12:10, 16:50, 17:10, 19:50, 20:20 IST) | `com.genlab.instagram-publisher.plist` | File lock + finalization pipeline (review → adapt → render → validate → publish) |
| `token_health` | cron daily 15:00 UTC | `com.genlab.token-refresh.plist` | None — calls `check_token_health.py` |
| `analytics` | interval 6h | `com.genlab.metric-collector.plist` | None — calls metric_collector flow |
| `engagement_poll` | interval 15 min | `com.genlab.engagement-poller.plist` | None — polls YT comments + X mentions |
| `quota_monitor` | interval 60s | `com.genlab.quota-monitor.plist` | None — runs quota check loop |
| `daily_intel` | cron daily 8:00 IST | `com.genlab.daily-intel.plist` | None — triggers intel pipeline |

**Per-niche publishers** (SpliceReel, FrameDrift, ClutchWire, CriticalRush) each get their own job, matching their existing plist schedules.

**Note:** `publish_tick` fires at the SAME fixed times as the current plist (not every 5 minutes). This preserves the existing publishing cadence. The scheduler just replaces the timer mechanism, not the timing.

### 4.4 Dashboard API

```
GET  /api/v1/scheduler/status           → job list with next_run_time, state
POST /api/v1/scheduler/jobs/{id}/pause   → pause job
POST /api/v1/scheduler/jobs/{id}/resume  → resume job
POST /api/v1/scheduler/trigger/{id}      → force-run now
```

### 4.5 Migration Path

1. Build scheduler, start it alongside existing launchd plists — plists still fire, scheduler logs only (dry run mode)
2. Compare for 1 week: did both identify the same due blueprints at the same times?
3. Switch scheduler to live mode, disable plists (`launchctl unload`)
4. Move plists to `runbooks/deprecated/` (not deleted — rollback fallback)
5. Update CLAUDE.md memory references

---

## Section 5: Team/Permission Skeleton

### 5.1 Data Models

File: `genlab_core/auth/models.py`

```python
class Permission(IntEnum):
    VIEWER = 0       # Read-only dashboard
    EDITOR = 1       # Can draft, can't publish
    PUBLISHER = 2    # Can publish (approval if configured)
    ADMIN = 3        # Can approve, manage members

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

**Storage (Issue #17):** In `single_admin` mode, no storage backend needed — the models exist as code only. When `multi_team` is activated, storage will be SQLite (same DB as scheduler, `~/.genlab/genlab.db`). The storage layer is NOT implemented in this phase — only the models and middleware skeleton.

### 5.2 Auth Middleware

File: `dashboard/server/middleware/auth.py`

- `AuthMiddleware(mode)` — `"single_admin"` (default) or `"multi_team"`
- `require_permission(min_permission)` — decorator for Flask routes
- In `single_admin` mode: passthrough (current behavior preserved exactly)
- In `multi_team` mode: extracts user from JWT, checks team membership + permission level

### 5.3 Activation

Controlled by `AUTH_MODE` env var:
- `AUTH_MODE=single_admin` (default) — no behavior change, no storage, no JWT parsing
- `AUTH_MODE=multi_team` — activates JWT auth, team checks, niche access control

No UI for team management in this phase. Teams/members managed via API or direct DB operations.

### 5.4 Scope Justification (Issue #18)

This section is minimal:
- 3 dataclasses (30 lines) + 1 enum (4 lines)
- 1 middleware class (~40 lines) that defaults to passthrough
- 1 decorator applied to ~5 sensitive endpoints (approve, publish, scheduler control)
- Zero test burden in `single_admin` mode (passthrough = nothing to test)

The value is avoiding a future refactor to retrofit permissions onto endpoints that were never designed for them. The cost is ~70 lines of code that do nothing until activated.

---

## Implementation Order (Strangler Fig)

```
Step 1: protocols.py + models.py + registry.py       (zero risk — new files only)
Step 2: 6 platform clients + postiz migration         (additive, old code untouched)
Step 3: gatekeeper.py + dispatcher.py                 (new files, old code untouched)
Step 4: responses.py + global error handler + client.ts interceptor (dashboard, low risk)
Step 5: auth/models.py + middleware skeleton           (new files + decorator on routes)
Step 6: scheduler.py + dashboard API + new deps        (parallel with launchd, dry-run first)
Step 7: Wire orchestrator to new clients + gatekeeper  (the swap — old imports replaced)
       Wire publish_single.py or delete it
Step 8: Wire engagement engine to new clients          (swap engagement imports)
Step 9: Wire token_health.py to HealthCheckable clients (Issue #22 — map per-platform reports to TokenStatus)
Step 10: platform/ shim → full deletion                (cleanup old singular package)
Step 11: Delete old publisher files                    (cleanup)
Step 12: Disable launchd plists (after parallel validation)
```

**Rollback plan (Issue #14):**
- Steps 1-6: Pure additive. Rollback = delete new files.
- Steps 7-9: Keep old files alongside new imports for 1 sprint. Feature flag `USE_NATIVE_CLIENTS=true` (default) vs `false` (falls back to old imports). Rollback = flip flag.
- Steps 10-12: Irreversible but gated behind successful parallel validation.

Each step is independently deployable. Steps 1-6 are purely additive. Steps 7-9 use feature flags. Steps 10-12 are cleanup after validation.

---

## Testing Strategy

- **Unit tests** for each platform client (mock HTTP, verify request shapes)
- **Unit tests** for each gate in PublishGatekeeper (parameterized: allowed/blocked per gate)
- **Unit tests** for `_safe_dispatch` error handling (Issue #12)
- **Unit tests** for `post_reply` unified signature across all platforms
- **Integration tests** for `dispatch_many` (mock clients, verify concurrent dispatch + partial failure)
- **Integration tests** for GenLabScheduler (verify job triggers, coalescing, recovery)
- **Migration tests** for response wrapper (verify both old and new frontend contracts work during transition)
- **Permission tests** for auth middleware (both modes, all permission levels)

Tests live in `genlab-core/tests/platforms/` and `dashboard/tests/`.

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Platform client behavior drift from old code | Port logic line-by-line; feature flag for rollback |
| `platform/` → `platforms/` import breakage | Shim re-exports from old path during transition |
| PublishResult field mismatch | Extend existing dataclass, don't create new one |
| APScheduler reliability vs launchd | Dry-run mode first, 1-week parallel, plists preserved |
| Eventlet + APScheduler threading conflict | Dashboard uses eventlet which monkey-patches threading. APScheduler's BackgroundScheduler runs in a **separate process** (launched by dashboard on startup via `subprocess.Popen`), NOT inside the eventlet-patched Flask process. Communicates via the shared SQLite job store. |
| Auth middleware breaks dashboard | Default `single_admin` = passthrough, zero behavior change |
| Response wrapper breaks frontend | client.ts interceptor handles both old and new formats |
| Scheduler fires at wrong times | Match existing plist cron schedules exactly, not generic intervals |
| Large scope — 5 refactors + 12 steps | Strangler fig + feature flags = each step independently revertible |
