# End-to-End Publishing Reliability Analysis

Generated: 2026-03-17
Scope: Full trace from launchd plist to "post appears on Instagram" for all 5 niches.

---

## 1. Trigger Chain -- How Does Publishing Start?

### Plist Schedule Summary

| Niche | Brand | Plist | Local Time (IST) | UTC | NICHE_ID |
|-------|-------|-------|-------------------|-----|----------|
| ai_creators | Blackbox Brief | `com.genlab.instagram-publisher.plist` | 17:30 IST | 12:00 UTC | `ai_creators` |
| gaming | CriticalRush | `com.genlab.criticalrush-publisher.plist` | 18:10 IST | 12:40 UTC | `gaming` |
| sports | ClutchWire | `com.genlab.clutchwire-publisher.plist` | 17:40 IST | 12:10 UTC | `sports` |
| movies | SpliceReel | `com.genlab.splicereel-publisher.plist` | 17:50 IST | 12:20 UTC | `movies` |
| anime | FrameDrift | `com.genlab.framedrift-publisher.plist` | 18:00 IST | 12:30 UTC | `anime` |

All plists share:
- `BACKLOG_CONFIG_PATH`: `/Users/anarchistsid/GenLab/Content Scraper/config/lists_config.yaml`
- `TimeOut`: 1800 seconds (30 minutes)
- `KeepAlive`: false
- `RunAtLoad`: false

### Two Distinct Trigger Paths

**Path A -- Blackbox Brief (ai_creators)**

```
com.genlab.instagram-publisher.plist
  WorkingDirectory: /Users/anarchistsid/GenLab/Content Scraper
  ProgramArguments:
    scripts/launch_wrapper.sh /bin/bash -lc 'exec "${GENLAB_PROJECT_DIR}/runbooks/publisher_wrapper.sh"'
  EnvVars: GENLAB_PROJECT_DIR, MAX_BLUEPRINTS_PER_RUN=1, MIN_PRIORITY_SCORE=0.3, POSTIZ_SHADOW_MODE=true
```

Trace:
```
plist (12:00 UTC)
  -> scripts/launch_wrapper.sh
       sources Content Scraper/.env + CriticalRush/.env (set -a)
       exec /bin/bash -lc 'exec .../publisher_wrapper.sh'
  -> Content Scraper/runbooks/publisher_wrapper.sh
       exec orchestrator.sh publish
  -> Content Scraper/runbooks/orchestrator.sh "publish"
       load_dotenv (Content Scraper/.env, strips quotes)
       run_finalize_steps (5 steps if work pending)
       $VENV_PYTHON execution/publish_all_platforms.py --run-id ... --niche ai_creators --max-blueprints 1
```

**Path B -- All Other Niches (gaming, sports, movies, anime)**

```
com.genlab.{channel}-publisher.plist
  WorkingDirectory: /Users/anarchistsid/GenLab
  ProgramArguments:
    /bin/bash -lc 'exec "/Users/anarchistsid/GenLab/scripts/publish.sh"'
  EnvVars: NICHE_ID={niche}
```

Trace:
```
plist (12:10--12:40 UTC depending on niche)
  -> scripts/publish.sh
       discovers GENLAB_ROOT, sets PUBLISHER_ROOT="$GENLAB_ROOT/Content Scraper"
       sets GENLAB_PROJECT_DIR
       exec "$PUBLISHER_ROOT/runbooks/orchestrator.sh" publish
  -> Content Scraper/runbooks/orchestrator.sh "publish"
       load_dotenv (Content Scraper/.env, strips quotes)
       run_finalize_steps (5 steps if work pending)
       PUBLISH_NICHE="${NICHE_ID:-ai_creators}"
       $VENV_PYTHON execution/publish_all_platforms.py --run-id ... --niche $PUBLISH_NICHE
```

**Key Difference**: BB's plist uses `launch_wrapper.sh` which sources both `Content Scraper/.env` AND `CriticalRush/.env` (loading all credentials), and sets extra env vars (`MAX_BLUEPRINTS_PER_RUN`, `MIN_PRIORITY_SCORE`, `POSTIZ_SHADOW_MODE`). Other niches use `publish.sh` -> `orchestrator.sh` which only sources `Content Scraper/.env`.

