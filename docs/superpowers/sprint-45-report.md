# Sprint 45 — Complete System Remediation + Schedule Change

**Date:** 2026-03-13
**Scope:** Full v3 audit remediation (P0–P3) + publishing cadence change

---

## Summary

All 8 tracks executed in order. Every track ended with tests passing + git commit.
Zero new test failures introduced. One real bug found and fixed during Track G testing.

## Track Results

### Track A: P0 Emergency Fixes (A1–A6)
- **A1:** Daily publishing cap (1/day/niche) in `genlab_core.publishing.daily_cap`
- **A2:** Whisper timing guard — `_safe_word_ts()` clamps to `[0, duration]`, prevents negative subtitle times
- **A3:** Whisper caption render — tolerates out-of-range timestamps without crashing FFmpeg
- **A4:** Instagram carousel — validates media container before publishing
- **A5:** Monetisation tracker — handles missing `revenue_estimate` fields gracefully
- **A6:** Media __init__ lazy imports — prevents circular import crash on startup

### Track B: Schedule Change
- Publishing cadence changed from 2x/day to **1x/day at 12:00 IST (06:30 UTC)**
- Updated: BB `publishing.yaml`, gaming `publishing.yaml`, 2 LaunchAgent plists, dashboard schedule fallback

### Track C: Data Integrity Fixes
- SharePoint graph_proxy `max_records` caps across all uncapped `.all()` calls
- Stable ID generation for analytics + A/B test IDs

### Track D: Facebook + Analytics Fixes
- **D1:** Facebook rules — strip URLs, remove competitor hashtags, cap at 5, engagement question for short captions (7 new tests)
- **D2:** Analytics — YouTube 72h age guard, Facebook all-zero permission detection, Threads UNAVAILABLE fallback, Facebook DELETED rate alerting (4 new tests)

### Track E: Dashboard Fixes
- **E1:** Analytics timeout fix — `max_records=500` on all uncapped SharePoint queries + bandit state 60s cache
- **E2:** Stub endpoints for `/api/v1/engagement/queue` and `/api/v1/publishing/schedule`
- **E4:** LaunchAgent secret removal — all 6 plists now use `launch_wrapper.sh` instead of inline env vars

### Track F: Code Quality + Architecture
- **F1:** IGDB client rate limiter — `TokenBucket(rate=4.0, burst=4.0)` before each API call
- **F2:** CriticalRush gaming stages retrofitted to strategy ABCs (ContentResearchStrategy, WritingStrategy, VisualRenderStrategy, PlatformAdaptationStrategy)
- **F3:** Dashboard accent color standardized — ai_creators `#3B82F6` → `#6366f1`
- **F4:** FrameDrift fashion→anime rename — 6 strategies, __init__, niche.yaml, 6 test files
- **F6:** Hygiene — removed BlackboxBrief `requirements.txt`, moved pytest to dev deps

### Track G: Test Coverage Gaps
- **G1:** 46 new dashboard smoke tests covering 9 previously untested API modules (34 routes)
- **G2:** 22 new scripts tests for `intelligence_hub` + `launch_wrapper`
- **Bug found:** `/api/v1/schedule/coverage` was calling `.get_json()` on a `(response, status_code)` tuple — would have 500'd in production. Fixed.

### Track H: Final Verification
- Full test suite across all packages: **3388 passed, 8 pre-existing failures**
- No new failures introduced

## Test Counts (Post-Sprint)

| Package | Passed | Failed | Skipped | Notes |
|---------|--------|--------|---------|-------|
| genlab-core | 1283 | 5 | 3 | Pre-existing: sandbox egress (3), litellm cost (2) |
| CriticalRush | 492 | 0 | 1 | |
| BlackboxBrief | 1390 | 0 | 19 | |
| Dashboard | 201 | 3 | 0 | Pre-existing: YouTube chunk retry (3) |
| Scripts | 22 | 0 | 0 | **NEW** |
| **Total** | **3388** | **8** | **23** | All failures pre-existing |

## New Test Coverage Added

| Area | Tests Added | Coverage Gap Closed |
|------|-------------|---------------------|
| Platform rules (Facebook) | 7 | Competitor hashtags, URL stripping, hashtag cap |
| Fetch insights | 4 | FB permissions, Threads unavailable, YT age guard |
| Dashboard learning API | 2 | Bandit cache, error handling |
| Dashboard engagement API | 2 | Queue stub, schedule stub |
| Dashboard smoke tests | 46 | 9 API modules (34 routes) |
| Scripts intelligence hub | 17 | Trend boost, optimal time, viral alerts, baselines |
| Scripts launch wrapper | 5 | Exec passthrough, exit codes, env loading |
| **Total new tests** | **83** | |

## Human Action Items (NOT automatable)

| ID | Action | Status |
|----|--------|--------|
| H1 | Rotate ElevenLabs API key and update `.env` | Pending |
| H2 | Rotate all credentials (Twitch, Azure, etc.) per security audit | Pending |
| H3 | Request YouTube quota increase via Google Cloud Console | Pending |
| H4 | Investigate SpliceReel Facebook page (404s on publish) | Pending |
| H5 | Provision per-niche X/Threads credentials (ClutchWire, SpliceReel, FrameDrift) | Pending |

## Commits (by track)

All commits follow conventional commit format with track prefix.
Each repo has its own git history — commits are in the respective repos:
- `genlab-core/` — Track A, D1
- `BlackboxBrief/` — Track B, D2, F6
- `CriticalRush/` — Track A (whisper), B, C, F1, F2
- `FrameDrift/` — Track F4
- `dashboard/` — Track B, E, F3, G1
- `scripts/` — Track E (launch_wrapper), G2
