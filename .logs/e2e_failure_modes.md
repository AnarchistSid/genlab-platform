# GenLab End-to-End Failure Modes Analysis

Generated: 2026-03-17

## Methodology

Systematically traced error handling paths across all subsystems:
- `Content Scraper/execution/publish_all_platforms.py` (BB legacy publisher, ~1700 LOC)
- `genlab-core/src/genlab_core/publishing/publish_all_platforms.py` (canonical publisher, ~530 LOC)
- `genlab-core/src/genlab_core/pipeline/stage_runner.py` (stage execution wrapper)
- `genlab-core/src/genlab_core/pipeline/pipeline_runner.py` (pipeline orchestrator)
- `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py`
- `genlab-core/src/genlab_core/pipeline/stages/validate_videos.py`
- `genlab-core/src/genlab_core/publishing/daily_cap.py`
- `genlab-core/src/genlab_core/publishing/niche_credentials.py`
- `genlab-core/src/genlab_core/publishing/analytics_recorder.py`
- `genlab-core/src/genlab_core/platforms/gatekeeper.py`
- `genlab-core/src/genlab_core/platforms/instagram.py`
- `genlab-core/src/genlab_core/http/backlog_client.py`
- `genlab-core/src/genlab_core/http/circuit_breaker.py`
- `genlab-core/src/genlab_core/engagement/comment_processor.py`
- `genlab-core/src/genlab_core/engagement/poller.py`
- `genlab-core/src/genlab_core/context.py`
- Dashboard server and API modules

---

## 1. PUBLISHER FAILURE MODES

### [PUB-01] SharePoint Update Fails After Successful Publish (Ghost Publish)
**Severity:** CRITICAL
**Location:** `genlab-core/src/genlab_core/publishing/publish_all_platforms.py:449-460`
**Trigger:** SharePoint is rate-limited or temporarily unreachable when the publisher attempts to write the final PUBLISHED status after platform publish succeeds.
**Symptom:** Post is live on Instagram/YouTube/etc but blueprint stays as PUBLISHING in SharePoint. Next run may select it again and double-publish.
**Current handling:** Exception is logged but swallowed:
```python
except Exception as exc:
    logger.error("[publish] Failed to update final status: %s", exc)
```
No retry. No fallback write. No alert.
**Risk:** MEDIUM (SharePoint is generally reliable, but rate limits hit during batch operations)
**Fix status:** OPEN

### [PUB-02] PUBLISHING Status Set But Publish Fails Completely (Stuck Blueprint)
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/publishing/publish_all_platforms.py:384-391`
**Trigger:** Status is set to PUBLISHING (line 385-389) but all platforms fail. The finally block (449-460) sets it back to VISUAL_READY only if no platforms succeeded. But if the status-set at line 385 fails, the blueprint remains in whatever state it was.
**Symptom:** If all platforms fail AND the final status update also fails, blueprint stays as PUBLISHING indefinitely. No pipeline stage will pick it up.
**Current handling:** PUBLISHING -> VISUAL_READY on all-fail is correct. But the initial PUBLISHING status set is also try/except pass-through.
**Risk:** LOW (requires two failures in sequence)
**Fix status:** MITIGATED (final status restores VISUAL_READY on failure)

### [PUB-03] CDN Upload Failure Returns Empty Media Paths (BB Publisher)
**Severity:** HIGH
**Location:** `Content Scraper/execution/publish_all_platforms.py:218-219`
**Trigger:** All CDN uploads to litterbox fail (CDN down, network issues). `_upload_ig_media_to_cdn` returns a payload with empty `media_paths` but does not raise.
**Symptom:** The canonical publisher (genlab-core) handles this: `_dispatch_niche_aware` checks `if not payload.media_paths` and returns FAILED. But the BB publisher path at `publish_instagram()` also checks at line 571 `if not visual_paths`. Acceptable. However, the error is logged but the blueprint is not demoted or marked as errored.
**Current handling:** Returns None from `publish_instagram`, counted as platform failure.
**Risk:** MEDIUM (CDN failures are transient but block all IG publishes)
**Fix status:** MITIGATED (failure properly counted; no retry mechanism for CDN)

### [PUB-04] Video File Deleted Between Selection and Upload
**Severity:** HIGH
**Location:** `Content Scraper/execution/publish_all_platforms.py:616-621`
**Trigger:** Disk cleanup cron (`find .tmp/media/videos/ -mtime +1 -delete`) runs while publisher is mid-execution. Blueprint was selected with valid visual_paths, but by the time Instagram CDN upload starts, the file is gone.
**Symptom:** `publish_instagram` catches this at line 616-621 and logs an error, returns None. Post is not published but blueprint stays VISUAL_READY (it reverts from PUBLISHING on failure).
**Current handling:** Properly returns None. The blueprint will be retried next run.
**Risk:** MEDIUM (timing window exists when cleanup cron overlaps with publish window)
**Fix status:** MITIGATED (cleanup protection in `disk_quota.py._is_published()` protects published runs, but not mid-publish runs)

### [PUB-05] PID Lock Race Condition (Canonical Publisher)
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/publishing/publish_all_platforms.py:105-118`
**Trigger:** Two publisher processes start nearly simultaneously. Process A writes PID file, process B reads an empty/incomplete PID file. `int(self.path.read_text().strip())` raises ValueError, which is caught, and lock is removed. Both processes acquire the lock.
**Symptom:** Two publishers run concurrently for the same niche, potentially double-publishing.
**Current handling:** PidLock catches ValueError and treats as stale lock. The BB publisher uses `fcntl.flock` which is atomic (no race). The canonical publisher's PID file approach has a TOCTOU window between read and write.
**Risk:** LOW (launchd stagger + different lock files per niche)
**Fix status:** OPEN (canonical publisher should use fcntl like BB publisher)