**Relevant Files**:
- `/Users/anarchistsid/Library/LaunchAgents/com.genlab.instagram-publisher.plist` (BB)
- `/Users/anarchistsid/Library/LaunchAgents/com.genlab.criticalrush-publisher.plist`
- `/Users/anarchistsid/Library/LaunchAgents/com.genlab.clutchwire-publisher.plist`
- `/Users/anarchistsid/Library/LaunchAgents/com.genlab.splicereel-publisher.plist`
- `/Users/anarchistsid/Library/LaunchAgents/com.genlab.framedrift-publisher.plist`
- `/Users/anarchistsid/GenLab/scripts/launch_wrapper.sh` (lines 14-17: sources .env files)
- `/Users/anarchistsid/GenLab/scripts/publish.sh` (line 31: delegates to orchestrator.sh)
- `/Users/anarchistsid/GenLab/Content Scraper/runbooks/orchestrator.sh` (lines 116-139: publish mode)

### .env Loading: Quote-Stripping Fix

The `orchestrator.sh` `load_dotenv()` function (lines 40-54) strips surrounding quotes from env var values:
```bash
var_value="${var_value#\'}" ; var_value="${var_value%\'}"
var_value="${var_value#\"}" ; var_value="${var_value%\"}"
```
This is the fix for the issue where quoted tokens were rejected by the Meta API.

### Pre-Publish Finalize Steps

Before `publish_all_platforms.py` runs, `orchestrator.sh` executes 5 finalize steps (lines 86-95):
1. `process_review.py` -- process visual review decisions
2. `adapt_for_platforms.py` -- YouTube + X content adaptation
3. `fetch_broll.py` -- Pexels B-roll for non-BB niches
4. `render_text_overlays.py` -- render drafted videos
5. `validate_videos.py --fix` -- validate + auto-fix rendered videos

These are non-fatal (`run_nonfatal`). Skipped entirely if no DRAFTED or actionable VISUAL_READY blueprints exist (preflight check, lines 72-84).

---

## 2. Blueprint Selection -- What Gets Published?

Entry point: `publish_all_platforms.py` -> `main()` -> `_main_locked()`

File: `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_all_platforms.py`

### Gate Execution Order (Production Mode)

```
Gate 1: File lock (per-niche)                    [line 1586-1597]
Gate 2: SharePoint query (VISUAL_READY + niche)  [line 1666]
Gate 3: Approval gate (action_taken=approved)    [line 1675-1684]
Gate 4: Strict creator-video gate (BB only)      [line 1685-1697]
Gate 5: Schedule gate (scheduled_for <= now+15m)  [line 1742-1747]
Gate 6: Video-only gate (local MP4 exists)        [line 1756-1769]
Gate 7: Per-niche dedup (max 1 per niche/run)     [line 1771-1789]
Gate 8: Score floor (priority_score >= 0.3)       [line 1791-1809]
Gate 9: Gap guard (min 3h since last publish)     [line 1811-1823]
```

### Gate Details

**Gate 1: Exclusive File Lock** (line 1586-1597)
- Per-niche lock file at `Content Scraper/.tmp/publisher-{niche}.lock`
- Uses `fcntl.LOCK_EX | fcntl.LOCK_NB` -- non-blocking, exits immediately if another instance running
- Prevents duplicate publishing from overlapping daemon runs

**Gate 2: SharePoint Query** (line 1666)
```python
client.get_blueprints_by_status("VISUAL_READY", niche_id=niche_filter)
```
- OData filter: `fields/status eq 'VISUAL_READY' and fields/niche_id eq '{niche}'`
- Returns all VISUAL_READY blueprints for the target niche
- Does NOT consume a daily slot -- just fetches candidates

**Gate 3: Approval Gate** (lines 1675-1684)
```python
blueprints = [bp for bp in all_visual_ready
    if bp["fields"]["action_taken"].strip().lower() == "approved"]
```
- Requires explicit human approval via dashboard
- `SKIP_APPROVAL_GATE` env var can bypass (not set in any plist)
- `TEST_MODE` also bypasses (retired since Sprint 30)
- Filtered-out items stay at VISUAL_READY -- no state change

**Gate 4: Strict Creator-Video Gate** (lines 1685-1697)
- BB-specific: requires `format == "reel"` for ai_creators blueprints
- Non-BB niches bypass this (they may have format != "reel")
- Controlled by `STRICT_CREATOR_VIDEO_ONLY` flag from `creator_policy.py`

