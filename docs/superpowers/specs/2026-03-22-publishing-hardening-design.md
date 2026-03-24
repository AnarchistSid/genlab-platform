# Publishing Hardening — Design Spec

**Date:** 2026-03-22
**Scope:** 8 improvements to publishing reliability, observability, and performance
**Prerequisite:** Sprint 67 publishing fixes (148 issues found, 33 fixed)

---

## 1. Partial Failure Retry with Error Classification

**What:** When a blueprint publishes to some platforms but fails on others, automatically retry failed platforms with exponential backoff. Classify errors to distinguish transient (retry) from permanent (don't retry).

**platform_publish_status format upgrade:**
```json
{
  "instagram": "PUBLISHED",
  "youtube": {
    "status": "FAILED",
    "attempts": 2,
    "last_error": "YouTube quota near limit",
    "error_class": "QUOTA",
    "next_retry_after": "2026-03-22T10:00:00Z"
  },
  "facebook": "PUBLISHED"
}
```

**Error classification:**

| Class | Retry? | Backoff | Examples |
|-------|--------|---------|----------|
| TRANSIENT | Yes | 1h, 4h, 12h | Timeout, 500, connection error, CDN unavailable |
| QUOTA | Yes (next day) | 24h | YouTube quota, Instagram rate limit |
| CREDENTIAL | No | — | Token expired, 401, invalid_grant |
| CONTENT | No | — | Video too large, caption too long, format rejected |
| PERMANENT | No | — | Account suspended, API deprecated |

**New file:** `genlab-core/src/genlab_core/publishing/error_classifier.py`

```python
def classify_error(error_message: str, platform: str) -> str:
    """Classify a publish error. Returns: TRANSIENT, QUOTA, CREDENTIAL, CONTENT, PERMANENT."""
```

**Publisher changes:**
- After querying VISUAL_READY, also query PUBLISHED blueprints
- For each PUBLISHED blueprint, parse `platform_publish_status`
- For platforms with `status=FAILED` and `error_class in (TRANSIENT, QUOTA)`:
  - Check `attempts < 3` and `next_retry_after < now()`
  - Attempt publish for just those platforms
  - Update attempts count and next_retry_after
- Max 3 retries per platform, then mark as PERMANENT

---

## 2. Dashboard Publishing Alerts

**What:** API endpoint returning categorized alerts. Frontend shows persistent alert drawer.

**Endpoint:** `GET /api/v1/alerts/publishing`

**Response:**
```json
{
  "critical": [
    {"type": "publish_failed", "count": 2, "niches": ["movies", "anime"]},
    {"type": "stuck_publishing", "count": 1, "blueprint_id": "abc123"}
  ],
  "warning": [
    {"type": "high_failure_rate", "rate": 35, "period": "24h"},
    {"type": "token_expiring", "platform": "threads", "niche": "gaming", "days_left": 5},
    {"type": "partial_publish", "count": 3}
  ],
  "info": [
    {"type": "youtube_quota", "used": 8000, "limit": 10000},
    {"type": "pending_retry", "count": 4}
  ],
  "total_unresolved": 6
}

```

**System-wide alerts endpoint:** `GET /api/v1/alerts/system`

```json
{
  "pipeline": {"missed_runs_today": ["gaming"], "last_run": {}},
  "disk": {"usage_pct": 45, "tmp_size_gb": 2.1},
  "database": {"pool_available": 8, "pool_max": 10},
  "downloads": {"yt_dlp_failures_24h": 3},
  "stale_blueprints": {"visual_ready_gt_7d": 5}
}
```

**Frontend:** Alert drawer component in Mission Control header bar. Red badge with count. Click to expand categorized list with suggested actions.

---

## 3. N+1 Query Fix + Indexes

**What:** Replace client-side filtering with server-side queries. Add supporting indexes.

**Video dedup fix:**
```python
# Before (N+1):
existing = [bp for bp in client.blueprints.all(max_records=200) if bp.video_id == video_id]

# After:
existing = client.blueprints.all(
    formula=f"{{video_id}}='{video_id}' AND {{niche_id}}='{niche_id}'",
    max_records=1,
)
```

**Hook dedup fix:**
```python
# Before: loads 50 blueprints, filters in Python
# After: query with formula
existing = client.blueprints.all(
    formula=f"{{hook}}='{escaped_hook}' AND {{niche_id}}='{niche_id}'",
    max_records=1,
)
```

**New indexes:**
```sql
CREATE INDEX IF NOT EXISTS idx_bp_video_niche ON blueprints(video_id, niche_id) WHERE video_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bp_hook_niche ON blueprints(hook, niche_id) WHERE hook IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bp_status_niche ON blueprints(status, niche_id);
```

---

## 4. CDN Reliability Upgrade

**What:** Circuit breakers, file size checks, runtime path resolution, URL validation.

**Circuit breaker:**
```python
class CDNCircuitBreaker:
    """Per-provider circuit breaker. Opens after 3 consecutive failures, resets after 1h."""
    def __init__(self, provider: str, failure_threshold: int = 3, reset_seconds: int = 3600): ...
    def can_attempt(self) -> bool: ...
    def record_success(self): ...
    def record_failure(self): ...
```

**File size pre-check:**
- Cloudflare tunnel: no limit (local file copy)
- Litterbox: 200MB max
- tmpfiles: 100MB max
- Skip providers that can't handle the file, log clearly

**Runtime path resolution:**
```python
# Before (import time):
_MEDIA_SHARE_DIR = Path(os.environ.get("GENLAB_PROJECT_ROOT", "")) / ".media" / "cdn"

# After (function call):
def _get_media_share_dir() -> Path:
    return Path(os.environ.get("GENLAB_PROJECT_ROOT", Path.cwd())) / ".media" / "cdn"
```

**URL validation after upload:**
- GET request (not HEAD) to verify content serves
- Compare response Content-Length with local file size
- Reject URLs that don't match (CDN upload failed silently)

---

## 5. Threads Parity with Instagram

**What:** Polling loop, permalink fetch, rate limit tracking.

**Polling loop:**
```python
def _poll_container_status(self, container_id: str, max_seconds: int = 120) -> str:
    """Poll Threads container status until FINISHED or timeout."""
    for _ in range(max_seconds // 5):
        resp = self._get(f"/{container_id}", params={"fields": "status"})
        status = resp.get("status")
        if status == "FINISHED":
            return "FINISHED"
        if status == "ERROR":
            return "ERROR"
        time.sleep(5)
    return "TIMEOUT"
```

**Permalink fetch after publish:**
```python
# After threads_publish, fetch permalink
permalink_resp = self._get(f"/{post_id}", params={"fields": "permalink"})
post_url = permalink_resp.get("permalink", f"https://www.threads.net/@/post/{post_id}")
```

---

## 6. Token Lifecycle Management

**What:** Expiry tracking, auto-refresh for Threads, clear alerts for credential issues.

**Threads auto-refresh:**
```python
def _refresh_token_if_needed(self) -> bool:
    """Refresh Threads long-lived token if expiring within 7 days."""
    if not self._token_needs_refresh():
        return True
    resp = requests.get(
        f"https://graph.threads.net/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": self._access_token},
    )
    if resp.ok:
        new_token = resp.json().get("access_token")
        # Write back to .env (or notify user to update)
        ...
```

**Token health endpoint enhancement:**
```json
{
  "platforms": {
    "instagram": {"status": "healthy", "expires": "never (EAA)", "niche": "gaming"},
    "youtube": {"status": "healthy", "expires": "2026-04-15", "days_left": 24},
    "threads": {"status": "warning", "expires": "2026-03-28", "days_left": 6, "action": "auto-refresh scheduled"}
  }
}
```

---

## 7. Error Classification Module

**What:** Centralized error classification for all publish failures.

**File:** `genlab-core/src/genlab_core/publishing/error_classifier.py`

```python
ERROR_PATTERNS = {
    "TRANSIENT": [
        r"timed? ?out",
        r"connection.*(?:reset|refused|error)",
        r"50[0-9]",
        r"temporary",
        r"try again",
        r"CDN.*(?:unavailable|failed)",
    ],
    "QUOTA": [
        r"quota",
        r"rate.?limit",
        r"429",
        r"too many requests",
    ],
    "CREDENTIAL": [
        r"401",
        r"unauthorized",
        r"token.*(?:invalid|expired|revoked)",
        r"invalid.?grant",
        r"OAuthException",
        r"No.*credentials",
    ],
    "CONTENT": [
        r"(?:caption|title|description).*too.*(?:long|short)",
        r"(?:video|image|media).*(?:too.*(?:large|small)|not.*(?:found|supported))",
        r"format.*(?:not.*supported|invalid)",
        r"2207077",
    ],
}

def classify(error_message: str, platform: str = "") -> str:
    """Classify error. Returns TRANSIENT, QUOTA, CREDENTIAL, CONTENT, or PERMANENT."""
```

---

## 8. Publishing Metrics in Dashboard

**What:** New section in dashboard showing publishing health metrics.

**Backend:** `GET /api/v1/metrics/publishing`

```json
{
  "success_rate_7d": {"instagram": 81, "youtube": 62, "facebook": 84},
  "trend_7d": [
    {"date": "2026-03-16", "ok": 12, "fail": 3},
    {"date": "2026-03-17", "ok": 72, "fail": 20}
  ],
  "avg_latency_seconds": {"instagram": 45, "youtube": 120, "facebook": 30},
  "retry_success_rate": 65,
  "error_distribution": {"TRANSIENT": 40, "QUOTA": 25, "CREDENTIAL": 20, "CONTENT": 15},
  "platform_status": {"instagram": "green", "youtube": "yellow", "facebook": "green"}
}
```

**Frontend:** Card in Mission Control with:
- Success rate sparklines per platform (7-day)
- Platform status indicators (green/yellow/red circles)
- Error distribution mini chart
- "View details" link to full Publishing Analytics view

---

## File Changes Summary

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/publishing/error_classifier.py` | New: error classification |
| `genlab-core/src/genlab_core/publishing/publish_all_platforms.py` | Partial failure retry logic |
| `genlab-core/src/genlab_core/platforms/cdn_upload.py` | Circuit breaker, size check, runtime path |
| `genlab-core/src/genlab_core/platforms/threads.py` | Polling loop, permalink, auto-refresh |
| `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py` | Server-side dedup queries |
| `dashboard/server/api/alerts.py` | New: publishing + system alerts API |
| `dashboard/server/api/metrics.py` | New: publishing metrics API |
| `dashboard/frontend/src/views/mission-control/AlertDrawer.tsx` | New: alert drawer component |
| `dashboard/frontend/src/views/mission-control/PublishingMetrics.tsx` | New: metrics card |
| `genlab-core/migrations/add_publishing_indexes.sql` | New indexes |

---

## Implementation Order

1. Error classifier (foundation — other features depend on it)
2. N+1 query fix + indexes (quick win, immediate performance improvement)
3. CDN reliability upgrade (fixes active failures)
4. Threads polling + permalink (fixes active failures)
5. Partial failure retry (biggest reliability improvement)
6. Token lifecycle management (prevents future failures)
7. Publishing alerts API (observability)
8. Publishing metrics dashboard (visibility)
