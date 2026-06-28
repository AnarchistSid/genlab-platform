# Pending work — refresh as of 2026-06-19 EOD (evening session)

Snapshot after a **second wave** of work on 2026-06-19 (the evening codebase-review + refactor arc).
Supersedes `PENDING-AS-OF-2026-06-19.md` (which captured the morning's 40-PR arc up to ~14:20 IST).

The day split into **three named arcs**:

1. **Morning bug-fix arc (#358 → #359 → #360)** — "CR producing useless content" investigation. Three silent-drops at gaming pipeline integration seams: FetchGamingStories REPLACE-not-MERGE → schema-normalize → FilterGamingStories trust list. **Proof one-liner**: `[FILTER] 20 → 5 stories (rejected: 0)` post-deploy vs `rejected: 7` before. Bug class fixed at the operator-visible layer.

2. **Evening codebase-review arc (v1 + v2)** — exhaustive 8-subsystem audit. Found doc drift (dashboard is Flask not FastAPI), 5× duplication in platform clients (~3,200 LOC across IG/FB/YT/X/Threads with no shared base), 3 modules >1500 LOC, and a 12-item prioritized refactor list.

3. **Evening refactor arc (#361 → #362 → #363 → #364)** — shipped 4 PRs from the review:
   - **#361 (P9)** — `GENLAB_DOMAIN` strict-mode flag prevents silent attribution loss
   - **#362 (P1 phase 1)** — typed `StoryCandidate` + `merge_stories`/`replace_stories` helpers + `FetcherStage` mixin + `collect_emitted_sources` producer registry. Migrated FetchTwitchClips + FetchSteamTrailers. **Closes the silent-drop bug *class*** (today's 3-PR arc becomes structurally impossible going forward).
   - **#363 (P2 phase 0)** — `BasePlatformClient` ABC foundation. Migration follow-up PRs are documented in `docs/MIGRATION-platform-clients.md`.
   - **#364 (P1 phase 2)** — migrated 4 more fetchers (FetchRedditClips, FetchScoreBatHighlights, FetchTMDBTrailers, FetchAnimePromos) to the producer registry pattern.

## TL;DR

| Bucket | At morning EOD | Evening Δ | Now |
|---|---|---|---|
| **Engineering-actionable** | 0 explicit | +0 closed today (refactors are quality work, not bug-class blockers) | 0 explicit; the 7-item review backlog is now well-scoped |
| **Operator-blocked** | 6 (5 affiliate creds + AUTO #2 Day-8) | 0 closed | 6 (unchanged) |
| **Architectural debt (newly catalogued)** | n/a | +12 items from v2 review, 4 closed | **8 open** (P3 P4 P5 P6 P7 P10 + 5 P2 migrations + 2 P1 follow-ups) |
| **Today's surfaced silent-failure classes** | 1 (gaming "useless content") | 0 new | Both **structurally closed** via #361/#362 |

**6 PRs shipped this evening alone, total 10 PRs across the day.**

---

## 1. Operator-blocked (unchanged from morning)

| Item | What's needed |
|---|---|
| AUTO #2 Day-8 calibration | ≥7 days of operator review clicks accumulated in `auto_approval_calibration` table; first niche to clear `ready_for_enforcement` threshold (90% agreement on 30+ samples) triggers per-niche `auto_publish.enabled: true` flip in `publishing.yaml` |
| Amazon PA-API signup | Operator tax ID, signup at `affiliate-program.amazon.in/assoc_credentials` |
| Impact.com signup | Operator W-9 + tax info, signup at `app.impact.com` |
| ShareASale signup | Per-program approval flow at `shareasale.com` |
| CJ Affiliate signup | Per-program approval at `cj.com` |
| Twitter Developer content policy approval | Currently held; recurring blocker |

## 2. Architectural debt from this evening's v2 review

**Shipped** (4 of 12):
- ✅ **P1** typed StoryCandidate + producer registry (PR #362 + phase-2 #364)
- ✅ **P2 phase 0** BasePlatformClient ABC foundation (PR #363)
- ✅ **P9** GENLAB_DOMAIN strict-mode flag (PR #361)

**Probe-corrected** (2 of 12) — review findings turned out to be wrong:
- ❌ **P11** "delete duplicate `cdn_upload.py`" — NOT a duplicate. `publishing/cdn_upload.py` (38 LOC) is a class-based adapter wrapping `platforms/cdn_upload.py` (267 LOC) implementation. Both actively imported.
- ❌ **P3+P12** "dedupe `platform_adaptation.py` ceremony × 4" — the 4 channel files exist BECAUSE pipeline_runner requires no-arg `__init__` and the base class takes positional `niche_id`. Removing them = restructure contract = cost > savings.

**Genuinely-open** (6 of 12):

| Pri | Item | LOE | Notes |
|---|---|---|---|
| P2 phase 1+ | Migrate 5 platform clients to `BasePlatformClient` | 1-2h each × 5 = 5-10h | One PR per client per `docs/MIGRATION-platform-clients.md`. Start with IG (smallest, well-tested). |
| P1 phase 3 | Migrate `FetchTrendingVideos` + `FetchGamingStories` to FetcherStage | 3-4h | Larger fetchers. Once done, `_LEGACY_HARDCODED_SOURCES` in `filter_gaming_stories.py` goes empty and producer registry becomes sole source of truth. |
| P4 | Centralize YAML backbone (`pipeline_template:` + per-niche `inject:` deltas) | 3-4h | Adding shared stage becomes 1-line not 5-edit (the AffiliateMatch arc had to touch 5 YAML files). |
| P5 | Split `health_monitor.py` (1990 LOC) + `push_to_backlog.py` (1791) + `metric_collector.py` (1524) into per-concern modules | 6h | All three have clear natural splits (24 check_* fns; 14 helpers + 1 class; 6 per-platform `_fetch_*` + reward + tasks). |
| P6 | Per-stage `fail_mode: continue \| abort` YAML key | 2h | Makes silent-failure class opt-in. Fetchers default continue, render/write default abort. |
| P7 | Promote `bb_strategies/_scoring.py` (1211 LOC) into genlab-core base | 6h | Reduces channel-package weight; the 1211 LOC is suspect-shaped for a "thin subclass" channel pkg. |
| P10 | TikTok stub + multi_team JWT TODO | varies | Either implement TikTok publishing (currently 31 LOC stub) or remove from supported list. JWT decode at `middleware/auth.py:27` is a TODO. |

## 3. Multi-day ML work (deferred — needs offline AUC + online shadow)

| Item | State |
|---|---|
| **W3.3 Layer 3** — pending_feedback enrichment | foundation in `hook_embeddings.py` from PR #348 |
| **W3.3 Layer 4** — online shadow scoring | needs Layer 3 first |
| **W3.3 Layer 2** | ✅ DONE (PR #348) |

## 4. Today's surfaced silent-failure classes — BOTH closed structurally

| Class | Tactical fix (morning) | Architectural fix (evening) | Result |
|---|---|---|---|
| Silent integration drops at stage seams | PR #358 (merge), PR #359 (schema), PR #360 (trust list) | **PR #362 + #364** — typed StoryCandidate + producer registry | Class impossible going forward |
| Silent monetization attribution loss | Operator set GENLAB_DOMAIN on prod | **PR #361** — strict-mode flag raises if env var ever unset | Class impossible going forward |

## 5. Doc drift caught

- CLAUDE.md says dashboard is FastAPI; it's actually Flask (`dashboard/server/review_server.py:82: app = Flask(__name__)`). Onboarding engineers reach for wrong patterns.
- The previous `PENDING-AS-OF-2026-06-19.md` claimed "0 engineering-actionable items remaining" — this was correct for bug-class work but obscured the 7 architectural items the evening review surfaced.

## What the next session should pick up

The single highest-leverage remaining item is **P5** (split the 3 big modules). It's mechanical, no behavior change, and dramatically improves code navigation across `metric_collector.py` (6 per-platform fetchers obvious), `health_monitor.py` (24 check_* fns), and `push_to_backlog.py` (14 helpers + 1 class).

After P5: **P2 phase 1** (migrate 5 platform clients to BasePlatformClient) is the next leverage win — it kills the 5× duplication identified in the review.

Either of those is a 2-4 PR session. Both together is 1-2 days of focused work.
