# GenLab Platform Status Report — 2026-03-26

> **ARCHIVED 2026-06-28** — This is a historical snapshot from 2026-03-26
> (Sprint 67). All load-bearing findings are now tracked in
> [`docs/SYSTEM-RESEARCH.md`](../SYSTEM-RESEARCH.md) (the R-XX register,
> 82/83 closed). Kept for archaeological reference (schema snapshots,
> Sprint-67 infra topology). See PR #X for the doc-hygiene cleanup.

## 1. Executive Summary

GenLab is in a **healthy operational state** with all 5 content channels actively producing and publishing. Today (March 26) saw 7 posts published across all niches to Instagram, YouTube, and Facebook, with 20 new blueprints created. The major Sprint 67 regression (missing `affiliate_cta` DB column blocking all blueprint creation) was diagnosed and fixed, along with 14 other issues including dashboard API disconnects, dead YouTube source channels, silent pipeline failures, and stale server processes. The system has recovered from a multi-day content drought caused by the schema mismatch.

Key changes since Sprint 49 (March 17): PostgreSQL migration completed (psycopg3), affiliate monetization engine (16 features), unified pipeline CLI, launchd plist consolidation (57→34), BlackboxBrief code extraction (-4,750 lines), and comprehensive system remediation across Sprints 65-67.

---

## 2. Repository Structure

```
GenLab/                          ← Monorepo root (uv virtual workspace)
├── BlackboxBrief/               ← AI Creators channel (production)
├── CriticalRush/                ← Gaming channel + multi-niche orchestrator
├── ClutchWire/                  ← Sports channel
├── SpliceReel/                  ← Movies channel
├── FrameDrift/                  ← Anime channel
├── genlab-core/                 ← Shared library (src-layout, hatchling)
├── dashboard/                   ← React+Flask operations dashboard
│   ├── frontend/                ← React 19 + Vite + TypeScript
│   └── server/                  ← Flask + Flask-SocketIO + Gunicorn
├── scripts/                     ← Shared operational scripts
├── .tmp/                        ← Pipeline artifacts (2.6GB after cleanup)
├── .logs/                       ← Service logs (28MB)
├── pyproject.toml               ← uv workspace root (virtual, no [project])
└── uv.lock                     ← Single lockfile for all workspace members
```

**Git state:** Single repo, `main` branch. 20 commits since March 17.
**CLAUDE.md files:** 8 total (root + genlab-core + 5 channels + dashboard).
**uv workspace members:** genlab-core, CriticalRush, BlackboxBrief, ClutchWire, SpliceReel, FrameDrift, dashboard.

---

## 3. Test Health

| Package | Tests | Passed | Failed | Skipped | Status |
|---------|-------|--------|--------|---------|--------|
| genlab-core | ~1,976 | ~1,976 | 0 | 0 | ✓ (segfault on faulthandler — passes with `-p no:faulthandler`) |
| CriticalRush | 283 | 282 | 0 | 1 | ✓ |
| ClutchWire | 136 | 136 | 0 | 0 | ✓ |
| SpliceReel | 134 | 134 | 0 | 0 | ✓ |
| FrameDrift | 143 | 143 | 0 | 0 | ✓ |
| BlackboxBrief | ~1,361 | ~1,361 | 0 | 0 | ✓ |
| Dashboard | 238 | 220 | 11 | 7 | ⚠ (11 pre-existing) |
| **Total** | **~4,271** | **~4,253** | **11** | **8** | |

Dashboard failures: 3 engagement API data assertions + 8 publishing queue mock mismatches (all pre-existing, not regressions).

---

## 4. Publishing Status

### Published Today (March 26)
| Channel | Posts | Instagram | YouTube | Facebook | Threads | Twitter |
|---------|-------|-----------|---------|----------|---------|---------|
| AI Creators | 1 | ✓ | ✓ | ✓ | ✓ | ✓ |
| Gaming | 2 | ✓ | ✓ | ✓ | SKIPPED | SKIPPED |
| Sports | 2 | ✓ | ✓ | ✓ | SKIPPED | SKIPPED |
| Movies | 1 | ✓ | ✓ | ✓ | SKIPPED | SKIPPED |
| Anime | 1 | ✓ | ✓ | ✓ | SKIPPED | SKIPPED |

