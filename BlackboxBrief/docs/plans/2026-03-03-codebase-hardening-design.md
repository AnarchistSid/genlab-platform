# Codebase Hardening — 3-Phase Upgrade Plan

**Date:** 2026-03-03
**Status:** Approved
**Scope:** 25 fixes across reliability, intelligence, and hygiene
**Estimated files:** ~30

---

## Context

After deploying the social media fix plan (2A-2G) which addressed platform-specific content packaging, a deep codebase audit revealed 42 systemic issues across 5 subsystems. This design covers the 25 highest-impact fixes in 3 phases.

### What 2A-2G Already Delivered
- Facebook landscape auto-render fallback
- Facebook content adaptation (prompt + adapt + publish)
- Twitter shareability prompts
- YouTube engagement comments + Shorts curiosity-gap titles
- Hook fallback with curiosity patterns
- Partial-publish failure visibility logging

### What Remains
- Video validator corrupts landscape files
- CDN uploads have zero retry logic
- Scoring never learns from post performance
- Dead carousel-era code across 10+ files
- Security gaps in review server and backlog client

---

## Phase 0 — Reliability (Prevent Data Loss)

7 fixes. Goal: eliminate silent production failures.

### P0.1 Landscape Validator Blind Spot

**Problem:** `validate_videos.py` enforces 1080x1920 for ALL `.mp4` files. Landscape `*_landscape.mp4` files (1920x1080) get CRITICAL resolution errors. `--fix` mode would re-encode them to portrait, corrupting the file.

**File:** `execution/validate_videos.py` lines 1241, 1305-1325

**Fix:** In `_validate_videos()`, skip `*_landscape.mp4` files from the Instagram portrait validation loop (matching the existing `_bg.mp4` exclusion). Add a separate lightweight landscape check:
```python
if video_path.stem.endswith("_landscape"):
    # Landscape videos are for Facebook/YouTube — validate 16:9 only
    result = _check_landscape_spec(video_path)
    ...
    continue
```

Add `_check_landscape_spec()` that validates:
- Resolution: 1920x1080
- Codec: H.264
- Duration: ≤900s
- File size: ≤100MB

**Test:** Add landscape validation tests in `test_validate_videos.py`.

### P0.2 CDN Upload Retry

**Problem:** `local_cdn.py` has zero retry logic. A single network hiccup loses the entire publish slot.

**File:** `execution/utils/local_cdn.py` lines 76-113

**Fix:** Add 3-attempt retry with exponential backoff:
```python
MAX_UPLOAD_RETRIES = 3
RETRY_DELAYS = [5, 30, 120]  # seconds

for attempt in range(MAX_UPLOAD_RETRIES):
    try:
        resp = requests.post(...)
        if resp.status_code == 200:
            url = resp.text.strip()
            if url.startswith("https://litter.catbox.moe/"):
                return url
            logger.error("Unexpected CDN URL domain: %s", url[:200])
    except requests.RequestException as exc:
        logger.warning("CDN upload attempt %d/%d failed: %s", attempt + 1, MAX_UPLOAD_RETRIES, exc)
    if attempt < MAX_UPLOAD_RETRIES - 1:
        time.sleep(RETRY_DELAYS[attempt])
return None
```

Also validate response URL domain (not just `https://` prefix).

**Test:** Add retry + domain validation tests in `test_local_cdn.py`.

### P0.3 CDN Failure in Instagram Retry Loop

**Problem:** `publish_all_platforms.py` line 207 — CDN upload failure hits `break` instead of `continue`, wasting the configured `max_retries`.

**File:** `execution/publish_all_platforms.py` ~line 207

**Fix:** Change `break` → `continue` so the retry loop re-attempts CDN upload.

**Test:** Existing `test_publish_all_platforms.py` covers retry logic.

### P0.4 OData Injection Escaping

**Problem:** `backlog_client.py` FIND formula handler inserts `val` into OData filter without `_esc()` at lines 151 and 159.

**File:** `execution/utils/backlog_client.py` lines 151, 159

**Fix:**
```python
# Line 151
return f"contains(fields/{field}_text, '{_esc(val)}')"
# Line 159
return f"contains(fields/{field}, '{_esc(val)}')"
```