**Gate 5: Schedule Gate** (lines 1742-1747, helper `_select_due_blueprints` lines 1132-1178)
- Requires `scheduled_for` field to be set (unscheduled items are ALWAYS skipped)
- Item must be due: `effective_scheduled_datetime <= now + 15 minutes`
- A/B publish offset is applied: `scheduled_for + ab_publish_offset_hours`
- Sorted by schedule time ascending (earliest first)
- Max blueprints capped at `max_blueprints_per_run` (default 1 via plist, but config says 5)
- `--force` bypasses the time check but NOT the schedule requirement

**Gate 6: Video-Only Gate** (lines 1756-1769) -- IMPORTANT: runs BEFORE per-niche gate
```python
due_blueprints = [bp for bp in due_blueprints
    if _has_local_video_file(bp.get("fields", {}))]
```
- Checks that at least one local video file (`.mp4`, `.mov`, `.webm`, `.m4v`) exists on disk
- Filtered items do NOT consume the niche's daily slot (this was a recent fix)
- Missing files = rendered video was cleaned up by disk quota manager

**Gate 7: Per-Niche Dedup** (lines 1771-1789)
- At most 1 blueprint per niche per run
- Second blueprint from same niche is deferred to next run
- Prevents double-posting when multiple approved items are due

**Gate 8: Score Floor** (lines 1791-1809)
- `MIN_PRIORITY_SCORE` defaults to 0.3 (set in BB plist, read from env in others)
- Low-quality blueprints below the threshold are skipped

**Gate 9: Gap Guard** (lines 1811-1823)
- `min_publish_gap_hours` = 3 (from publishing.yaml)
- Queries PUBLISHED blueprints from last 24h
- If last publish was < 3h ago, ALL due blueprints are deferred
- `--force` and `TEST_MODE` bypass this
- **BUG RISK**: This is global, not per-niche -- one niche's recent publish could block another niche

### Verified Gate Order (Video gate before niche gate)

The video-only gate (Gate 6) runs at line 1756, before the per-niche dedup at line 1771. This is the correct order -- confirmed in current code. A no-video blueprint no longer consumes the niche's daily slot.

---

## 3. Per-Platform Publish -- What Happens for Each Platform?

### Enabled Platforms (from publishing.yaml)

```yaml
enabled_platforms:
  - instagram
  - facebook
  - youtube
  - tiktok       # but disabled in tiktok config: enabled: false
  - threads
```

Twitter is commented out in the enabled list. TikTok and Threads are listed but typically skipped due to missing credentials.

### Dispatch Flow

After all gates pass, per-blueprint publishing proceeds:

```
For each due_blueprint:
  1. Cross-channel isolation gate: _require_niche_id()         [line 1965-1980]
  2. Content safety gate: _has_publishable_content()            [line 1991-2000]
  3. Load existing platform_publish_status (retry logic)        [line 2012-2020]
  4. Filter: skip platforms already PUBLISHED/PUBLISHING         [line 2023-2027]
  5. FB credentials pre-flight check                            [line 2029-2033]
  6. Twitter daily post limit                                    [line 2035-2041]
  7. Per-platform payload completeness: _platform_payload_ready [line 2045-2054]
  8. Daily cap gate (DailyCapEnforcer)                           [line 2056-2066]
  9. Mark remaining platforms as PUBLISHING in SharePoint        [line 2080-2094]
  10. PublishGatekeeper.evaluate() per platform                  [line 2108-2123]
  11. Build PublishPayload                                       [line 2126-2128]
  12. _dispatch_niche_aware() -- concurrent ThreadPoolExecutor   [line 2134-2163]
  13. Persist each result incrementally to SharePoint            [line 2166-2174]
  14. Log to Publishing_Analytics + seed Analytics table         [line 2186-2238]
  15. Update blueprint status (PUBLISHED or NEEDS_REVIEW)        [line 2271-2312]
  16. Register PendingFeedbackTask for learning loop              [line 2327-2355]
```

### Credential Resolution

File: `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/publishing/niche_credentials.py`

```python
NICHE_CREDENTIAL_PREFIXES = {
    "sports": "CLUTCHWIRE",
    "movies": "SPLICEREEL",
    "anime": "FRAMEDRIFT",
    "gaming": "CRITICALRUSH",
    "ai_creators": "BLACKBOXBRIEF",
    "ai_tech": "BLACKBOXBRIEF",
}
```

Resolution pattern (`resolve_niche_env`, line 31-54):
1. Look up prefix for niche (e.g., `sports` -> `CLUTCHWIRE`)
2. Try `{PREFIX}_{SUFFIX}` (e.g., `CLUTCHWIRE_META_ACCESS_TOKEN`)
3. If present, return it
4. If missing, return `""` -- **never falls back to global/BB credentials**
5. Only `ai_creators`/`ai_tech` with prefix `BLACKBOXBRIEF` will use `BLACKBOXBRIEF_{SUFFIX}`