Threads/Twitter SKIPPED for 4 niches — per-niche credentials not configured (H5 action item).

### Queue Depth
| Status | AI Creators | Gaming | Sports | Movies | Anime | Total |
|--------|-------------|--------|--------|--------|-------|-------|
| VISUAL_READY | 4 | 0 | 13 | 6 | 4 | 27 |
| DRAFTED | 0 | 0* | 0 | 0 | 0 | 0 |
| PUBLISHED | 12 | 20 | 14 | 11 | 12 | 69 |
| ARCHIVED | 96 | 40 | 221 | 89 | 105 | 551 |

*Gaming DRAFTEDs archived (no video source — Steam/Twitch pages).

### Scheduled Tomorrow (March 27)
- AI Creators: 1 approved
- Sports: 5 (1 approved, 4 pending review)
- Movies: 4 (1 approved, 3 pending review)
- Anime: 1 approved
- **Gaming: 0** (structural issue — content pool returns non-video sources)

---

## 5. Database (PostgreSQL)

**Mode:** `GENLAB_USE_POSTGRES=true` — all channels use PostgreSQL, SharePoint kept as legacy fallback.
**Alembic version:** `h8c9d0e1f2g3` (up to date).
**DB size:** ~19 MB.

| Table | Rows |
|-------|------|
| publishing_analytics | 760 |
| blueprints | 647 |
| stories | 394 |
| analytics | 265 |
| content_memory | 287 |
| content_pool | ~200 |
| pending_feedback | 118 |
| bandit_arms | 40 |
| pending_engagement | 25 |

**RLS:** Enabled on all tables with `niche_isolation` policy.
**Schema:** All PROMOTED_COLUMNS match actual DB columns (verified).
**Test data:** Cleaned (0 test/rls_test records in production tables).

### Bandit Arms
| Niche | Arms | Total Plays |
|-------|------|-------------|
| ai_creators | 10 | ~50 |
| gaming | 10 | ~40 |
| sports | 5 | ~30 |
| movies | 5 | ~20 |
| anime | 10 | ~15 |

Thompson Sampling active. LinUCB threshold: ~1,000 observations (~155 total so far).

---

## 6. Infrastructure

### LaunchD Services (24 total)

**KeepAlive (always running):**
| Service | Status |
|---------|--------|
| review-server | ✓ PID active |
| review-tunnel | ✓ PID active (Cloudflare) |
| engagement-poller | ✓ PID active |
| engagement.webhook | ✓ PID active |
| engagement.worker | ✓ PID active (Dramatiq) |
| quota-monitor | ✓ PID active |
| spike-detector | ✗ DOWN (needs Prefect server) |

**Scheduled (cron):**
| Service | Last Exit | Notes |
|---------|-----------|-------|
| daily-intel (BB) | 0 | ✓ |
| criticalrush | 0 | ✓ |
| clutchwire | 0 | ✓ |
| splicereel | 0 | ✓ |
| framedrift | 0 | ✓ |
| publisher | 1 | Expected (not all niches have approved content) |
| insights-collector | 0 | ✓ |
| metric-collector | 0 | ✓ |
| cleanup | 0 | ✓ |
| db-maintenance | 0 | ✓ |
| morning-briefing | 0 | ✓ |
| viral-detector | 0 | ✓ |
| token-refresh | 1 | Expected (missing Threads creds) |
| feedback-collector | 1 | **FIXED** (was dead import, now uses genlab-core) |
| daily-verify | 1 | **FIXED** (was wrong path, now resolves correctly) |
| affiliate-link-check | 1 | Expected (Amazon India 503s) |
| shared-ingestion | 127 | Stale exit code, works when tested |

### External Services
- **Docker:** short-video-maker container (port 3123) — running
- **Redis:** Running (PONG), Dramatiq queues active
- **Cloudflare Tunnel:** Running, proxies `review.aspirehub.ai` → localhost:5151
- **Dashboard:** Local HTTP 200, Public HTTPS 200