Also promote the OData-fallback debug log (line ~479) from DEBUG to WARNING.

**Test:** Add injection test in `test_formula_translator.py`.

### P0.5 Review Server Auth Bypass

**Problem:** `review_server.py` exempts all `*_flow.mp4` paths from auth — a leftover from Meta App Review.

**File:** `execution/review_server.py` lines 232-239, 248-252

**Fix:** Remove the `_flow.mp4` auth bypass and CORS exemption entirely. If Meta re-requests screencasts, use a time-limited signed URL instead.

Also add strict `run_id` validation:
```python
import re
_SAFE_RUN_ID = re.compile(r'^[a-zA-Z0-9_\-]{1,64}$')
```

**Test:** Add auth bypass removal test in `test_review_server.py`.

### P0.6 Rate Limiter Global Lock

**Problem:** `rate_limiter.py` holds a single global lock during `time.sleep()`, serializing all domains.

**File:** `execution/utils/rate_limiter.py` lines 112-135

**Fix:** Replace global lock with per-domain locks:
```python
self._domain_locks: Dict[str, threading.Lock] = {}
self._domain_locks_lock = threading.Lock()

def _get_domain_lock(self, domain: str) -> threading.Lock:
    with self._domain_locks_lock:
        if domain not in self._domain_locks:
            self._domain_locks[domain] = threading.Lock()
        return self._domain_locks[domain]
```

`wait()` acquires `_get_domain_lock(domain)` instead of `self._lock`.

**Test:** Add concurrent domain test in `test_rate_limiter.py`.

### P0.7 Twitter FFmpeg Return Code

**Problem:** `publish_twitter.py` discards `subprocess.run()` result — corrupt partial files may be uploaded.

**File:** `execution/publish_twitter.py` lines 118-132

**Fix:** Check return code before using truncated file:
```python
result = subprocess.run([...], capture_output=True, text=True, timeout=300)
if result.returncode == 0 and truncated.exists():
    truncated_media.append(truncated)
else:
    logger.warning("Twitter: FFmpeg truncation failed (rc=%s), using original", result.returncode)
    truncated_media.append(mp)
```

**Test:** Add FFmpeg failure path test in `test_publish_twitter.py`.

---

## Phase 1 — Intelligence (Improve Content Quality)

8 fixes. Goal: close the feedback loop so the system learns what works.

### P1.1 Performance-to-Scoring Feedback Loop

**Problem:** Analytics table has `viral_score`, `save_rate`, `share_rate` per published post but data hits a dead end. The system never adjusts scoring weights based on actual engagement.

**File:** `execution/process_feedback.py` (new function)

**Fix:** Add `auto_tune_scoring_weights()`:
1. Read last 30 days of Analytics records via BacklogClient
2. Join with Blueprints to get `hook_formula` category per post
3. Compute average `viral_score` per hook formula category
4. Adjust `scoring_weights.yaml` `hook_formulas` weights proportionally:
   - Categories at ≥2x average: weight += 0.05 (capped at 0.50)
   - Categories at ≤0.5x average: weight -= 0.05 (floored at 0.05)
5. Write adjusted weights back to `scoring_weights.yaml`
6. Write changelog to `.tmp/runs/weight_adjustments.json`

Run weekly (add to `runbooks/weekly_inspo.sh`), not daily.

**Test:** Add unit test with mock Analytics data in `test_process_feedback.py`.

### P1.2 Cost Estimation Fix

**Problem:** `write_post_content.py:1013-1016` uses GPT-4o-mini pricing even when Haiku is active.

**File:** `execution/write_post_content.py` lines 1013-1016

**Fix:** Add pricing dict and use actual token counts:
```python
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": (0.25, 1.25),  # (input, output) per 1M tokens
    "gpt-4o-mini": (0.15, 0.60),
}
```

Use `_token_usage` dict (already tracked) instead of character-based estimation.

**Test:** Update cost tests in `test_write_post_content.py`.

### P1.3 YouTube `privacy_status` Config Mismatch

**Problem:** YAML key is `privacy_status`, code reads `privacy`. Config changes are silently ignored.

**File:** `execution/publish_youtube.py` line 204

**Fix:** Change to `yt_config.get("privacy_status", "public")`.