The cross-channel guard (`_dispatch_niche_aware`, lines 310-322) blocks all tasks if `niche_id` is invalid.

### Instagram Publish Flow

Files:
- `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/platforms/instagram.py` (native client)
- `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_to_instagram.py` (legacy + library)
- `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_all_platforms.py` (lines 520-724, orchestrator)

**Two publish paths exist**:

Path A (native client, used by `_dispatch_niche_aware`):
```
_dispatch_niche_aware()
  -> _resolve_client_kwargs("instagram", niche_id)
     -> resolve_meta_credentials(niche_id) -> {ig_access_token, ig_user_id}
  -> _upload_ig_media_to_cdn(payload)
     -> _ensure_ig_audio_track() -- add silent AAC if missing
     -> upload_to_litterbox() -- 24h CDN, up to 1GB
  -> InstagramClient(**kwargs).publish(payload)
     -> _publish_reel(video_url, caption, share_to_feed, cover_url)
        -> _create_reel_container: POST graph.facebook.com/{v21.0}/{ig_user_id}/media
        -> _poll_container_status: GET graph.facebook.com/{v21.0}/{creation_id}
           poll_interval: 5s initially, 10s after 30s elapsed
           max_poll: 120 seconds
           states: IN_PROGRESS -> FINISHED (proceed) / PUBLISHED (skip publish) / ERROR (fail)
        -> _media_publish: POST graph.facebook.com/{v21.0}/{ig_user_id}/media_publish
```

Path B (legacy, used by `publish_instagram()` function, lines 520-724):
```
publish_instagram(blueprint_fields, config, story_fields)
  -> resolve_meta_credentials(niche_id)
  -> build_caption()
  -> Probe video duration via ffprobe
  -> If <= 900s: single reel via publish_reel() (CDN upload ONCE before retry loop)
  -> If > 900s: split into <=59s segments, carousel of video clips
  -> publish_reel() calls graph.facebook.com (3-step: container -> poll -> publish)
```

**Which path runs?** The native path via `_dispatch_niche_aware()` (Path A) runs for all platforms when `not args.dry_run` (line 2103). The legacy `publish_instagram()` is NOT called in the current native dispatch flow.

**API endpoint**: Always `graph.facebook.com` -- confirmed in:
- `InstagramClient._base_url = f"https://graph.facebook.com/{api_version}"` (instagram.py line 57)
- `publish_to_instagram.py` lines 175, 237, 268, 306, 365, 398, 449, 501, 531 -- all `graph.facebook.com`

**Container polling timeout**: 120 seconds (instagram.py `_DEFAULT_MAX_POLL_SECONDS`)

**On success**:
- `PublishResult(success=True, post_id=..., post_url=...)` returned
- Post ID stored in `platform_post_ids["instagram"]`
- DailyCapEnforcer incremented
- platform_publish_status updated to `{"status": "PUBLISHED", "post_id": "..."}` in SharePoint

**On failure**:
- `PublishResult(success=False, error="...")` returned
- platform_publish_status set to `"FAILED"` in SharePoint
- Error logged to Publishing_Analytics
- Other platforms continue (best_effort strategy)

### YouTube Publish Flow

File: `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/platforms/youtube.py`

```
_dispatch_niche_aware()
  -> _resolve_client_kwargs("youtube", niche_id)
     -> resolve_youtube_credentials(niche_id)
        -> {PREFIX}_YOUTUBE_CLIENT_ID, {PREFIX}_YOUTUBE_CLIENT_SECRET, {PREFIX}_YOUTUBE_REFRESH_TOKEN
        -> client_id/client_secret may fall back to global (shared OAuth app)
  -> YouTubeClient(**kwargs).publish(payload)
     -> _get_access_token() -- OAuth2 token refresh (50-min TTL cache)
        POST https://oauth2.googleapis.com/token with grant_type=refresh_token
     -> _upload_video() -- resumable upload via googleapiclient
        MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=10MB)
        service.videos().insert(part="snippet,status", body=..., media_body=...)
        Chunked upload loop (max 200 chunks)
     -> Returns video_id, post_url = f"https://youtube.com/shorts/{video_id}"
```

**Upload type**: Resumable upload via YouTube Data API v3 `videos.insert` (10MB chunks)