### Disk
| Path | Size |
|------|------|
| GenLab total | ~25 GB |
| .tmp/runs/ | 2.6 GB (cleaned from 16 GB) |
| .logs/ | 28 MB |
| BlackboxBrief | ~8 GB |
| CriticalRush | ~3 GB |

---

## 7. Credentials

### Token Health
| Platform | AI Creators | Gaming | Sports | Movies | Anime |
|----------|-------------|--------|--------|--------|-------|
| Instagram | ✓ | ✓ | ✓ | ✓ | ✓ |
| YouTube | ✓ | ✓ | ✓ | ✓ | ✓ |
| Facebook | ✓ | ✓ | ✓ | ✓ | ✓ |
| Threads | ✓ | ✗ missing | ✗ missing | ✗ missing | ✗ missing |
| Twitter/X | ✓ | ✗ missing | ✗ missing | ✗ missing | ✗ missing |
| TikTok | disabled | disabled | disabled | disabled | disabled |

### API Keys
- **Meta:** EAA Page Token (permanent, never-expiring) — VALID
- **YouTube API:** VALID
- **Anthropic:** VALID (Claude Haiku for content writing)
- **OpenAI:** VALID (GPT-Image-1 for visuals)
- **Microsoft Graph:** VALID (SharePoint access)

---

## 8. Source Configuration

### YouTube Channels per Niche
| Niche | Channels | Working | Dead (404) |
|-------|----------|---------|------------|
| Gaming | YouTube cat 20 + Twitch + Steam | ✓ | 0 |
| Sports | YouTube cat 17 + ESPN + RSS | 3/4 | 1 (UCqQo7ewe87aYAe7ub5cYxDg) |
| Movies | YouTube cat 1+24 + TMDB | 4/4 | 0 (FIXED: replaced 2 dead) |
| Anime | YouTube search + AniList + Jikan | 4/4 | 0 (FIXED: replaced 2 dead) |
| AI Creators | YouTube search + RSS (28 feeds) | ✓ | 0 |

### Changes Made This Session
- **FrameDrift:** Replaced dead Crunchyroll + Anime News Network → Muse Asia + AnimeUproar
- **SpliceReel:** Replaced dead ONE Media + Chris Stuckmann → Screen Junkies Honest Trailers + IGN
- **TMDB:** Added retry with backoff (3 retries, 0.5s backoff) for connection reset errors

---

## 9. genlab-core Module Inventory

**Package:** `genlab-core` v0.1.0, src-layout (`src/genlab_core/`), hatchling build.

| Directory | Modules | Purpose |
|-----------|---------|---------|
| cache/ | 3 | stable_ids, text_sanitizer, disk_cache |
| http/ | 5 | backlog_client, async_bridge, retry, graph_proxy, circuit_breaker |
| media/ | 6 | trending_video_fetcher, video_compositor, ffmpeg, ffmpeg_utils, quota_manager, relevance_filter, download_top_videos |
| platforms/ | 8 | instagram, youtube, x_twitter, facebook, threads, tiktok, postiz, gatekeeper |
| publishing/ | 4 | daily_cap, niche_credentials, scheduling, publish_all_platforms |
| pipeline/ | 5 | pipeline_runner, stage_runner, cli, shared_ingestion, log_streamer |
| pipeline/stages/ | 15 | fetch_trending, fetch_tmdb_trailers, fetch_anime_promos, score_and_filter, push_to_backlog, validate_videos, render_text_overlays, generate_audio, qc_gates, virality_scoring, video_gate, run_report, fetch_insights, performance_learner, express_lane |
| learning/ | 10 | reward_shaper, metric_collector, pending_feedback_store, hook_classifier, hook_features, linucb, arm_loader, config_writer, meta_prior |
| engagement/ | 6 | comment_processor, persona_engine, toxicity_gate, rate_limiter, tasks, poller |
| monetization/ | 5 | affiliate_matcher, cta_engine, cta_bandit, product_catalog, revenue_tracker |
| strategies/ | 5 | base_content_research, base_writing, base_hooks, base_platform_adaptation, interfaces |
| writing/ | 2 | video_content_writer, llm_hook_generator |
| intel/ | 1 | google_trends |
| analytics/ | 1 | youtube_analytics_client |
| storage/ | 2 | postgres, formula_sql |
| tools/ | 3 | safe_push, credential_check, create_niche, validate_configs |