### [PUB-06] No SKIPPED Record Written for Missing Credentials
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/publishing/publish_all_platforms.py:400-405`
**Trigger:** A niche has no credentials for a platform (e.g., sports has no Twitter tokens). `_resolve_client_kwargs` returns None.
**Symptom:** The canonical publisher returns a PublishResult(success=False) which IS recorded to Publishing_Analytics (line 436-445 records both SUCCESS and FAILED). So this is actually handled. But the BB publisher at `Content Scraper/execution/publish_all_platforms.py:1516` returns "SKIPPED_NO_CREDENTIALS" as a string post_id, which is misleading.
**Current handling:** Canonical publisher: properly records FAILED. BB publisher: returns a fake success string.
**Risk:** LOW
**Fix status:** MITIGATED (canonical publisher is correct; BB publisher has legacy quirk)

### [PUB-07] DailyCapEnforcer Fails Open (Potential Over-Publish)
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/publishing/daily_cap.py:155-189`
**Trigger:** SharePoint query to load today's counts fails. `_load_today_counts` returns empty dict `{}`. All platforms show 0 publishes for today.
**Symptom:** If two niches' publishers run quickly and both fail to load counts, each might publish despite the cap. The cap is 1/platform/day, so this could result in 2 posts per niche per day.
**Current handling:** Explicitly fail-open: "Starting from 0 (fail-open)." This is a deliberate design choice documented in the docstring.
**Risk:** MEDIUM (SharePoint failures are rare, but could cause 2x daily posts)
**Fix status:** OPEN (by design, but should add a disk-backed fallback counter)

### [PUB-08] DailyCapEnforcer Only Counts SUCCESS, Ignores In-Flight
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/publishing/daily_cap.py:161-183`
**Trigger:** Two pipeline runs for the same niche overlap. Both load counts before either finishes publishing. Both see 0 publishes today.
**Symptom:** Both runs publish, resulting in 2 posts instead of 1.
**Current handling:** The in-memory `_session_counts` dict tracks within a single process, but separate processes (separate niches via launchd) have no shared state. Per-niche PID lock prevents same-niche overlap.
**Risk:** LOW (PID lock prevents same-niche overlap; different niches have independent caps)
**Fix status:** MITIGATED (PID lock is the real guard for same-niche)

---

## 2. PIPELINE FAILURE MODES

### [PIPE-01] All Pipeline Stages Are Non-Fatal (No Fatal Error Path)
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/pipeline/stage_runner.py:120-139`
**Trigger:** Every stage exception is caught, logged, and recorded with `fatal=False`. The pipeline never aborts on a stage failure.
**Symptom:** If `PushToBacklog` fails (e.g., SharePoint is down), the pipeline continues to subsequent stages (render, validate, etc.) that depend on backlog data. Downstream stages operate on stale/missing data but don't crash. Run completes "successfully" with errors in the log.
**Current handling:** `pipeline_ctx.record_error(stage_name, e, fatal=False)` is hardcoded. There is no mechanism for a stage to declare itself as fatal.
**Risk:** HIGH (critical stages like PushToBacklog should be fatal)
**Fix status:** OPEN

