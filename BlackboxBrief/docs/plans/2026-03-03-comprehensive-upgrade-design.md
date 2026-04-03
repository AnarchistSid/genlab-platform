# Comprehensive Codebase Upgrade — Design Doc

**Date:** 2026-03-03
**Status:** Approved
**Scope:** 46 fixes and upgrades across 4 phases
**Previous audit:** 25-fix hardening plan (completed same day, commits dc01fa1..d226af5)

---

## Problem Statement

A deep 6-agent parallel audit of the entire codebase (71,465 LOC, 80+ files) found
95 raw issues. After deduplication, verification, and false-positive elimination,
46 actionable items remain. The most critical cluster: 3 related bugs in the
publishing orchestrator that can cause **duplicate posts** after daemon crashes or
timeouts. The rendering pipeline has broken multi-clip assembly and timeout issues
for long videos. Security gaps exist in prompt injection defense, and configuration
drift has left QC gates and SLO tracking non-functional.

## Audit Methodology

Six parallel code-explorer agents analyzed:
1. **Publishing pipeline** — publish_all_platforms, per-platform publishers, API clients
2. **Rendering pipeline** — render_visuals, render_text_overlays, assemble_video_reel, validate_videos
3. **Content generation** — generate_content, write_post_content, adapt_for_platforms, generate_hooks
4. **Data pipeline** — fetch_ai_creators, dedupe_rank_items, compose_blueprints, push_to_backlog
5. **Test coverage** — all test files vs execution files
6. **Architecture** — config system, observability, review server, runbooks, security

Findings were verified by a targeted follow-up agent that confirmed or eliminated
each item with exact line numbers.

---

## Phase 1: Critical — Duplicate Posts & Silent Failures (8 fixes)

These can cause real-world harm right now.

### P0.1 — PUBLISHING state causes duplicate posts after crash

**Problem:** `publish_all_platforms.py:1124` retry filter only skips `"PUBLISHED"`.
After a daemon crash, `"PUBLISHING"` status is not skipped, so the platform is
re-published on the next run.

**Fix:** Add `"PUBLISHING"` to the skip set. Add staleness check: if a platform has
been in PUBLISHING for >30 minutes, treat as failed (not skip, not re-publish).

### P0.2 — 120s future timeout shorter than CDN/video uploads

**Problem:** `publish_all_platforms.py:1202` uses `future.result(timeout=120)`.
Facebook video upload (300s), litterbox CDN (600s), and YouTube resumable uploads
can all exceed this. The thread keeps running in the background after timeout,
potentially completing the upload — then the next daemon run re-publishes.

**Fix:** Raise timeout to `700` seconds (covers worst-case litterbox + API call).

### P0.3 — all_or_nothing missing SKIPPED_* codes

**Problem:** `publish_all_platforms.py:1326-1331` only whitelists `SKIPPED_DAILY_LIMIT`.
Other skip codes (`SKIPPED_NO_CREDENTIALS`, `SKIPPED_PAYLOAD_*`) cause the blueprint
to stay in VISUAL_READY and get re-published on already-succeeded platforms.

**Fix:** Whitelist all `status.startswith("SKIPPED")` codes.

### P0.4 — YouTube HttpError crashes thread silently

**Problem:** `PUBLISH_OP_ERRORS` (line 69-78) does not include
`googleapiclient.errors.HttpError`. YouTube API 4xx/5xx errors propagate uncaught
through 14+ catch sites. Also `publish_youtube.py:314-343` catches only
`RequestException` and `KeyError`.

**Fix:** Add `HttpError` to `PUBLISH_OP_ERRORS`. Add explicit `HttpError` catch in
YouTube upload functions.

### P0.5 — Twitter video upload broken (no chunked upload)

**Problem:** `twitter_client.py:70` uses `api_v1.media_upload(file_path)` without
`chunked=True`. Twitter's simple upload endpoint rejects video files >5MB. All MP4
tweets silently become text-only.

**Fix:** Pass `chunked=True` when file extension is `.mp4`, `.mov`, or `.gif`.

### P0.6 — Fixed 120s FFmpeg timeout kills long-video overlays

**Problem:** `render_text_overlays.py:504,1691` uses `FFMPEG_TIMEOUT = 120` on the
legacy drawtext path. A 15-minute video at CRF 17 slow preset takes 8-15 minutes.

**Fix:** Apply dynamic timeout: `max(FFMPEG_TIMEOUT, int(video_duration * 4))` in
both `_render_drawtext()` and `_render_pillow()`.

### P0.7 — Multi-clip total_duration=0 produces zero-duration scenes

**Problem:** `assemble_video_reel.py:500-517` `_allocate_scene_durations()` computes
`scale = 0 / preset_sum = 0.0` when `total_duration=0`. All scenes get 0-second
allocation, fail the `< 2.0` guard, and the multi-clip path never works.