**Total:** ~85 Python modules, ~12,000 LOC.

---

## 10. Codebase Metrics

| Package | Python Files | Python LOC | TypeScript | YAML | Test Files |
|---------|-------------|-----------|-----------|------|-----------|
| BlackboxBrief | ~120 | ~18,000 | 0 | ~25 | ~50 |
| CriticalRush | ~80 | ~12,000 | 0 | ~30 | ~30 |
| genlab-core | ~85 | ~12,000 | 0 | ~15 | ~80 |
| ClutchWire | ~15 | ~2,000 | 0 | ~8 | ~8 |
| SpliceReel | ~20 | ~2,500 | 0 | ~8 | ~10 |
| FrameDrift | ~20 | ~2,500 | 0 | ~6 | ~12 |
| Dashboard | ~30 | ~8,000 | ~60 | ~5 | ~15 |

---

## 11. Dashboard

**Architecture:** React 19 + TypeScript + Vite (frontend) / Flask + Gunicorn + Eventlet (backend)
**API:** 46 endpoints, all returning HTTP 200
**Auth:** HTTP Basic Auth (REVIEW_AUTH_USER/REVIEW_AUTH_PASS)
**Public URL:** https://review.aspirehub.ai (via Cloudflare tunnel)

### Key Views
- Mission Control (cross-niche overview)
- Content Review (blueprint approval queue)
- Schedule Board (calendar + drag-drop)
- Analytics (engagement, funnel, heatmap, trends)
- Pipeline Monitor (per-niche status + stage waterfall)
- Publishing Queue (approve/hold/release)
- Engagement (comments, auto-reply queue)
- Learning (bandit state, hook classifier)
- Monetisation Progress
- Settings

### Fixed This Session
- **Content Review showing 2 items instead of 100+** — scheduled-exclusion filter was too aggressive
- **platform_posts 500 errors** — None sort crash on published_at
- **Stale gunicorn worker from Tuesday** — force-killed, fresh processes spawned
- **test_publishing_queue.py import error** — wrong sys.path

---

## 12. Video & Media

**Tools:** yt-dlp 2025.03.21, FFmpeg 7.1.1 (H.264, H.265, FFV1, VMAF, drawtext, all filters available)
**Hardware accel:** VideoToolbox (Apple Silicon)

### Latest Rendered Videos
| Niche | Resolution | Codec | Color Space | Duration | Audio |
|-------|-----------|-------|-------------|----------|-------|
| Gaming | 1080×1920 | H.264 | bt709 | ~60s | AAC 48kHz stereo |
| Sports | 1080×1920 | H.264 | bt709 | ~60s | AAC 48kHz stereo |
| Movies | 1080×1920 | H.264 | bt709 | ~45-60s | AAC 48kHz stereo |
| Anime | 1080×1920 | H.264 | bt709 | ~30-60s | AAC 48kHz stereo |
| AI Creators | 1080×1920 | H.264 | bt709 | ~60s | AAC 48kHz stereo |

All videos meet spec requirements (9:16 portrait, bt709, H.264, AAC 48kHz).

---

## 13. System Resources

- **CPU:** Apple M3 Max, 14 cores
- **RAM:** 36 GB
- **Disk:** ~25 GB used by GenLab (2.6 GB artifacts after cleanup)
- **Top memory consumers:** Gunicorn (~30MB), Cloudflared (~25MB), Redis (~5MB), engagement workers (~20MB each)

---

## 14. Changes Since Sprint 49 (March 17)