**On quota exceeded**: `googleapiclient.errors.HttpError` raised with `uploadLimitExceeded`, caught by `PUBLISH_OP_ERRORS` -> result is `FAILED`

**On success**: Video ID returned (e.g., `_kcsv1ReIoA`)

### Facebook Publish Flow

Files:
- `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/platforms/facebook.py` (native client)
- `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_facebook.py` (legacy)

```
_dispatch_niche_aware()
  -> _resolve_client_kwargs("facebook", niche_id)
     -> resolve_fb_credentials(niche_id) -> (access_token, page_id)
  -> FacebookClient(**kwargs).publish(payload)
     -> POST graph.facebook.com/{v21.0}/{page_id}/videos (video upload)
     -> Retry with exponential backoff on 429, 5xx, connection errors
```

**Pre-flight**: Page token verified before blueprint loop (lines 1865-1888):
```python
verify_page_token(fb_page_id, fb_access_token, api_version)
```

**On "problem uploading your video file"**: Results in HTTP error -> `FAILED` status, logged

### X/Twitter Publish Flow

Currently **disabled** in `publishing.yaml` (line 159: `# - twitter`). When enabled:
- OAuth 1.0a credentials per niche
- Rate limit coordination via `threading.Event`
- Fresh `TwitterClient` per call (tweepy not thread-safe)
- Daily post limit (1/day on free tier)

### Threads Publish Flow

Listed in enabled_platforms but typically skipped due to `_platform_payload_ready` returning `(False, "unsupported_platform")` or missing credentials.

---

## 4. Post-Publish State Updates

File: `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_all_platforms.py` (lines 2271-2355)

### SharePoint Fields Updated

After each blueprint publishes:

1. **platform_publish_status** (JSON string): Updated incrementally per platform
   ```json
   {
     "instagram": "{\"status\": \"PUBLISHED\", \"post_id\": \"17882748348378860\"}",
     "facebook": "{\"status\": \"PUBLISHED\", \"post_id\": \"2420525511721320\"}",
     "youtube": "{\"status\": \"PUBLISHED\", \"post_id\": \"_kcsv1ReIoA\"}",
     "tiktok": "SKIPPED_PAYLOAD_UNSUPPORTED_PLATFORM",
     "threads": "SKIPPED_PAYLOAD_UNSUPPORTED_PLATFORM"
   }
   ```

2. **status**: Transitions from `VISUAL_READY` -> `PUBLISHED`
   - Uses `can_transition()` from workflow state machine
   - If transition invalid, sets `NEEDS_REVIEW` with error_log (safety net)

3. **Notably NOT updated**: `published_at` (column does not exist on Blueprints list)

### Publishing_Analytics Record

Created per platform (lines 2188-2205):
```python
client.log_publish_result(
    candidate_id, platform, status="SUCCESS"/"FAILED",
    post_id, platform_format, time_to_publish_seconds,
    error_message, file_size_bytes, blueprint_record_id, niche_id
)
```

Also for skipped platforms (lines 2224-2238): status="SKIPPED"

### Analytics Table Seed

For successful publishes (lines 2207-2222):
```python
client.upsert_analytics(
    post_id, platform, insights={},  # empty -- fetch_insights fills later
    blueprint_record_id, candidate_id, published_at, niche_id
)
```

### PendingFeedback Task

Created for each successfully published platform (lines 2327-2355):
```python
PendingFeedbackTask(
    content_id=candidate_id, platform=p, niche_id=bp_niche_id,
    published_at=now, platform_post_id=pid, content_type=format,
    hook_text=..., hook_length=..., hook_type=...
)
```
Used by the learning loop to collect engagement metrics at 6h/24h/48h/168h windows.

### What Happens If SharePoint Update Fails?

Line 2310-2312:
```python
try:
    client.blueprints.update(record_id, update_fields, typecast=True)
except PUBLISH_OP_ERRORS as exc:
    logger.error("  Backlog update failed: %s", exc, exc_info=True)
```

**The post is live on Instagram/YouTube/Facebook but the blueprint status stays VISUAL_READY.** On the next daemon run, the retry logic (Gate: existing `platform_publish_status`) would detect the platform is already `PUBLISHED` and skip it. The blueprint would just need to be re-advanced to `PUBLISHED` status manually or on the next successful update.

**Mitigation**: Incremental status updates (line 2166-2174) persist each platform result immediately, so a crash after IG but before YT still records IG as PUBLISHED.

---

## 5. Daily Cap Enforcement

File: `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/publishing/daily_cap.py`