### [PIPE-02] Parallel Stage Context Mutation Race
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/pipeline/stage_runner.py:310-363`
**Trigger:** Multiple stages in the same `parallel_group` mutate the shared `context` dict concurrently. If two stages write to the same key (e.g., both append to `stories`), data can be lost.
**Symptom:** Lost story entries, corrupted blueprints list, or KeyError during downstream stages.
**Current handling:** Comment at line 315: "they must write to non-overlapping keys to avoid races." This is documentation only, with no enforcement.
**Risk:** MEDIUM (depends on niche config declaring parallel groups correctly)
**Fix status:** OPEN (no runtime guard)

### [PIPE-03] Video Dedup Check in PushToBacklog Uses Full Table Scan
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py:166-178`
**Trigger:** PushToBacklog dedup fetches ALL blueprints (`client.blueprints.all(max_records=200)`) and filters client-side by video_id. As the blueprint table grows beyond 200 records, dedup silently misses older entries.
**Symptom:** Same video_id creates duplicate blueprints after the backlog exceeds 200 records. The `max_records=200` cap means the dedup scan only covers the 200 most recent records.
**Current handling:** Client-side filter with a hard cap of 200.
**Risk:** HIGH (table is growing daily; 200 record cap already exceeded for some niches)
**Fix status:** OPEN

### [PIPE-04] PushToBacklog Story Error Silently Skips Blueprint
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py:143-146`
**Trigger:** Story upsert fails (SharePoint error). The `continue` statement skips blueprint creation for that story entirely.
**Symptom:** Story is lost for this run. Content was written by LLM (expensive), rendered (expensive), but never reaches the backlog. No retry mechanism.
**Current handling:** Logged as warning, appended to errors list, but pipeline continues. The error count is reported in run_stats but no alert is triggered.
**Risk:** MEDIUM
**Fix status:** MITIGATED (error is logged; manual intervention needed)

### [PIPE-05] Blueprint Dedup Check Allows Overwrite of DRAFTED/SCORED
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py:199-209`
**Trigger:** Existing blueprint in DRAFTED or SCORED status is found by candidate_id. Only `niche_id` is updated; content/hooks/captions from the new run are NOT written.
**Symptom:** Stale content persists. If a hook was improved in the new run, the old hook stays in SharePoint.
**Current handling:** Only updates niche_id field. Does not update content fields.
**Risk:** LOW (most blueprints reach VISUAL_READY quickly)
**Fix status:** OPEN

### [PIPE-06] VideoGate Checks File Size But Not File Integrity
**Severity:** LOW
**Location:** `genlab-core/src/genlab_core/pipeline/stages/video_gate.py:39-48`
**Trigger:** A corrupted video file (>100KB but broken MP4) passes the size check. Downstream render fails.
**Symptom:** Story passes VideoGate, LLM writes content for it, but render fails. Wasted LLM tokens.
**Current handling:** Only checks `st_size >= 100KB`.
**Risk:** LOW (corrupted downloads are rare; yt-dlp validates during download)
**Fix status:** OPEN

### [PIPE-07] ValidateVideos VMAF Fail-Open When No Master
**Severity:** LOW
**Location:** `genlab-core/src/genlab_core/pipeline/stages/validate_videos.py:169-172`
**Trigger:** No `master_path` in story media dict. VMAF check is skipped entirely (returns "pass").
**Symptom:** A badly re-encoded video passes validation without quality comparison.
**Current handling:** Explicitly fail-open: `return "pass"` with a debug log.
**Risk:** LOW (most renders have a master; the spec checks still run)
**Fix status:** MITIGATED (by design)

---

## 3. DATA INTEGRITY FAILURE MODES