**Fix:** When `total_duration <= 0`, return `[0.0] * n_clips` (each 0 = "use full
clip duration"). Update `_assemble_multi_clip` and `_assemble_single_clip` to treat
0 as "use full clip" rather than 0 seconds.

### P0.8 — Two blueprints can both post to Twitter

**Problem:** `publish_all_platforms.py:835-836` sets `tw_rate_limited` event only on
429 error. Two blueprints can both pass the `is_set()` check before either posts.
On Free tier (1/day), this causes a 24-hour rate limit.

**Fix:** Set `tw_rate_limited.set()` immediately after first successful Twitter post,
not just on 429.

---

## Phase 2: Publishing Correctness & Security (14 fixes)

### P1.1 — Partial thread marked as success
Detect `len(posted_ids) < len(thread_tweets)` → return None.

### P1.2 — Carousel timeout falls through to publish
Change `break` to `return None` on carousel container timeout (match reel behavior).

### P1.3 — PUBLISHED poll falls through to re-publish
Return early when container status is already PUBLISHED during polling.

### P1.4 — CDN upload inside Instagram retry loop
Move `_ensure_public_url()` before the retry loop. Retry only the API call.

### P1.5 — Missing `-r 30` in FINAL_VIDEO_PARAMS
Add `"-r", "30"` to the shared constant. All consumers inherit consistent 30fps.

### P1.6 — Inconsistent audio bitrate (192k vs 256k)
Replace hardcoded `"192k"` in `render_text_overlays.py` and `assemble_video_reel.py`
with `FINAL_AUDIO_PARAMS` from `ffmpeg_utils.py`.

### P1.7 — story_url not checked for injection
Add `story_url` to the injection-check field list in `write_post_content.py`.

### P1.8 — Backlog content not re-sanitized before adaptation
Apply `sanitize_text()` + `check_for_injection()` to hook/caption loaded from
backlog before interpolating into adaptation prompts.

### P1.9 — Facebook missing from to_adapt filter
Add `has_fb = bool(fields.get("facebook_content", ""))` to the filter condition.

### P1.10 — SLO duration mismatch (600s hardcoded vs 2400s config)
Read `duration_p95` from `error_budgets.yaml` in `write_run_report.py` instead of
hardcoded 600. Apply to both `collect_speed_metrics()` and the SLO violation check.

### P1.11 — risk_rules.yaml missing high-risk keywords
Add: `deepfake`, `synthetic media`, `election`, `disinformation`, `terrorism`,
`bioweapon`, `weapon`, `violence` to appropriate risk tiers.

### P1.12 — Twitter credential failure inflates error budgets
Set `platform_results["twitter"] = "SKIPPED_NO_CREDENTIALS"` when `tw_credentials_ok`
is False, matching the Facebook pattern.

### P1.13 — Thread tweet media not capped at 4
Add `media_ids = media_ids[:4]` before `post_tweet()` call in `post_thread()`.

### P1.14 — Intermediate clip timeout too short
Apply `max(120, int(actual_dur * 4))` to individual clip preprocessing in
`assemble_video_reel.py`.

---

## Phase 3: Rendering Quality & Data Pipeline (12 fixes)

### P2.1 — feedparser no timeout
Pass `timeout=30` to `feedparser.parse()`.

### P2.2 — normalize_url lowercases path
Only lowercase `scheme` and `netloc`; preserve `path`, `query`, `fragment` case.

### P2.3 — QC Gate B has no reel constraints
Add `constraints:` block to reel templates in `templates.yaml`:
`max_seconds: 900`, `max_words_per_beat_title: 15`, `beat_cadence_seconds: 5`.

### P2.4 — Duplicate _escape_drawtext functions
Move the class method version to `ffmpeg_utils.py` as a module-level function.
Both call sites import from the single source.

### P2.5 — Font path not escaped for FFmpeg
Use `shlex.quote()` or the new unified escape function for the font path.

### P2.6 — max_tokens 2048 may truncate carousel JSON
Raise to 4096 for content generation LLM calls.

### P2.7 — No disk space pre-flight
Add `shutil.disk_usage()` check before render pipeline. Abort with clear message
if <2GB free.

### P2.8 — Temp file cleanup (_tw.mp4, _cdn.mp4)
Add `finally:` cleanup blocks in `publish_twitter.py` and `publish_facebook.py`.

### P2.9 — Reel-ratio enforcement missing cross-story case
Implement the cross-story reel addition path in `compose_blueprints.py:978-1014`.

### P2.10 — Column map cache no TTL
Add 1-hour TTL check in `_load_column_map()` using `time.monotonic()`.

### P2.11 — push_to_backlog fetches entire tables
Add OData `$top` + `$filter` for incremental fetch (only records updated in
last 7 days for stories, last 24h for blueprints).

### P2.12 — Facebook prompt 500-char limit contradicts config
Align prompt to `publishing.yaml` value: change prompt to "Max 2000 characters"
(reasonable middle ground).

---

## Phase 4: Architecture & Polish (12 upgrades)

### P3.1 — Remove dead assemble_reel.py
Move to `execution/archive/` or delete entirely.

### P3.2 — Clean stale publishing.yaml config
Remove `format_mix` block and update `visuals.dimensions` to 1080x1920.

### P3.3 — Express trigger GET → POST
Change route method and update dashboard client.

### P3.4 — Deduplicate review logic
`blueprints.py` review routes delegate to shared `_execute_review_action()` in
`review_server.py`. WebSocket events emitted from the shared path.

### P3.5 — SESSION_COOKIE_SECURE env-based
Read from `os.getenv("FLASK_COOKIE_SECURE", "true")`. Default True for production,
set False in local dev via `.env`.

### P3.6 — Unify config loading
Replace all inline `yaml.safe_load()` calls with `config_loader.load_config()`.

### P3.7 — YouTube resumable upload retry with backoff
Add per-chunk retry (3 attempts, exponential backoff) for transient network errors
in `upload_short()` and `upload_video()`.

### P3.8 — YouTube client error handling for post_comment/update_metadata
Add `requests.HTTPError` catch in both methods.

### P3.9 — Persist community post text on manual_post_required
Write full text to `.tmp/runs/<run_id>/manual_posts/` JSON file.

### P3.10 — Landscape spec in single-file --video mode
Check `_landscape` suffix in `main()` single-video path, route to
`_check_landscape_spec()`.

### P3.11 — Dead loop removal in blueprints.py
Remove the url computation loop (lines 57-60).

### P3.12 — OData filter input validation
Validate `action_taken` against allowlist before interpolating into filter string.

---

## Files Changed (Summary)

| File | Phase(s) | Change Count |
|------|----------|-------------|
| `execution/publish_all_platforms.py` | 1, 2 | 7 fixes |
| `execution/publish_youtube.py` | 1 | 1 fix |
| `execution/utils/twitter_client.py` | 1, 2 | 3 fixes |
| `execution/publish_twitter.py` | 2, 3 | 2 fixes |
| `execution/publish_to_instagram.py` | 2 | 2 fixes |
| `execution/render_text_overlays.py` | 1, 2, 3 | 4 fixes |
| `execution/assemble_video_reel.py` | 1, 2 | 3 fixes |
| `execution/utils/ffmpeg_utils.py` | 2, 3 | 2 fixes |
| `execution/write_post_content.py` | 2, 3 | 2 fixes |
| `execution/adapt_for_platforms.py` | 2 | 2 fixes |
| `execution/write_run_report.py` | 2 | 1 fix |
| `execution/compose_blueprints.py` | 3 | 1 fix |
| `execution/utils/backlog_client.py` | 3 | 1 fix |
| `execution/push_to_backlog.py` | 3 | 1 fix |
| `execution/fetch_ai_creators.py` | 3 | 1 fix |
| `execution/utils/stable_ids.py` | 3 | 1 fix |
| `execution/validate_videos.py` | 4 | 1 fix |
| `execution/review_server.py` | 4 | 3 fixes |
| `execution/api/blueprints.py` | 4 | 2 fixes |
| `execution/utils/youtube_client.py` | 4 | 3 fixes |
| `execution/render_visuals.py` | 3 | 1 fix |
| `config/risk_rules.yaml` | 2 | 1 fix |
| `config/templates.yaml` | 3 | 1 fix |
| `config/content_prompts.yaml` | 3 | 1 fix |
| `config/publishing.yaml` | 4 | 1 fix |
| `execution/publish_facebook.py` | 3 | 1 fix |
| `execution/assemble_reel.py` | 4 | remove/archive |

## What Stays the Same

- Shorts (<=180s) -> always portrait 9:16
- Instagram Reels publishing flow (already correct after verification)
- Playwright page lifecycle (already has proper `finally: page.close()`)
- Batch landscape validation (already calls `_check_landscape_spec()`)
- `_simplify_topic()` (verified: defined correctly at line 811)
- Config/sources.yaml source list (no changes)
- Scoring weights (no changes)

## Verification Strategy

Each phase:
1. Write failing tests for testable fixes
2. Implement fixes
3. Run `venv/bin/python -m pytest tests/ -x -q --tb=short`
4. Verify import health
5. Commit per-phase

Expected final test count: 955+ existing + ~25-30 new tests = ~980-985 tests.