### How It Counts Today's Publishes

```python
def _load_today_counts(self) -> dict[str, int]:
    items = self._client.publishing_analytics.all()
    for item in items:
        if status != "SUCCESS": continue
        if self._niche_id and item_niche != self._niche_id: continue
        if pub_at not starts with today_str: continue
        counts[platform] += 1
```

### Per-Niche Scope

When `niche_id` is provided (line 168-170):
```python
if self._niche_id:
    item_niche = str(fields.get("niche_id") or "").strip()
    if item_niche and item_niche != self._niche_id:
        continue
```

Each niche's cap is independent -- gaming's Instagram publish does not count against ai_creators.

### Timezone Boundary

Uses **UTC calendar day** (line 142-143):
```python
def _today_utc(self) -> date:
    return datetime.now(timezone.utc).date()
```

`today_str = self._today_utc().isoformat()` produces `"2026-03-17"`.

### Can It Be Fooled?

The `published_at` field could be in a different timezone format, but the check uses `startswith(today_str)` which matches ISO date prefixes. Since all publishes record UTC timestamps, and the cap queries by UTC date, timezone drift should not fool it.

However: the `_load_today_counts` fetches ALL items from Publishing_Analytics (no server-side date filter), then filters client-side. This becomes expensive over time but is fail-safe.

### Cap Values

From `genlab-core/config/platform_caps.yaml`:
```yaml
daily_post_cap:
  instagram: 5   # 1 per niche x 5 niches
  youtube: 5
  facebook: 5
  tiktok: 5
  twitter: 5
  threads: 5
```

### Session Counter

After `record_publish()`, an in-memory counter prevents a second publish in the same process from seeing stale SharePoint counts.

---

## 6. Retry and Recovery

### Partial Publish Handling

If Instagram fails but YouTube succeeds (lines 2246-2268):
- `published_platforms = ["youtube"]`, `failed_platforms = ["instagram"]`
- `best_effort` strategy: blueprint advances to PUBLISHED (any success counts)
- `platform_publish_status` records: `{"youtube": "PUBLISHED", "instagram": "FAILED"}`
- Warning logged: `PARTIAL PUBLISH for {cid}: succeeded on [youtube], failed on [instagram]`

### Re-Running the Publisher

The publisher can be re-run to retry failed platforms (line 2023-2027):
```python
platforms_to_publish = [
    p for p in enabled
    if existing_status.get(p) not in ("PUBLISHED", "PUBLISHING")
]
```

On re-run:
- Loads `platform_publish_status` from SharePoint
- Skips platforms already `PUBLISHED` or `PUBLISHING`
- Retries platforms that are `FAILED`, `SKIPPED_*`, or absent
- The `PUBLISHING` intermediate state prevents double-publish from overlapping runs

### "Already Published" Detection

For each platform, existing status is checked before dispatch:
```python
existing_status = json.loads(fields.get("platform_publish_status", "")) or {}
```
Platforms with status `PUBLISHED` are skipped entirely.

### best_effort Strategy

From `publishing.yaml`: `publish_strategy: "best_effort"`

Rules:
- PUBLISHED if ANY platform succeeds
- SKIPPED platforms (no credentials, daily limit) are acceptable
- Only truly FAILED platforms are counted as failures
- `all_or_nothing` alternative: requires ALL enabled platforms to succeed

### PUBLISHING Intermediate State

Before API calls, all target platforms are marked `PUBLISHING` in SharePoint (line 2083-2094). This prevents:
- A second daemon run from picking up the same blueprint
- Even if the file lock fails somehow

---

## 7. Verification: Most Recent Publish Logs

### Gaming Publish -- 2026-03-17 12:40 IST (com.genlab.criticalrush-publisher)

Log: `/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/publish_20260317_124004_pub.log`

```
Niche: gaming
Niche isolation: strict (--niche gaming)
Platforms: [instagram, facebook, youtube, tiktok, threads]
Strategy: best_effort | Max/run: 1 | Score floor: 0.30

VISUAL_READY: 3 total, 1 approved, 2 filtered out
Schedule: 1 due (scheduled_for = 2026-03-17T06:30:00+00:00)

Blueprint: 81ac4b1c82b7007b [reel]
  tiktok: SKIPPED (unsupported_platform)
  threads: SKIPPED (unsupported_platform)
  Dispatched: [instagram, facebook, youtube]

  Instagram:
    CDN upload: 81ac4b1c82b7007b_reel.mp4 (13.0 MB) -> litterbox
    CDN URL ready in ~18s
    Container created: 18060768287429968
    Processing complete: 29s
    PUBLISHED: ID 17882748348378860

  Facebook:
    Video published in ~10s
    PUBLISHED: ID 2420525511721320

  YouTube:
    Upload progress: 76% -> complete
    PUBLISHED: ID _kcsv1ReIoA

  Total publish time: 60.1s
  All 3 platforms SUCCESS
```