### [DATA-01] Same Video Can Create Multiple Blueprints (200-Record Cap)
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py:166-178`
**Trigger:** Blueprint table has >200 records for a niche. Older entries with the same video_id are not found by the dedup scan (capped at 200). A new pipeline run creates a duplicate blueprint for an already-blueprinted video.
**Symptom:** Duplicate content appears in the publishing queue. Could result in posting the same video clip twice.
**Current handling:** `max_records=200` is a pagination limit, not a dedup guarantee.
**Risk:** HIGH (this is actively getting worse as the table grows)
**Fix status:** OPEN

### [DATA-02] Published Blueprint Can Be Overwritten Back to DRAFTED
**Severity:** CRITICAL (mitigated)
**Location:** `genlab-core/src/genlab_core/http/backlog_client.py:80-100`
**Trigger:** ScheduleGuardedProxy blocks status demotions on scheduled posts. But a non-scheduled PUBLISHED blueprint could theoretically be overwritten if the guard is bypassed.
**Symptom:** A published post reverts to DRAFTED, potentially publishing again.
**Current handling:** ScheduleGuardedProxy checks STATUS_ORDER and blocks demotions. PushToBacklog explicitly skips PUBLISHED/PUBLISHING/VISUAL_READY blueprints (line 202).
**Risk:** LOW (double guard in place)
**Fix status:** MITIGATED

### [DATA-03] Two Niches Can Publish the Same Video
**Severity:** MEDIUM
**Location:** PushToBacklog dedup is per-niche (line 169: `bp.get("niche_id") == niche_id`)
**Trigger:** The same YouTube video_id is trending in both gaming and sports (or appears in overlapping search results). Each niche creates its own blueprint because dedup is scoped by niche.
**Symptom:** Same underlying video published to CriticalRush AND ClutchWire (under different brands). Not a bug per se (different channels, different audiences) but potentially a brand differentiation issue.
**Current handling:** By design -- niches are independent.
**Risk:** LOW
**Fix status:** N/A (by design)

### [DATA-04] Daily Cap Can Be Bypassed Via Process Restart
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/publishing/daily_cap.py:152-193`
**Trigger:** A manual re-run of the publisher after a successful publish. If the Publishing_Analytics record was written but then SharePoint is briefly unavailable, the new process loads 0 counts and publishes again.
**Symptom:** 2+ posts per channel per day.
**Current handling:** Relies on SharePoint as source of truth. Fail-open design.
**Risk:** LOW (manual re-runs are rare; launchd runs once per schedule slot)
**Fix status:** MITIGATED (operational discipline is the guard)

### [DATA-05] Blueprint Can Publish Without Rendered Video (Gatekeeper Gap)
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/platforms/gatekeeper.py:96-104`
**Trigger:** `_media_ready_gate` only checks that `visual_paths` is non-empty (has at least one entry). It does NOT check that the referenced file actually exists on disk. If visual_paths contains a path to a deleted file, the gate passes.
**Symptom:** Publish proceeds, CDN upload fails, platform publish fails. Blueprint reverts to VISUAL_READY.
**Current handling:** The actual publish functions (Instagram, YouTube) check file existence before upload. So the failure is caught later, but wastes a publish attempt and leaves the blueprint in a retry loop.
**Risk:** MEDIUM
**Fix status:** MITIGATED (caught at publish time; wastes one attempt)

---

## 4. CREDENTIAL FAILURE MODES

### [CRED-01] Token With Embedded Quotes Silently Corrupts Requests
**Severity:** CRITICAL (fixed)
**Location:** `genlab-core/src/genlab_core/publishing/niche_credentials.py:45`
**Trigger:** `.env` file has `META_ACCESS_TOKEN="EAA..."` (with double quotes around the value). Python `os.getenv` includes the quotes. API calls fail with "Invalid OAuth access token" but the error message does not indicate quote corruption.
**Symptom:** All Meta API calls fail. Logs show "invalid token" but the token looks correct in `.env`.
**Current handling:** `.strip()` is called (line 45) but does NOT strip quotes. `dotenv.load_dotenv(override=True)` usually handles this, but some shell sourcing doesn't.
**Risk:** MEDIUM (happened in production; was a known bug)
**Fix status:** PARTIALLY FIXED (dotenv handles most cases, but manual `.env` edits can reintroduce)

### [CRED-02] YouTube Refresh Token Expiration (No Auto-Recovery)
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/publishing/niche_credentials.py:83-89`
**Trigger:** YouTube OAuth refresh token expires (after 7 days of inactivity, or revocation). `resolve_youtube_credentials` returns the stale token, YouTube API returns 400 at publish time.
**Symptom:** All YouTube publishes fail silently (counted as platform failure, recorded as FAILED).
**Current handling:** Token health checker (`check_token_health.py`) runs daily and warns about expired tokens, but does not auto-refresh YouTube tokens (requires user interaction).
**Risk:** HIGH (YouTube tokens expire; manual intervention required)
**Fix status:** MITIGATED (health check warns; requires human action)

### [CRED-03] Per-Niche Token Missing Returns Empty String, Not Error
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/publishing/niche_credentials.py:44-52`
**Trigger:** A niche has a registered prefix (e.g., CLUTCHWIRE) but the env var `CLUTCHWIRE_META_ACCESS_TOKEN` is not set. `resolve_niche_env` returns `""`.
**Symptom:** The publisher skips the platform silently (returns None from `_resolve_client_kwargs`). No SKIPPED record is written in the canonical publisher; it's recorded as FAILED with "No credentials".
**Current handling:** Cross-channel guard prevents fallback to BB's global tokens. But the only indication is a debug-level log.
**Risk:** LOW (by design; niche tokens pending provisioning)
**Fix status:** MITIGATED (debug log exists; operational runbook documents pending tokens)

### [CRED-04] SharePoint OAuth Client Secret Rotation
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/http/backlog_client.py` (uses AZURE_CLIENT_SECRET)
**Trigger:** Azure AD app registration client secret expires (typically 6mo or 1yr). All SharePoint operations fail simultaneously.
**Symptom:** Every pipeline stage that touches SharePoint fails. PushToBacklog fails. Publisher fails to query VISUAL_READY blueprints. Dashboard shows no data.
**Current handling:** No expiry monitoring for Azure secrets. Token health checker only monitors platform tokens (Meta, YouTube, X, Threads), NOT SharePoint/Azure credentials.
**Risk:** HIGH (when the secret expires, everything breaks at once)
**Fix status:** OPEN

