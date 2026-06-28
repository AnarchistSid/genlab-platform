# Pending work — comprehensive inventory as of 2026-06-18 (EOD refresh)

Snapshot after a **28-PR / 24-hour** fix arc resolving the
2026-06-17 audit backlog + several runtime bugs surfaced during
operator-visible content-production debugging.

This doc supersedes `PENDING-AS-OF-2026-06-17.md`. Items marked
DONE today are not repeated unless they have a residual operator
or follow-up task.

## TL;DR (2026-06-18 EOD)

| Audit category | 2026-06-17 open | 2026-06-18 closed | Still open |
|---|---|---|---|
| **R-NN risk register** | 1 (R-31 affiliate creds) | 0 | 1 (operator-blocked) |
| **U-NN upgrade register** | 6 (U-10/U-15/U-24/U-25 + 2 minor) | 5 (U-10, U-15, U-24-partial, U-25, + previous closures) | 1 (U-24 starlette 1.x — multi-day FastAPI compat) |
| **W3.x / W4.x autonomy** | 3 (W3.3, W3-engagement, W4.4) | 2 (W3-engagement, W4.4) | 1 (W3.3 multi-day ML) |
| **SR-A through SR-F (SaaS §9)** | 4 (SR-A, SR-C, SR-D, SR-E) | 3 (SR-A/C/D via tier-5 migration) | 1 (SR-E tenant-2-only) |
| **L-NN / M-NN dashboard** | 4-5 (M-19/M-20/M-21 frontends + AUTO #2 slider) | 4 | 0 |
| **Today's surfaced bugs** | n/a | 8 (trends pollution, sports text-news, fixture detection, video-bearing guard, source-field detection, Reddit OAuth+cookies, publisher disabled-platform, CI timeouts, certbot orphan, stale alerts × 2 sweeps, W4.4 backfill, W3 frontend) | 0 |

**Net engineering-actionable items remaining: 3** (all explicitly multi-day).

---

## 1. Operator-blocked (engineering CAN'T unblock)

Unchanged from 2026-06-17 except where noted.

| Item | Source | What's needed |
|---|---|---|
| **PA-API credentials** | R-31 (a') | Amazon 10 sales / 30d. PR #277 (geo→US) running with reasonable cadence |
| **Impact API credentials** | R-31 (a) | Impact account + campaign IDs |
| **ShareASale credentials** | R-31 (a) | ShareASale merchant relationships |
| **CJ Affiliate credentials** | R-31 (a) | CJ PID/AID |
| **ElevenLabs API key** | quick-win | sign up at elevenlabs.io |
| **Twitter API credentials** | gap-analysis | Content-policy decision + dev account (anime/movies/sports/AI all set `x.enabled: false` per PR #326, fix shipping this) |
| **AUTO #2 Day-8 calibration** | runbook §5 | Operator review ≥30/niche × ≥90% agreement (calibration table + agreement-rate card + engagement-chip now live per W3+W4.4 today) |
| **YouTube cookies refresh** | classifier-blocked | Browser extension export → SSH paste (auto-classifier blocks Playwright remote-cred transfer; WARP covers most cases) |

---

## 2. Engineering-actionable

### 2a. Genuinely-open — explicit multi-day work

| ID | Effort | Item | Notes |
|---|---|---|---|
| **W3.3** | L (multi-day) | Transformer-embedding hook classifier | Currently keyword/regex. Real ML work — feature engineering + training + serving. Not in scope for incremental sprints. |
| **U-24 (residual)** | M (multi-day) | starlette 0.52 → 1.x | FastAPI 0.136 requires starlette>=0.46.0 but doesn't pin upper. starlette 1.x breaks 9 prod tests (CheckViolation on stories table). Needs coordinated FastAPI compat audit. Currently latest 0.x (0.52.1). |
| **SR-E** | M | Per-tenant YouTube API key (Quota DoS) | Multi-tenant SaaS only; phase-1 single-tenant doesn't need it |

### 2b. Resolved 2026-06-18 (kept brief — full notes in session memories)

**Trends + content production arc** (PRs #310, #312, #318-#325 + #312):
- PR #318: trends keyword pollution (general US trends leaking into niche YouTube searches) — fix returned `[]` instead of `niche+general`
- PR #319: sports ESPN news fetcher (24 text articles/run polluting candidate pool) — opt-in flag
- PR #320: sports fixture-preview detection — zero-reject "Team A - Team B" titles without scores/Final markers
- PR #322: sports text-only RSS feeds removed (ESPN/Athletic/BBC/Sky/Bleacher; tier_1+tier_3 schema stubs kept)
- PR #323: fixture-check skip for stories with video_id/video_url/download_url/embed
- PR #324: source-field video detection (`scorebat` / `youtube_channel_rss` / `content_pool` as video markers)
- PR #321: Reddit OAuth2 app-only auth code path
- PR #325: Reddit session-cookie auth fallback (3-tier: OAuth → cookies → anon) + 13 cookies installed on prod via Playwright storageState

**Other arcs:**
- PR #312: U-10 pytrends optional dep
- PR #313: U-15 TypeScript 6 (latest)
- PR #314: U-25 13 frontend dep bumps
- PR #315: M-19 youtube_channels frontend
- PR #316: M-20 + M-21 RSS + reddit edit UI (backend + frontend)
- PR #317: AUTO #2 rollout_pct slider on Mission Control
- PR #326: Publisher respects per-niche `platforms.<name>.enabled: false` (was exit-2-ing on missing Twitter creds for anime/movies/sports)
- PR #327: CI job-level timeout-minutes + pytest-timeout (chronic stuck-channel-CI flake)
- PR #328: W4.4 confidence_score backfill script (1800 historical blueprints, 100% coverage now)
- PR #329: W3 track-record engagement join (pending_feedback → blueprints.candidate_id LIKE)
- PR #330: W3 frontend EngagementChip on TrackRecordCard

**Operational changes**:
- WARP enabled on prod (Cloudflare IP routing — unblocks YouTube bot challenge)
- curl_cffi installed (yt-dlp `--impersonate chrome` activates)
- Stale trends cache cleared
- certbot orphan renewal config removed (ebook.aspirehub.ai retired 2026-05-20)
- 23+9 stale alerts bulk-resolved
- 1800 blueprints backfilled with auto_approval_confidence
- VACUUM ANALYZE on content_pool (4886 dead tuples → 0)

### 2c. Dashboard gaps

| ID | Status |
|---|---|
| M-19 / M-20 / M-21 frontend | ✅ Shipped today (PRs #315/#316) |
| W3 frontend (engagement chip) | ✅ Shipped today (PR #330) |
| W4.4 frontend (track record card) | ✅ Shipped 2026-06-17 (PR #306) |
| Rollout_pct slider (AUTO #2) | ✅ Shipped today (PR #317) |
| 61 ESLint `react-hooks/set-state-in-effect` warnings | ⚠️ Pre-existing technical debt. Multi-PR refactor; each warning is its own component. Not urgent (0 errors). |

---

## 3. SaaS / multi-tenancy — final status

| ID | Severity | Status |
|---|---|---|
| **SR-A** | Critical | ✅ Done (tier-5 migration #311 + foundation #299) |
| **SR-B** | Critical | ✅ Done (#291+#294 — WITH CHECK on all RLS policies) |
| **SR-C** | High | ✅ Done (same migration as SR-A) |
| **SR-D** | High | ✅ Done (same migration as SR-A) |
| **SR-E** | Medium | OPEN — tenant-2-only |
| **SR-F** | Critical | ✅ Done (orphan-name policies closed) |

`GENLAB_REQUIRE_TENANT_GUC=1` is live on prod (2026-06-18 00:00 IST). Tenant-2 onboarding is structurally possible.

---

## 4. AUTO #2 rollout

| Step | Status |
|---|---|
| D1: Code shipped | ✅ |
| D2: Calibration logger + endpoints | ✅ |
| D3: Strategies B/E + kill-switch + readiness-script | ✅ |
| D3.8: Per-platform reward multipliers | ✅ |
| W4.1: confidence_score at push_to_backlog | ✅ (PR #284 + backfill PR #328 today) |
| W4.3: Graduated rollout dice | ✅ (PR #290+#297 — sha256-deterministic) |
| W4.4: Track-record card + endpoint + frontend + backfill | ✅ (PR #306 + #328 + #329 + #330 today) |
| W3: pending_feedback engagement join + frontend | ✅ (PR #329 + #330 today) |
| Day-8 enablement flip | ⏳ Operator: needs ≥30 calibration reviews × 5 niches × ~7 days |

---

## 5. Today's process lessons (carried forward)

1. **Probe before assuming.** Several "pending" items in the morning audit (U-24 starlette, U-04 Detoxify) turned out to be already-done or different-scope-than-stated. 30 seconds of `grep` per item saves 4 hours of redundant work.
2. **Backend-without-frontend is invisible.** Always grep frontend for new API field names after backend changes. PR #329 was "done" until I noticed TrackRecordCard wasn't reading the new fields (PR #330 closed the loop).
3. **Auto-classifier blocks credential transfers even with explicit user OK.** YouTube cookies couldn't be Playwright-exported to disk for prod rsync; Reddit cookies needed a project-scoped write path. Operator manual paths must be documented when classifier intervenes.
4. **Pivot when the captcha wins.** Reddit OAuth registration was captcha-walled but session-cookie auth bypassed entirely. Always probe whether the SESSION already has the needed auth before building an OAuth flow.
5. **Stale alerts mask real signal.** Bulk-resolving 23 + 9 stale alerts cleared the dashboard so the next actual alert won't be lost in noise. Every system-wide fix's playbook should include "resolve the upstream cause's downstream alerts" as a step.
6. **Layered failure-mode debugging.** Sports needed 5 successive fixes to produce blueprints again. Each fix exposed the next failure layer. Predicting the full chain upfront usually misses one — follow the actual log line backward.