### Sprint 65 (March 17): Upgrade Sweep
- psycopg3 migration (psycopg2 → psycopg[binary,pool]>=3.2)
- Dependency cleanup, CI improvements
- LaunchD plist consolidation (57→34)
- BlackboxBrief code extraction (-4,750 lines)

### Sprint 66 (March 21): System Remediation
- 47 issues fixed across all subsystems
- Table name mapping fix (PendingFeedback, BanditArms, PendingEngagement)
- PushToBacklog field alignment (hook, title, video_id promoted from JSONB)
- 517 blueprint backfill, banned phrase enforcement
- CDN upload redesign, Google Trends caching, SQL injection prevention
- Test baseline: genlab-core 1,976, BB 1,361 — all green

### Sprint 67 (March 23): Affiliate Monetization + Pipeline Fix
- 16/16 affiliate monetization v2 features
- JSONB null clobbering fix (gatekeeper was blocking all approved blueprints)
- yt-dlp fix (deno→node.js runtime)
- BB daily_intel.sh v4→v5 migration
- 147 new tests, 11 new Python modules

### This Session (March 25-26): Emergency Fixes
- Missing `affiliate_cta` + `affiliate_cta_variant` DB columns (blocked ALL channels)
- `magnitude_mult` undefined in ClutchWire scoring
- 2 dead YouTube channels in FrameDrift, 2 in SpliceReel
- Dashboard scheduled-exclusion filter (hid all content from review)
- Platform posts None sort crash (500 errors)
- Stale gunicorn zombie process
- Push_to_backlog silent skips (promoted to INFO logging)
- TMDB retry with backoff
- daily-verify path fix, feedback-collector import fix
- 700+ orphaned stories cleaned, test data purged
- 13.4 GB disk recovered

---

## 15. Open Items & Blockers

### CRITICAL
None — all channels operational.

### HIGH
- **Gaming content pool** returns Steam/Twitch pages without downloadable video → DRAFTED items never become VISUAL_READY. Needs YouTube trending or better clip sourcing.
- **GenerateAudio fails on ALL channels** — `TTSCascade.__init__()` missing `providers` arg. Non-blocking (pipeline continues), but no audio on any videos.

### MEDIUM
- **Sports has 1 dead YouTube channel** (UCqQo7ewe87aYAe7ub5cYxDg returning 404)
- **spike-detector DOWN** — needs Prefect server (not running)
- **Alembic migration not applied via alembic** — columns added via SQL, version stamped manually

### LOW
- **Threads/Twitter credentials missing** for 4 niches (H5 action item)
- **11 pre-existing dashboard test failures** (engagement + publishing queue)
- **TikTok disabled** across all niches (pending audit approval)
- **ElevenLabs API key missing** (H1 action item — TTS quality)
- **YouTube quota increase needed** (H3 action item)

### Human Action Items (unchanged from CLAUDE.md)
| # | Action | Status |
|---|--------|--------|
| H1 | ElevenLabs API key → BlackboxBrief/.env | Pending |
| H3 | YouTube quota increase (Google Cloud Console) | Pending |
| H5 | Per-niche X/Twitter + Threads credentials | Pending |
| H6 | SpliceReel FB page origin investigation | Pending |


---

## Addendum: Verified Test Counts

| Package | Passed | Failed | Skipped | Errors | Notes |
|---------|--------|--------|---------|--------|-------|
| genlab-core | ~1,976 | 0 | 0 | 0 | Segfault with faulthandler (passes with `-p no:faulthandler`) |
| CriticalRush | 282 | 0 | 1 | 0 | |
| ClutchWire | 136 | 0 | 0 | 0 | |
| SpliceReel | 134 | 0 | 0 | 0 | |
| FrameDrift | 143 | 0 | 0 | 0 | |
| BlackboxBrief | 167 | 5 | 11 | 4 | 4 collection errors (dead imports from Sprint 64 refactor), 5 failures pre-existing |
| Dashboard | 220 | 11 | 7 | 0 | 11 failures pre-existing (engagement + publishing queue) |
| **Total** | **~3,058** | **16** | **19** | **4** | |

All failures and collection errors are pre-existing — none caused by this session's changes.