---

## 5. SILENT FAILURE MODES

### [SILENT-01] Engagement Poller Returns Empty on Credential Failure
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/engagement/poller.py:42-49`
**Trigger:** YouTube API key or OAuth credentials are not set. Poller returns `[]`.
**Symptom:** Engagement engine never processes any comments. No error visible in dashboard. Logs show a warning that's easily buried.
**Current handling:** Returns `[]` silently.
**Risk:** MEDIUM (engagement processing is a monetisation driver)
**Fix status:** OPEN

### [SILENT-02] FeedbackCollector Returns Empty Dict on API Failure
**Severity:** MEDIUM
**Location:** `CriticalRush/niches/gaming/learning/feedback_collector.py:306-311` (and 12 similar locations in same file)
**Trigger:** Any platform API call fails during metric collection. Function returns `{}`.
**Symptom:** Bandit learning system receives no metrics for a post. Thompson Sampling priors don't update. Learning loop stalls without any visible alert.
**Current handling:** `return {}` with a warning log.
**Risk:** MEDIUM (degrades learning quality over time)
**Fix status:** OPEN

### [SILENT-03] Intelligence Hub Cache Miss Returns None Silently
**Severity:** LOW
**Location:** `scripts/intelligence_hub.py:59-62`
**Trigger:** Cache file is corrupted or missing. `json.JSONDecodeError` is caught, returns None.
**Symptom:** Intelligence hub functions operate without schedule data. Recommendations may be wrong.
**Current handling:** `except (json.JSONDecodeError, OSError): pass` then `return None`.
**Risk:** LOW (intelligence scripts are advisory, not pipeline-critical)
**Fix status:** OPEN

### [SILENT-04] Dashboard Schedule API Returns None on SharePoint Error
**Severity:** MEDIUM
**Location:** `dashboard/server/api/schedule.py:295-296`
**Trigger:** SharePoint query fails during slot collision check. `except Exception: return None`.
**Symptom:** Dashboard shows "slot available" for a time that actually has a conflict. User approves a post that creates a slot collision.
**Current handling:** Bare `except Exception: return None` with no logging.
**Risk:** MEDIUM (could cause double-booking of publish slots)
**Fix status:** OPEN

### [SILENT-05] Publishing Analytics Record Write Fails Silently
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/publishing/analytics_recorder.py:58-71`
**Trigger:** Publishing_Analytics proxy is None or SharePoint write fails.
**Symptom:** DailyCapEnforcer has no record of the publish. Next run's cap check doesn't count this publish. Could lead to over-publishing.
**Current handling:** Logs a warning. Never retries.
**Risk:** MEDIUM (cap enforcement depends on analytics records)
**Fix status:** OPEN

### [SILENT-06] Gatekeeper Schedule Gate Swallows Parse Errors
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/platforms/gatekeeper.py:71-82`
**Trigger:** `scheduled_for` field contains a malformed date string. `datetime.fromisoformat` raises ValueError, which is caught with `pass`. Gate returns ALLOWED.
**Symptom:** A blueprint with a garbage scheduled_for value passes the schedule gate. It may publish at the wrong time or immediately.
**Current handling:** `except (ValueError, TypeError): pass` -- falls through to "allowed".
**Risk:** LOW (scheduled_for is set by pipeline code, not user input)
**Fix status:** OPEN

---

## 6. INFRASTRUCTURE FAILURE MODES

### [INFRA-01] SharePoint Circuit Breaker Blocks Entire Pipeline
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/http/circuit_breaker.py:273-278`
**Trigger:** 5 SharePoint failures in 120 seconds. SHAREPOINT_CB trips open. All backlog operations fail with `CircuitOpenError` for the next 60 seconds.
**Symptom:** Pipeline stages that use BacklogClient fail immediately. PushToBacklog, metric collection, feedback collection all fail. Pipeline continues (non-fatal) but produces no output.
**Current handling:** Circuit breaker with 60s recovery. But during open state, all stages that touch SharePoint fail instantly with no retry.
**Risk:** MEDIUM (SharePoint outages are rare but happen)
**Fix status:** MITIGATED (circuit breaker prevents cascading failures, but 60s downtime is accepted)

