# Sprint 30 Report — 2026-03-11

**Theme:** Verify content is live · Debug dashboard · Twitter decision · Architecture review

## Prime Directive 1: Is content actually publishing?

**Status: YES (with caveats)**

- Publisher launchd daemon runs daily at 19:30 IST (was TEST_MODE)
- Stale-file filter fix committed (`0a2ef72`) — `_has_local_video_file()` replaces `os.path.exists()`
- 2026-03-08 live publish: IG 5/5, X 5/5, YT 0/5 (quota), FB 0/5 (token), TikTok 0/0 (skipped), Threads 0/0 (skipped)
- YouTube upload quota exhausted (resets daily) — persistent blocker
- Facebook token issue from 2026-03-08 — resolved (EAA page token refreshed 2026-03-09)

## Prime Directive 2: Dashboard approval workflow

**Status: FIXED AND VERIFIED END-TO-END**

**Root cause:** `run_async()` async bridge deadlocks under gunicorn's eventlet worker (un-greened RLocks + asyncio conflict).

**Fix:** Replaced all async MS Graph SDK calls with synchronous Graph REST API calls via `requests` (properly monkey-patched by eventlet).

| Operation | Before | After |
|-----------|--------|-------|
| Queue list | Infinite hang (deadlock) | 1.1s (33 items) |
| Approve | Deadlock | 0.87s |
| Hold | Deadlock | 0.98s |
| Release | HTTP 400 | 0.95s |

**Changes committed:** `b82987b` (5 files, 281 insertions)
- `server/core/publishing_queue.py` — sync Graph REST API (`_fetch_blueprints_sync`, `_update_blueprint_sync`)
- `server/api/publishing_queue.py` — niche_id="all" default, lite transform
- `server/api/blueprints.py` — `_transform_media(lite=True)` skip expensive ops
- `frontend/src/hooks/use-publishing-queue.ts` — pass niche_id="all" through
- `runbooks/review_server_wrapper.sh` — full gunicorn path for launchd

## TEST_MODE Retirement

**Status: DONE**

- Removed `TEST_MODE=true` from publisher launchd plist
- Changed schedule: 1x/day 19:30 IST → 2x/day 12:00 + 20:00 IST (matches `publishing.yaml` schedule_slots)
- Production code path requires `action_taken=approved` (dashboard) + `scheduled_for` (pipeline)
- First production-mode run triggered at 16:02 IST — processing normally

## Track A: Twitter $200/mo Decision

**Status: DOCUMENTED, AWAITING HUMAN DECISION**

See `docs/plans/2026-03-11-twitter-cost-decision.md`. Recommendation: disable Twitter (free tier insufficient, $200/mo low ROI).

## Track B: Bandit Observation Count

**Status: 0 observations across all niches**

- 292 `collect_metrics` runs all complete instantly (0s)
- No PendingFeedbackTasks exist (no successful publishes yet)
- Hook classifier needs MIN_EXAMPLES=200 (weeks of data accumulation needed)
- Bandit system is correctly wired but starved of data

## Track C: CW/SR/FD Publishing Audit

**Status: NONE CAN PUBLISH**

Blockers per niche:
- `use_live_publishing: false` in all configs
- Zero platform credentials configured
- Null account IDs across all platforms
- Incomplete `publishing.yaml` (missing platform sections)
- No per-niche credential switching in publisher (hardcoded `ai_creators`)
- FrameDrift `niche_id` still says `anime` in some configs

## Track D: Postiz Fly.io

**Status: NOT STARTED (requires human action)**

Postiz Docker Compose evaluated but not deployed. `POSTIZ_SHADOW_MODE=true` in publisher plist.

## Track E: SaaS Readiness

**Status: SCORE 10/100 — single-tenant only**

Critical gaps:
- No multi-tenancy (niche-based, not user/org-based)
- HTTP Basic Auth only (no OAuth/OIDC/JWT/RBAC)
- SharePoint as sole data backend (no SQL DB)
- macOS LaunchAgent deployment only (no containerization)
- No billing/metering infrastructure
- No observability stack (local logs only)
- Plaintext env vars (no secrets management)

Current architecture is production-ready for **single user/organization** but requires complete overhaul for SaaS.

## Commits This Sprint

| Hash | Repo | Description |
|------|------|-------------|
| `0a2ef72` | Content Scraper | fix: stale-file filter uses `_has_local_video_file()` |
| `b82987b` | Dashboard | fix: replace async Graph SDK with sync REST API for queue |

## What's Next (Sprint 31)

1. **Twitter decision** — commit the config change based on human decision
2. **Approve first production publish** — use dashboard to approve a VISUAL_READY blueprint, verify it publishes at next scheduled slot
3. **CW/SR/FD credentials** — start populating platform credentials for at least one additional niche
4. **Bandit bootstrapping** — first successful publishes will seed PendingFeedbackTasks
