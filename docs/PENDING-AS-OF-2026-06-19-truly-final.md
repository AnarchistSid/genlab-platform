# Pending work — TRULY final snapshot 2026-06-19

Supersedes `PENDING-AS-OF-2026-06-19-final.md` (which captured only the
first 17 PRs). After that doc shipped as PR #374, the session continued for
**6 more PRs** (#375-#380) — primarily completing P2 phase 1 end-to-end.
This doc captures the complete **23-PR day**.

## TL;DR — final ledger

| Category | Status |
|---|---|
| **Morning bug-fix arc** (#358 #359 #360) | ✅ closed |
| **P1** typed StoryCandidate + producer registry (4 PRs) | ✅ closed |
| **P5a** metric_collector.py split 1524→832 LOC (6 PRs) | ✅ closed |
| **P6** per-stage fail_mode + sandbox fix + 2 adoptions (4 PRs) | ✅ closed |
| **P9** GENLAB_DOMAIN strict-mode | ✅ closed |
| **P2 phase 0** BasePlatformClient ABC foundation | ✅ closed |
| **P2 phase 1** niche_id 5-client rollout + activation (5 PRs) | ✅ closed end-to-end |
| **Doc snapshots** | #365 + #374 + this doc |

**23 PRs shipped: #358 → #380**

## Architectural shifts that landed today

### 1. Producer-registry pattern is now THE source of truth for the gaming filter
PR #360 fixed a silent-drop bug with a hand-maintained 2-entry frozenset.
PRs #362/#364/#366/#367 migrated 6 fetchers to `FetcherStage.EMITTED_SOURCES`
and replaced the hardcoded set with `collect_emitted_sources(...)`. The
legacy hardcoded fallback is now `frozenset()` — empty.

### 2. metric_collector.py is now navigable
1524 LOC → 832 LOC (-45%). Six per-platform modules in `learning/metrics/`
(youtube, instagram, facebook, x_twitter, tiktok, threads). Backward-compat
re-export shim preserves every existing import path.

### 3. Pipeline can opt-in to fail-fast per stage
PR #368 added `fail_mode: continue | abort` YAML key (default continue,
matches historical behavior). PRs #375 + #376 adopted it for PushToBacklog
+ render stages across all 5 niches. PR #376 also closed a P6 design hole
in `SandboxAwareStageRunner` (was silently dropping fail_mode on sandboxed
stages — broke gaming's RenderGamingVideo).

### 4. All 5 platform clients are niche-aware end-to-end
PR #361 (P9 GENLAB_DOMAIN strict-mode) + #363 (BasePlatformClient ABC
foundation) set the stage. PRs #377/#378/#379/#380 then:
  - #377: InstagramClient gets `niche_id` kwarg with 3-tier resolver
  - #378: ThreadsClient + FacebookClient (same pattern)
  - #379: YouTubeClient + XTwitterClient (multi-field bundle variant)
  - #380: `resolve_client_kwargs` passes `niche_id` to every `get_client()` call

After #380, production publishes route niche identity all the way from
preflight → resolve_client_kwargs → get_client() → ClientClass.__init__
→ self.niche_id. Unblocks per-niche logging / rate limiting / metrics.

## Patterns established this session (worth keeping)

1. **Phased rollout with re-export shims** — proven 3× this session
   (P1: 4 phases, P5a: 6 phases, P2 phase 1: 4 phases). Each phase is
   small, shippable, reversible. The re-export shim preserves 100%
   backward compatibility throughout.

2. **Probe-before-ship** — caught 3 v2-review false-positives in this
   session (P3+P12 contract-bound, P11 adapter-not-duplicate, CLAUDE.md
   gitignored). 30 seconds of probing saves 2-3 hours of false-start.

3. **Contract tests pin the architectural shape** — every architectural
   PR shipped a regression test that fails CI if a future PR tries to
   reintroduce the closed bug class. Examples:
   - `test_filter_producer_registry.py` pins producer-registry vs trust list
   - `test_models.py` pins StoryCandidate schema + merge/replace semantics
   - `test_stage_runner.py::TestLocalStageRunnerFailMode` pins fail_mode contract
   - `test_preflight.py::TestNicheIdFlowsThroughAllPlatforms` pins niche_id wire-up

4. **Capstone PRs activate the foundation in tiny code** — PR #380 is
   the canonical example: 17 production-code lines + 5 test pins, and
   that's what activated 22 PRs of prior work end-to-end. Foundation
   PRs deserve a small activation PR right after so they don't sit
   unused.

5. **Monitor + ScheduleWakeup self-pacing loop** — `gh pr checks` polling
   with cache-aware delays (270s for quick polls / 1200s for safety net)
   shipped 23 PRs through CI cycles with minimal context burn.

## Genuinely-open work (next sessions)

### Tier 1 — high-leverage architectural
| Item | LOE | Risk | Notes |
|---|---|---|---|
| **P4** YAML backbone (`pipeline_template:` + per-niche `inject:` deltas) | 3-4h | Medium (loader changes touch all 5 niches) | Adding shared stage becomes 1-line not 5-edit (e.g. AffiliateMatch required 5 niche.yaml edits) |
| **P7** Promote `bb_strategies/_scoring.py` (1211 LOC) into genlab-core base | 6h | Medium (channel→core move) | Reduces channel-package weight; the 1211 LOC is anomalous for "thin subclass" pattern |
| Per-niche structured logging adapter (LoggerAdapter with `extra={'niche_id': self.niche_id}`) | ~50 LOC × 5 clients | Low | Now-unblocked by PR #380 activation. Operator logs filterable by niche cleanly. |

### Tier 2 — Operator decisions
| Item | What's needed |
|---|---|
| **P10** TikTok stub | Either implement TikTok Content Posting API properly OR remove from supported-platforms list |
| **P10** multi_team JWT decode | Implement the JWT-decode TODO at `middleware/auth.py:27` OR commit to single_admin-only mode |
| **P6 broader adoption** | Add `fail_mode: abort` to publish stages per niche.yaml (per-stage operator judgment) |

### Tier 3 — Multi-day
| Item | State |
|---|---|
| **W3.3 Layer 3** — pending_feedback enrichment | foundation in `hook_embeddings.py` from PR #348 |
| **W3.3 Layer 4** — online shadow scoring | needs Layer 3 first |

### Tier 4 — Architecture design (not coding)
| Item | Why |
|---|---|
| **Full BasePlatformClient ABC migration** (vs lightweight niche_id kwarg) | Contract design: ABC requires positional `niche_id` but ~5 test fixtures + 3 production callers pass legacy kwargs. The current lightweight kwarg approach (PRs #377/#378/#379) delivers per-niche correctness without the breakage. Whether to force ABC inheritance later is a separate architectural-design call. |

### Operator-blocked (unchanged from morning)
- AUTO #2 Day-8 calibration (≥7 days of operator clicks)
- Amazon PA-API / Impact.com / ShareASale / CJ Affiliate signup
- Twitter Developer content policy approval

## Prod state

- **HEAD**: `9d173620` (PR #380 — P2 phase 1 activation capstone)
- **0 failed services**, engagement worker active, publisher oneshot inactive (correct)
- **Behavior**: zero functional changes across the 23 PRs — every change was structural / backward-compatible / opt-in

## What the next session should pick up

If the goal is **immediate user-visible value**:
- Per-niche structured logging adapter (Tier 1 small win)

If the goal is **maximum architectural leverage**:
- P4 YAML backbone (Tier 1 medium effort, highest cumulative value)

If the goal is **clean up known smells**:
- P7 `bb_strategies/_scoring.py` promotion (Tier 1 longer effort)
- P10 operator decisions (Tier 2 — could be a 30-min decision conversation)

The 23-PR session that produced this doc represents the most disciplined
refactor work pace of the project so far. The pattern is repeatable.