### [INFRA-02] Redis Down Kills Engagement Queue (Dramatiq)
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/engagement/tasks.py` (Dramatiq actors)
**Trigger:** Redis server is unreachable. Dramatiq cannot enqueue or dequeue engagement tasks.
**Symptom:** Comment replies stop processing. Engagement rate drops to zero. No alert mechanism.
**Current handling:** Dramatiq will retry connections, but there's no health check endpoint for Redis in the dashboard.
**Risk:** MEDIUM (Redis is a single point of failure for engagement)
**Fix status:** OPEN

### [INFRA-03] Disk Space Exhaustion During Video Download
**Severity:** HIGH
**Location:** All video download stages (DownloadTopVideos, clip_video.py)
**Trigger:** `.tmp/media/videos/` accumulates 19GB+ of downloaded videos. yt-dlp download fills remaining disk.
**Symptom:** yt-dlp fails with OSError. Render stages have no clips. All blueprints stay at DRAFTED.
**Current handling:** QuotaManager in `genlab-core/media/quota_manager.py` does score-weighted eviction, but requires periodic invocation. No automatic disk pressure check at pipeline start.
**Risk:** HIGH (happened in production; required manual cleanup)
**Fix status:** MITIGATED (manual cleanup pattern documented; no automatic pre-flight check)

### [INFRA-04] Dashboard Server Crash (Gunicorn + Eventlet)
**Severity:** MEDIUM
**Location:** `dashboard/server/review_server.py`
**Trigger:** Unhandled exception in a Flask route. Eventlet worker dies.
**Symptom:** Dashboard becomes unresponsive. Approval queue is inaccessible. Posts cannot be approved for publishing.
**Current handling:** Gunicorn will restart the worker, but if the crash is deterministic (e.g., bad data in SharePoint), it will crash-loop.
**Risk:** MEDIUM (Flask apps are generally stable; crash-loop requires bad data)
**Fix status:** MITIGATED (gunicorn worker restart)

### [INFRA-05] Prefect Server Down (Pipeline Orchestration)
**Severity:** LOW
**Location:** Pipeline runner does NOT require Prefect; it runs directly via Python.
**Trigger:** N/A -- Prefect is optional (used only for metric_collector and config_update flows).
**Symptom:** Metric collection flows stop. Config auto-update stops. Core pipeline continues.
**Current handling:** Prefect flows are standalone; pipeline runner uses direct execution.
**Risk:** LOW (Prefect is not in the critical path)
**Fix status:** N/A

---

## 7. TIMING / RACE CONDITION FAILURE MODES

### [RACE-01] Two Publisher Plists for Same Niche Fire Simultaneously
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/publishing/publish_all_platforms.py:98-122` (PidLock)
**Trigger:** Launchd fires both the BB publisher (`com.genlab.daily-intel`) and the canonical publisher for the same niche. Or a manual `--force` run while launchd publisher is active.
**Symptom:** PidLock (canonical publisher) or fcntl lock (BB publisher) prevents double-run within the same lock namespace. But BB and canonical publishers use DIFFERENT lock files (`Content Scraper/.tmp/publisher-*.lock` vs `/tmp/publisher-*.lock`).
**Current handling:** BB uses `Content Scraper/.tmp/publisher-{niche}.lock`. Canonical uses `/tmp/publisher-{niche}.lock`. **These are different files, so both publishers can run simultaneously for the same niche.**
**Risk:** HIGH (if both publishers are configured for the same niche)
**Fix status:** OPEN (ensure only one publisher is active per niche)

### [RACE-02] Pipeline Writes Blueprint While Publisher Reads It
**Severity:** LOW
**Location:** Pipeline stages write to SharePoint; publisher reads from SharePoint.
**Trigger:** Pipeline sets a blueprint to VISUAL_READY at the exact moment the publisher queries VISUAL_READY blueprints.
**Symptom:** Publisher either picks up or misses the newly-ready blueprint. If missed, it's published on the next run. No data corruption -- SharePoint's atomicity guarantees per-record consistency.
**Current handling:** SharePoint provides record-level atomicity.
**Risk:** LOW (worst case is a one-cycle delay)
**Fix status:** N/A

