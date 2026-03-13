# Sprint 45B — Audit Gap Remediation — COMPLETE

**Date:** 2026-03-14
**Scope:** 17 remediation items across 5 tracks, addressing every finding from the v3 Exhaustive Audit NOT covered in Sprint 45.

---

## Track AA — Data & SharePoint Gaps

- [x] **AA1**: Fixed `analytics_recorder.py` field name (`error_log` → `error_message`) to match SP schema. Added error_message propagation in `fetch_insights.py` for DELETED posts. 4 new tests.
- [x] **AA2**: Added `PendingFeedback` and `Content_Memory` entries to all 4 niche `lists_config.yaml` files (CriticalRush, ClutchWire, SpliceReel, FrameDrift).
- [x] **AA3**: Replaced hardcoded `CONTENT_MEMORY_LIST_ID` in `content_memory.py` and `backfill_content_memory_niche_id.py` with config-file reads (with fallback).
- [x] **AA4**: Confirmed CriticalRush config files exist at `config/` (not `configs/`). Added missing PendingFeedback + Content_Memory entries.
- [x] **AA5**: Sprint 42 blueprint discrepancy investigation (background agent, findings in `CriticalRush/SPRINT42_INVESTIGATION.md`).

## Track BB — Rate Limiters

- [x] **BB1**: Added `TokenBucket(rate=1.0, burst=5)` to `YouTubeAnalyticsClient`.
- [x] **BB2**: Added thread-safe daily rate limiter (900/day) to `OMDbClient`.
- [x] **BB3**: Added `TokenBucket(rate=1.0, burst=10)` to `SteamSpikeFetcher` (both API endpoints).
- [x] **BB4**: Added `TokenBucket(250/3600, burst=10)` to `ThreadsClient._create_container()`. TikTok skipped (stub-only, always fails).

## Track CC — Infrastructure Gaps

- [x] **CC1**: Added per-step SLO warnings (>5min = SLOW) and pipeline-level 40min SLO check to `daily_intel.sh`. Root cause: `render_visuals.py` has zero parallelism (1891 LOC, sequential processing).
- [x] **CC2**: Added specific `tweepy.Unauthorized`/`tweepy.Forbidden` exception handling in `engagement/poller.py`. Improved `check_token_health.py` to distinguish 401 (expired) vs 403 (expected on free tier).
- [x] **CC3**: Disabled Postiz containers in `docker-compose.yaml` with DISABLED comment. Containers stopped.

## Track DD — Code Quality

- [x] **DD1**: Added ruff linter config to workspace `pyproject.toml`. Fixed 438 pyflakes errors (F401/F541) across all packages via auto-fix.
- [x] **DD2**: Added module-level docstrings to 3 CriticalRush shim files (`cdn_upload.py`, `threads_client.py`, `tiktok_client.py`).
- [x] **DD3**: Fixed 3 pre-existing `TestYouTubeChunkRetry` dashboard test failures. Root cause: YouTube quota gate (added after tests were written) blocked uploads before retry logic could run. Fix: autouse fixture mocking `YouTubeQuotaTracker` in `sys.modules`.
- [x] **DD4**: Deleted `genlab_core.platform/` shim package (6 files). Migrated all callers to canonical paths (`platforms.postiz`, `platforms.rules`, `engagement.*`). Consolidated `render/` into `rendering/` (moved `video_renderer.py`, updated all imports). Deleted `test_postiz_shim.py`.
- [x] **DD5**: Updated `.env.example` with 25+ missing env vars (Redis, webhooks, gaming APIs, OpenSandbox, Postiz, FFmpeg, dashboard auth, stock media keys, per-niche account IDs).

## Track EE — Final Verification

### Test Counts
| Package | Passed | Failed | Notes |
|---------|--------|--------|-------|
| genlab-core | 1,286 | 10 | All pre-existing (3 sandbox egress, 2 litellm cost, 5 daily_cap) |
| Content Scraper | 1,397 | 0 | |
| CriticalRush | 492 | 0 | |
| dashboard | 204 | 0 | |
| **Total** | **3,379** | **10** | **0 new failures** |

### Pre-existing Failures (not introduced by Sprint 45B)
- `test_sandbox_runner.py` (3): OpenSandbox egress policy API mismatch
- `test_cost_litellm.py` (2): litellm cost computation fallback
- `test_daily_cap.py` (5): Sprint 45 cap=1 change, tests expect updated behavior but code not aligned

### Commits
1. `fix(analytics): use error_message field, propagate on DELETED posts` (AA1)
2. `feat(config): add PendingFeedback + Content_Memory to all niche lists_config` (AA2)
3. `fix(scripts): replace hardcoded Content_Memory list ID with config-file read` (AA3)
4. `feat(config): add PendingFeedback + Content_Memory to CriticalRush lists_config` (AA4)
5. `feat(ratelimit): add TokenBucket to YouTube Analytics, OMDB, Steam, Threads` (BB1-BB4)
6. `feat(infra): add SLO timing to daily_intel.sh, improve Twitter error handling` (CC1-CC2)
7. `fix(docker): disable Postiz containers pending integration` (CC3)
8. `feat(lint): add ruff config, auto-fix 438 pyflakes errors` (DD1)
9. `docs(CriticalRush): add module docstrings to 3 publishing shim files` (DD2)
10. `fix(dashboard): mock YouTube quota gate in chunk retry tests` (DD3)
11. `refactor(genlab-core): delete platform/ shim, consolidate render/ → rendering/` (DD4)
12. `docs: update .env.example with missing env vars` (DD5)

### Human Actions (NOT automatable)
- **H1**: FrameDrift needs Azure/SharePoint credentials for bandit arm_loader
- **H2**: Placeholder `account_id` values in per-niche configs need real platform accounts
- **H3**: Postiz postgres container needs manual investigation when re-enabling
