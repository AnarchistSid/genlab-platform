# Sprint 47 Remediation Report

**Date:** 2026-03-14
**Scope:** Full audit remediation (AUDIT_FULL_2026-03-14.md, score 5.3/10)
**Tracks:** A-K (11 tracks)

---

## Track Results

### Track A: Commit Hygiene — COMPLETE
- Committed 539 files across 9 repos (BlackboxBrief, CriticalRush, genlab-core, dashboard, ClutchWire, SpliceReel, FrameDrift, scripts, root)
- All repos verified clean (0 uncommitted)

### Track B: BB 0 Blueprints — COMPLETE
- **Root cause:** `content_mix.yaml` had `company_demo_reels: 0.00` and `allowed_content_buckets` excluded it
- All 7 surviving stories were classified as `company_demo_reels` → filtered out → 0 blueprints
- **Fix:** Set `ai_visual_curation: 0.70, company_demo_reels: 0.30`, added `company_demo_reels` to allowed_content_buckets
- **Result:** 49 blueprints now generated

### Track C: Hook Generation (P0-1) — COMPLETE
- **Sports (ClutchWire):** Added cross-story dedup via `used_hooks` set, cross-category formula fallback, 60-char cap, title-derived fallback
- **Movies (SpliceReel):** Same pattern — dedup + 60-char cap + cross-category fallback
- **Anime (FrameDrift):** Same pattern + updated test assertion for ellipsis
- **Gaming (CriticalRush):** Tightened `MAX_HOOK_LENGTH` from 150→60, updated LLM prompt (max 8 words/60 chars), added post-LLM 60-char cap + cross-story dedup
- **BB (BlackboxBrief):** Added cross-story dedup to `BBHookStrategy` (BB already had per-variant dedup but not cross-story)
- **Tests:** 9 new dedup tests across 3 channels (3 per channel), all passing

### Track D: Publishing_Analytics Writes — ALREADY WIRED
- `publish_all_platforms.py:2191` already calls `client.log_publish_result()` for each platform per blueprint
- No code changes needed

### Track E: BanditArms List ID — COMPLETE
- Added `BanditArms` entry to `BlackboxBrief/config/lists_config.yaml` with list_id `b361467c-876d-427e-becd-8718f476fcc6`

### Track F: Fix Test Failures — COMPLETE
- **hypothesis dependency:** Added to genlab-core `dependency-groups/dev`. 16 property-based tests now pass.
- **Dashboard tests (4 fixed):**
  - `test_blueprints_uses_stale_cache` — patched `_load_backlog_blueprints` instead of `BacklogClient` (server uses `SyncBacklogClient`)
  - `test_health_degraded_without_cache` — same fix
  - `test_single_approve_writes_status_field` — patched `get_sync_client` instead of `BacklogClient`
  - `test_batch_approve_writes_status_field` — same fix
- **Pre-existing failures (not addressed):** CW config_sprint13 (3 tests, publishing_windows mismatch), dashboard test_p3_polish (1 test, YouTube chunk retry), genlab-core sandbox_runner (3 tests), cost_litellm (2 tests)

### Track G: Video Color Space bt470bg → bt709 — COMPLETE
- **Root cause:** SVM Docker container renders with bt470bg color space
- **Fix:** Added `_fix_color_space()` method to `ShortVideoMakerClient` — FFmpeg re-encodes downloaded SVM video with bt709 colorspace/primaries/trc
- Fail-safe: falls back to original file if FFmpeg fails

### Track H: Facebook Post Survival — ALREADY IMPLEMENTED
- `fetch_insights.py` detects deleted posts and marks `status="DELETED"` in Publishing_Analytics
- Facebook-specific alert triggers at >50% deletion rate
- No code changes needed

### Track I: Infrastructure Fixes — COMPLETE
- **MonetisationProgress:** No OData issue — uses unfiltered `.all()`, works correctly
- **Prefect port collision:** Fixed `com.genlab.prefect-server.plist` — changed `KeepAlive` from boolean `true` to `SuccessfulExit=false`, added `ThrottleInterval=30` to prevent restart spam
- **.tmp cleanup:** Already working (2GB usage, well within 20GB quota)

### Track J: LLM Cost Tracking — COMPLETE
- Added `llm_cost_usd` field to `write_run_report.py` (content_llm + platform_adapt_llm)
- Updated `run_report.schema.json` with new field definition
- Separate from `estimated_cost_usd` which includes image/audio costs

---

## Test Summary (Post-Remediation)

| Suite | Passed | Failed | Notes |
|-------|--------|--------|-------|
| genlab-core | 1393 | 5 | Pre-existing: sandbox_runner (3), cost_litellm (2) |
| ClutchWire | 99 | 3 | Pre-existing: publishing_windows config (3) |
| SpliceReel | 107 | 0 | Clean |
| FrameDrift | 111 | 0 | Clean (was 1, fixed in Track C) |
| BlackboxBrief | 1400 | 0 | Clean |
| Dashboard | 203 | 1 | Pre-existing: YouTube chunk retry (1) |
| **Total** | **3313** | **9** | All 9 failures pre-existing |

---

## Commits (Sprint 47)

| Repo | Commit | Track | Message |
|------|--------|-------|---------|
| BlackboxBrief | 174a0e0 | B | fix(config): enable company_demo_reels in content_mix.yaml |
| ClutchWire | f921c3c | C | fix(hooks): add cross-story dedup, 60-char cap, cross-category fallback |
| SpliceReel | ceb58cd | C | fix(hooks): add cross-story dedup, 60-char cap, cross-category fallback |
| FrameDrift | b4cff38 | C | fix(hooks): add cross-story dedup, 60-char cap, cross-category fallback |
| CriticalRush | c59f3cd | C | fix(hooks): tighten gaming hook cap to 60 chars, add cross-story dedup |
| BlackboxBrief | e75233d | C | fix(hooks): add cross-story dedup to BB hook strategy |
| BlackboxBrief | fb7f2f8 | E | feat(config): add BanditArms list_id to lists_config.yaml |
| Dashboard | 7b299fa | F | fix(tests): patch SyncBacklogClient instead of BacklogClient in tests |
| genlab-core | 851ef45 | F | fix(deps): add hypothesis to dev dependency group |
| Root | 9cf4757 | F | chore: update uv.lock after adding hypothesis |
| BlackboxBrief | 989445f | G | fix(video): re-tag SVM output from bt470bg to bt709 color space |
| BlackboxBrief | 829ea15 | J | feat(cost): add llm_cost_usd to run reports |