**Test:** Add config-read test in `test_publish_youtube_strict.py`.

### P1.4 Dead Hook System Cleanup

**Problem:** `HOOK_PATTERNS_BY_TEMPLATE` (12 dead template IDs) and `HOOK_PATTERNS` dict are never called.

**File:** `execution/generate_content.py` lines 79-159, 689

**Fix:** Remove both dicts. Remove the dead template ID branch at line 689. The active hook engine is `generate_hooks.py` + `hook_formulas.yaml`.

**Test:** Existing `test_generate_content.py` should still pass after removal.

### P1.5 Hardcoded Constants → Config

**Files:** `publish_youtube.py`, `publish_facebook.py`, `write_post_content.py`, `publishing.yaml`

**Fix:**
- `publish_youtube.py:131` — read `youtube.max_short_duration_seconds` from config instead of hardcoding 180.0
- `publish_facebook.py:51` — read `facebook.max_retries` from config (or fall back to `publisher.max_retries`)
- `write_post_content.py:467,512` — read `content_generation.temperature` from config (default 0.7)

Add missing config keys to `publishing.yaml`.

### P1.6 `is_due()` Timezone Fix

**Problem:** `scheduling.py:27` doesn't handle "Z"-suffix timestamps.

**File:** `execution/utils/scheduling.py` line 27

**Fix:** `scheduled_dt = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))`

**Test:** Add "Z"-suffix test in `test_schedule_cascade.py`.

### P1.7 Incremental Status Write Visibility

**Problem:** `publish_all_platforms.py:1230` logs failures at DEBUG level.

**Fix:** Promote to WARNING.

### P1.8 OData Fallback Visibility

**Problem:** `backlog_client.py` silently falls back to full-table scan at DEBUG level.

**Fix:** Promote to WARNING.

---

## Phase 2 — Hygiene (Reduce Tech Debt)

10 fixes. Goal: clean foundations for future development.

### P2.1 Remove Dead Carousel Logic
- `compose_blueprints.py:1016-1038` — carousel swap block
- `compose_blueprints.py` lines 74, 88, 98, 119 — old template ID comments
- `process_feedback.py:266-304` — `tighten_carousel_constraints`

### P2.2 Remove Airtable Remnants
- `api/blueprints.py:60` — `airtableusercontent.com` check
- `test_publish_all_platforms.py:1235` — `recXYZ123` ID format

### P2.3 Update Stale Test Fixtures
- `tests/fixtures/news_samples/sample_backlog_row.json` — carousel → reel, TPL_BRK1 → TPL_REE_NEWS
- `tests/golden/blueprint_pack.json` — reel blueprint shape
- `tests/fixtures/news_samples/sample_parsed_items.json` — add video metadata

### P2.4 Cache Security Hardening
- `cache.py` — add safe-key regex validation
- Add auto-purge on `__init__`, configurable `MAX_ENTRIES`

### P2.5 Review Server Hardening
- Per-session CSRF nonce (stored in Flask session)
- Rate limiting on `/login` (5 attempts/IP/minute)
- `max_records=50` cap on batch-review
- Strict `run_id` regex

### P2.6 BacklogClient Column Map Caching
Module-level column map cache keyed by `list_id`. Eliminates 8 API calls per `BacklogClient()` construction.

### P2.7 Add `edge-tts` to requirements.txt

### P2.8 Safe Zone Constant Consolidation
`validate_videos.py` — import `SAFE_TOP`, `SAFE_BOTTOM`, `SAFE_LEFT`, `SAFE_RIGHT` from `text_optimizer.py` instead of defining independently.

### P2.9 Alerting Channel Stub
Uncomment Slack webhook in `monitoring.yaml`. Add `SLACK_WEBHOOK_URL` to `.env.example`. Wire into `track_error_budget.py`.

### P2.10 Dead Config Removal
Remove `streaming_review` block from `publishing.yaml`.

---

## Execution Order