### AI Creators Publish -- 2026-03-17 12:00 IST (com.genlab.instagram-publisher)

Log: `/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/publish_20260317_120001_pub.log`

```
Niche: ai_creators
9 VISUAL_READY, 8 approved, 1 filtered out
Schedule gate: skipped 7 (not due or unscheduled)
Video-only gate: skipped 1 (no local video file)

Result: 0 blueprints published (all gated out)
```

The one due+approved blueprint had no local video file -> properly blocked by video-only gate.

---

## 8. Known Reliability Issues

### Issue 1: Shell .env Loader Didn't Strip Quotes (FIXED)

**Problem**: Bash `source .env` preserves literal quotes in values. Meta API rejected tokens with leading/trailing quotes.

**Fix**: `orchestrator.sh` `load_dotenv()` (lines 48-49) strips single and double quotes:
```bash
var_value="${var_value#\'}" ; var_value="${var_value%\'}"
var_value="${var_value#\"}" ; var_value="${var_value%\"}"
```

**File**: `/Users/anarchistsid/GenLab/Content Scraper/runbooks/orchestrator.sh` lines 40-54

### Issue 2: Video-Only Gate Ran After Per-Niche Gate (FIXED)

**Problem**: A no-video blueprint passed the per-niche gate first, consuming the niche's daily slot. The video-only gate then rejected it, but the slot was already used.

**Fix**: Video-only gate (line 1756) now runs BEFORE per-niche dedup (line 1771). Verified in current code:
```python
# ── Video-only gate: MUST run BEFORE per-niche dedup so a
# no-video blueprint doesn't consume the niche's daily slot. ──
```

**File**: `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_all_platforms.py` lines 1756-1769

### Issue 3: Rendered Videos in .tmp/ Deleted by Cleanup (MITIGATED)

**Problem**: Disk cleanup (`cleanup_artifacts.py`) deleted rendered videos from `.tmp/runs/*/rendered/` before the publisher could find them.

**Mitigation**:
- Publishing lock file (`.publishing_lock`) placed in run directory during publish (line 1938-1942)
- `disk_quota.py._is_published()` checks for MP4s in protected directories
- Videos now also stored in `.tmp/media/videos/` (longer-lived)

**Remaining risk**: If rendered video is ONLY in a run dir that gets cleaned before any publish attempt, the video-only gate catches it but the blueprint stays unpublished until the next pipeline run re-renders.

### Issue 4: Rejected Blueprints Occupied Schedule Slots (FIXED)

**Problem**: Dashboard rejection of a blueprint left it at VISUAL_READY with a `scheduled_for` value. The schedule gate saw it as "due", the approval gate rejected it, but it occupied the schedule window and blocked approved posts.

**Fix**: Approval gate filters BEFORE schedule gate. The sequence now is:
1. Query VISUAL_READY
2. Filter by `action_taken=approved`
3. THEN check schedule
This means rejected items never enter the schedule selection pool.

### Issue 5: Gap Guard Is Global, Not Per-Niche (POTENTIAL)

**Problem**: `_check_recent_publish_gap()` (line 1310-1379) queries ALL PUBLISHED blueprints from last 24h regardless of niche. With `min_publish_gap_hours=3`, a gaming publish at 12:40 would block sports at 12:10+3h=15:10 (but sports runs at 12:10, which is before gaming).

**Actual impact**: Because niches run at different times (12:00, 12:10, 12:20, 12:30, 12:40 UTC) and the gap check queries all niches' published blueprints, a niche could be deferred if another niche published within 3 hours. However, the gap check happens ONLY within a single run, and each run is niche-isolated by `--niche` flag. The query for "recent publishes" would see OTHER niches' publishes but would still trigger the gap guard.

**Severity**: Medium -- could cause late-day niches to skip publishing if an early-day niche published within the gap window. Should be per-niche.

### Issue 6: Publishing_Analytics OData Filter Failures (OBSERVED)

In the 2026-03-17 12:40 log (lines 925-963), multiple OData filter queries against Publishing_Analytics fail with `invalidRequest`:
```
A field provided for filtering is not valid in that context
```