### [RACE-03] Cleanup Cron Deletes Video While Publisher Is Uploading
**Severity:** MEDIUM
**Location:** `.tmp/media/videos/` cleanup (documented pattern: `find .tmp/media/videos/ -mtime +1 -delete`)
**Trigger:** Cleanup cron runs during the publish window. Publisher has already read visual_paths from SharePoint but hasn't uploaded to CDN yet.
**Symptom:** CDN upload fails with FileNotFoundError. Platform publish fails. Blueprint reverts to VISUAL_READY.
**Current handling:** `disk_quota.py._is_published()` protects directories with rendered MP4s, but `videos/` (downloaded source clips) is not protected.
**Risk:** MEDIUM (timing window is real)
**Fix status:** OPEN

### [RACE-04] Two Engagement Pollers Process Same Comment
**Severity:** LOW
**Location:** `genlab-core/src/genlab_core/engagement/comment_processor.py:148-150`
**Trigger:** Two poller instances run concurrently and find the same comment. Both attempt to reply.
**Symptom:** The JSONL idempotency file (`replied_comments.jsonl`) is protected by `fcntl.flock(f, fcntl.LOCK_EX)`. The second poller will find the comment already in the replied-set and skip it.
**Current handling:** Properly guarded with file-level lock + idempotency check.
**Risk:** LOW
**Fix status:** FIXED

### [RACE-05] Dashboard Approval While Publisher Is Running
**Severity:** LOW
**Location:** Dashboard schedule API, publisher queries
**Trigger:** User approves a blueprint in the dashboard while the publisher is mid-query. Publisher's VISUAL_READY query was already made, so the newly-approved blueprint is not included.
**Symptom:** Blueprint publishes on the next run, not this one. No data loss.
**Current handling:** Publisher queries are point-in-time. Next run catches it.
**Risk:** LOW
**Fix status:** N/A

---

## 8. CONFIGURATION FAILURE MODES

### [CONF-01] Missing niche.yaml Crashes Pipeline Runner
**Severity:** HIGH
**Location:** `genlab-core/src/genlab_core/pipeline/pipeline_runner.py:113`
**Trigger:** `load_niche_config(niche_id, niche_root)` fails because niche.yaml is missing or malformed.
**Symptom:** Pipeline run fails with `NicheConfigError` before any stage executes.
**Current handling:** Raises `NicheConfigError` with descriptive message.
**Risk:** LOW (configs are checked into git; only breaks on new niche setup)
**Fix status:** FIXED (raises with helpful message)

### [CONF-02] Empty pipeline.stages List Raises NicheConfigError
**Severity:** LOW
**Location:** `genlab-core/src/genlab_core/pipeline/pipeline_runner.py:260-263`
**Trigger:** niche.yaml has an empty `pipeline.stages: []`.
**Symptom:** Clear error: "pipeline.stages for '{niche_id}' is empty."
**Current handling:** Properly validated.
**Risk:** LOW
**Fix status:** FIXED

### [CONF-03] platform_caps.yaml Missing Falls Back to Default Cap of 1
**Severity:** LOW
**Location:** `genlab-core/src/genlab_core/publishing/daily_cap.py:41-48`
**Trigger:** Config file not found at expected path.
**Symptom:** Default cap of 1/platform/day is applied. Safe but inflexible.
**Current handling:** Warning logged, defaults applied.
**Risk:** LOW
**Fix status:** MITIGATED

---

## 9. TWO-PUBLISHER ARCHITECTURE RISK

### [ARCH-01] BB and Canonical Publishers Coexist With Different Logic
**Severity:** HIGH
**Location:**
- `Content Scraper/execution/publish_all_platforms.py` (~1700 LOC, legacy)
- `genlab-core/src/genlab_core/publishing/publish_all_platforms.py` (~530 LOC, canonical)
**Trigger:** Both publishers exist. BB publisher is used for ai_creators niche. Canonical publisher is used for other niches. The logic differs in material ways:
  1. BB publisher has TEST_MODE/SKIP_APPROVAL_GATE; canonical does not.
  2. BB publisher does per-platform video selection (landscape vs portrait); canonical does not.
  3. BB publisher has schedule cascade logic; canonical does not.
  4. BB publisher uses fcntl locking; canonical uses PID file.
  5. BB publisher creates PendingFeedbackTask after publish; canonical does not.
**Symptom:** Behavior differs between niches. Bugs fixed in one publisher don't propagate to the other.
**Current handling:** This is known architecture debt documented in CLAUDE.md.
**Risk:** HIGH (long-term maintenance burden; divergent bug fixes)
**Fix status:** OPEN (planned migration to canonical publisher)

---

## 10. ENGAGEMENT ENGINE FAILURE MODES