```
Phase 0 (Reliability):
  P0.1  Landscape validator → test
  P0.2  CDN retry + domain validation → test
  P0.3  CDN break→continue fix
  P0.4  OData escaping + log promotion → test
  P0.5  Auth bypass removal + run_id regex → test
  P0.6  Per-domain rate limiter → test
  P0.7  Twitter FFmpeg returncode → test
  → pytest tests/ -x

Phase 1 (Intelligence):
  P1.1  Performance feedback loop → test
  P1.2  Cost estimation fix → test
  P1.3  YouTube privacy_status → test
  P1.4  Dead hook cleanup → verify existing tests pass
  P1.5  Constants → config
  P1.6  is_due() timezone fix → test
  P1.7  Status write log promotion
  P1.8  OData fallback log promotion
  → pytest tests/ -x

Phase 2 (Hygiene):
  P2.1  Dead carousel logic removal
  P2.2  Airtable remnants
  P2.3  Stale fixtures update
  P2.4  Cache hardening → test
  P2.5  Review server hardening → test
  P2.6  BacklogClient caching → test
  P2.7  edge-tts requirement
  P2.8  Safe zone consolidation
  P2.9  Alerting stub
  P2.10 Dead config removal
  → pytest tests/ -x
```

## Files Modified (by phase)

### Phase 0
| File | Changes |
|------|---------|
| `execution/validate_videos.py` | Skip landscape files, add `_check_landscape_spec()` |
| `execution/utils/local_cdn.py` | Add retry loop + domain validation |
| `execution/publish_all_platforms.py` | break→continue, status write log promotion |
| `execution/utils/backlog_client.py` | `_esc()` in FIND handler, OData fallback log promotion |
| `execution/review_server.py` | Remove auth bypass, add run_id regex |
| `execution/utils/rate_limiter.py` | Per-domain locks |
| `execution/publish_twitter.py` | FFmpeg returncode check |
| `tests/test_validate_videos.py` | Landscape validation tests |
| `tests/test_local_cdn.py` | Retry + domain tests |
| `tests/test_formula_translator.py` | Injection test |
| `tests/test_review_server.py` | Auth bypass removal test |
| `tests/test_rate_limiter.py` | Concurrent domain test |

### Phase 1
| File | Changes |
|------|---------|
| `execution/process_feedback.py` | Add `auto_tune_scoring_weights()` |
| `execution/write_post_content.py` | Fix cost estimation with MODEL_PRICING dict |
| `execution/publish_youtube.py` | Read `privacy_status` from config, read max duration from config |
| `execution/generate_content.py` | Remove dead HOOK_PATTERNS dicts + dead template branch |
| `execution/publish_facebook.py` | Read max_retries from config |
| `execution/utils/scheduling.py` | Normalize "Z" suffix |
| `config/publishing.yaml` | Add facebook.max_retries, content_generation.temperature |
| `tests/test_process_feedback.py` | Auto-tune test |
| `tests/test_write_post_content.py` | Cost estimation test |
| `tests/test_schedule_cascade.py` | "Z"-suffix test |

### Phase 2
| File | Changes |
|------|---------|
| `execution/compose_blueprints.py` | Remove carousel swap block + stale comments |
| `execution/process_feedback.py` | Remove `tighten_carousel_constraints` |
| `execution/api/blueprints.py` | Remove Airtable CDN check |
| `execution/utils/cache.py` | Safe-key validation, auto-purge, MAX_ENTRIES |
| `execution/review_server.py` | Per-session CSRF, rate limiting, batch cap |
| `execution/utils/backlog_client.py` | Column map caching |
| `execution/validate_videos.py` | Import safe zone constants from canonical source |
| `execution/track_error_budget.py` | Slack webhook integration |
| `config/monitoring.yaml` | Uncomment Slack webhook |
| `config/publishing.yaml` | Remove streaming_review block |
| `requirements.txt` | Add edge-tts |
| `tests/fixtures/` | Update stale fixtures |
| `tests/golden/` | Update stale golden files |
| `tests/test_publish_all_platforms.py` | Fix Airtable-style record ID |

## Verification

1. Each phase: `pytest tests/ -x` must pass
2. P0: Manual dry-run of `publish_all_platforms.py --dry-run` to verify no landscape corruption
3. P1.1: `python execution/process_feedback.py --auto-tune --dry-run` to inspect proposed weight changes
4. P2.6: Verify BacklogClient init makes 8 API calls once, not per-request (add timing log)