The `graph_proxy` falls back to client-side filtering (fetching all items then filtering in Python). This works but is slow and increases SharePoint API load. The `analytics_id` field may not be indexed for server-side filtering.

---

## Summary: Complete Flow Diagram

```
launchd plist (12:00-12:40 UTC per niche)
  |
  v
scripts/publish.sh (or launch_wrapper.sh for BB)
  |
  v
Content Scraper/runbooks/orchestrator.sh "publish"
  |-- load_dotenv (quote-stripping)
  |-- run_finalize_steps (5 steps, non-fatal)
  |
  v
execution/publish_all_platforms.py --run-id ... --niche {niche}
  |
  |-- [Gate 1] File lock (per-niche)
  |-- [Gate 2] SharePoint: VISUAL_READY + niche_id filter
  |-- [Gate 3] Approval: action_taken == "approved"
  |-- [Gate 4] Creator-video: format == "reel" (BB only)
  |-- [Gate 5] Schedule: scheduled_for <= now + 15m
  |-- [Gate 6] Video-only: local MP4 exists
  |-- [Gate 7] Per-niche dedup: max 1 per niche
  |-- [Gate 8] Score floor: priority_score >= 0.3
  |-- [Gate 9] Gap guard: 3h since last publish
  |
  v (for each surviving blueprint)
  |
  |-- [Per-platform]
  |   |-- _require_niche_id() -- cross-channel guard
  |   |-- _has_publishable_content() -- content safety
  |   |-- Skip already PUBLISHED platforms
  |   |-- FB credentials pre-flight
  |   |-- _platform_payload_ready() -- payload completeness
  |   |-- DailyCapEnforcer.can_publish()
  |   |-- PublishGatekeeper.evaluate()
  |   |-- Mark PUBLISHING in SharePoint
  |   |
  |   v
  |   _dispatch_niche_aware() (ThreadPoolExecutor)
  |     |
  |     |-- Instagram:
  |     |   CDN upload (litterbox) -> container create -> poll (120s) -> publish
  |     |   graph.facebook.com/v21.0/{ig_user_id}/media + media_publish
  |     |
  |     |-- YouTube:
  |     |   OAuth2 token refresh -> resumable upload (10MB chunks)
  |     |   Data API v3 videos.insert
  |     |
  |     |-- Facebook:
  |     |   Page token -> video upload with retry
  |     |   graph.facebook.com/v21.0/{page_id}/videos
  |     |
  |     v
  |   Per-platform results (PUBLISHED / FAILED)
  |
  v
  |-- Update SharePoint: platform_publish_status, status -> PUBLISHED
  |-- Log Publishing_Analytics (per platform)
  |-- Seed Analytics table (for fetch_insights)
  |-- Register PendingFeedbackTask (for learning loop)
  |
  v
[Post-publish]
  |-- fetch_insights.py (engagement metrics)
  |-- write_run_report.py
```

### Critical File Paths

| Component | Path |
|-----------|------|
| Publisher orchestrator | `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_all_platforms.py` |
| Shell orchestrator | `/Users/anarchistsid/GenLab/Content Scraper/runbooks/orchestrator.sh` |
| Shared publish script | `/Users/anarchistsid/GenLab/scripts/publish.sh` |
| BB launch wrapper | `/Users/anarchistsid/GenLab/scripts/launch_wrapper.sh` |
| Instagram client (native) | `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/platforms/instagram.py` |
| Instagram publish (legacy) | `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_to_instagram.py` |
| YouTube client | `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/platforms/youtube.py` |
| Facebook publish | `/Users/anarchistsid/GenLab/Content Scraper/execution/publish_facebook.py` |
| Platform registry | `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/platforms/registry.py` |
| Niche credentials | `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/publishing/niche_credentials.py` |
| Daily cap enforcer | `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/publishing/daily_cap.py` |
| Publishing gatekeeper | `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/platforms/gatekeeper.py` |
| Workflow state machine | `/Users/anarchistsid/GenLab/Content Scraper/execution/utils/workflow_state_machine.py` |
| Creator policy | `/Users/anarchistsid/GenLab/Content Scraper/execution/utils/creator_policy.py` |
| Publishing config | `/Users/anarchistsid/GenLab/Content Scraper/config/publishing.yaml` |
| Platform caps | `/Users/anarchistsid/GenLab/genlab-core/config/platform_caps.yaml` |
| Publish logs | `/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/publish_*.log` |