### [ENG-01] Toxicity Gate Detoxify Import Fails (Fail-Open on Inbound)
**Severity:** MEDIUM
**Location:** `genlab-core/src/genlab_core/engagement/toxicity_gate.py`
**Trigger:** Detoxify library not installed or CUDA error. Import fails.
**Symptom:** Inbound toxicity check fails open (all comments pass through). Outbound check fails closed (no replies sent).
**Current handling:** Documented in the module: "fail-open inbound, fail-closed outbound."
**Risk:** LOW (fail-closed outbound prevents posting toxic replies)
**Fix status:** MITIGATED (by design)

### [ENG-02] Engagement Rate Limiter Is Process-Local
**Severity:** LOW
**Location:** `genlab-core/src/genlab_core/engagement/comment_processor.py:89-97`
**Trigger:** Two engagement worker processes run simultaneously. Each has its own TokenBucket instance.
**Symptom:** Combined reply rate is 2x the configured cap. Platform API rate limits may trigger.
**Current handling:** RATE_CAPS dict is per-process. No shared state.
**Risk:** LOW (single poller instance per launchd plist)
**Fix status:** MITIGATED (operational: only one poller runs)

---

## PRIORITY SUMMARY

### CRITICAL (Fix immediately)
| ID | Name | Component |
|---|---|---|
| PUB-01 | Ghost Publish (SP update fails after publish) | Publisher |
| ARCH-01 | Two-publisher architecture divergence | Architecture |

### HIGH (Fix this sprint)
| ID | Name | Component |
|---|---|---|
| PUB-07 | DailyCapEnforcer fails open | Publisher |
| PIPE-01 | All stages non-fatal (no fatal path) | Pipeline |
| DATA-01 | Video dedup 200-record cap | PushToBacklog |
| CRED-04 | Azure client secret expiry not monitored | Credentials |
| INFRA-03 | Disk space exhaustion | Infrastructure |
| RACE-01 | BB and canonical publishers use different lock files | Publisher |

### MEDIUM (Fix next sprint)
| ID | Name | Component |
|---|---|---|
| PUB-03 | CDN upload failure (no retry) | Publisher |
| PUB-04 | File deleted between selection and upload | Publisher |
| PUB-05 | PID lock TOCTOU race | Publisher |
| PUB-08 | DailyCapEnforcer cross-process blind spot | Publisher |
| PIPE-02 | Parallel stage context mutation | Pipeline |
| PIPE-04 | Story error skips blueprint | PushToBacklog |
| DATA-05 | Gatekeeper doesn't check file existence | Gatekeeper |
| CRED-02 | YouTube refresh token expiry | Credentials |
| SILENT-01 | Engagement poller silent on missing creds | Engagement |
| SILENT-02 | FeedbackCollector returns {} on API failure | Learning |
| SILENT-04 | Schedule API returns None on SP error | Dashboard |
| SILENT-05 | Analytics record write failure | Publishing |
| SILENT-06 | Gatekeeper schedule gate swallows parse errors | Gatekeeper |
| INFRA-01 | SharePoint circuit breaker blocks pipeline | Infrastructure |
| INFRA-02 | Redis SPOF for engagement | Infrastructure |
| RACE-03 | Cleanup cron vs publisher timing | Infrastructure |

### LOW (Track, fix opportunistically)
| ID | Name | Component |
|---|---|---|
| PUB-02 | Stuck PUBLISHING status | Publisher |
| PUB-06 | BB publisher fake SKIPPED_NO_CREDENTIALS | Publisher |
| PIPE-05 | Blueprint dedup doesn't update content | PushToBacklog |
| PIPE-06 | VideoGate doesn't check file integrity | Pipeline |
| PIPE-07 | VMAF fail-open when no master | Pipeline |
| DATA-03 | Cross-niche same video | Data integrity |
| DATA-04 | Daily cap bypass via restart | Data integrity |
| CRED-01 | Quoted tokens (partially fixed) | Credentials |
| CRED-03 | Missing niche token returns "" | Credentials |
| SILENT-03 | Intelligence hub cache miss | Scripts |
| INFRA-04 | Dashboard crash-loop | Dashboard |
| ENG-01 | Toxicity gate fail-open inbound | Engagement |
| ENG-02 | Rate limiter process-local | Engagement |
| RACE-02 | Pipeline vs publisher read race | Infrastructure |
| RACE-04 | Duplicate engagement poller | Engagement |
| RACE-05 | Dashboard approval mid-publisher | Dashboard |
| CONF-01 | Missing niche.yaml | Configuration |
| CONF-02 | Empty stages list | Configuration |
| CONF-03 | Missing platform_caps.yaml | Configuration |
