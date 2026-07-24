# Phase 7.7 — Why 41.4%?

**Findings:** 73 (8C / 21H / 27M / 14I / 3L). New: **F-0072 HIGH** (mandate decomposition), **F-0073 MEDIUM** (Twitter scope drift). SCORECARD restructured (Part D). DECISION.md Rev 7 body written fresh.

## 1. Rows-per-publish AND rewards-per-row: both 100%

| Platform | PF rows | reward_48h closed | % |
|---|--:|--:|--:|
| youtube | 157 | 157 | **100.0** |
| facebook | 157 | 157 | **100.0** |
| instagram | 136 | 136 | **100.0** |
| threads | 41 | 41 | **100.0** |
| twitter | 21 | 7 | 33.3 (out of scope) |

Phase 7.6's "~100% coverage" holds for both quantities on the four north-star platforms. Reward loop is complete.

## 2. Reels-per-day vs platforms-per-reel — both short

14d data:

| Niche | Reels | Reels/day | Posts | Platforms/reel |
|---|--:|--:|--:|--:|
| ai_creators | 10 | 0.71 | 33 | **3.30** |
| sports | 11 | 0.79 | 32 | 2.91 |
| gaming | 10 | 0.71 | 28 | 2.80 |
| anime | 10 | 0.71 | 27 | 2.70 |
| **movies** | **8** | **0.57** | **21** | **2.63** |

Multiplicative decomposition: 0.70 (reel factor) × 0.72 (platform factor) = 0.504 = **50.4% of 4-platform mandate on 14d** (matches 41.4% 7d within noise). **BOTH axes short; movies is short on both.**

## 3. B.5 breakdown — recoverability of the 70 posts/week gap

| Bucket | Posts/wk | Named causes |
|---|--:|---|
| **Config-recoverable** | ~13 | Anthropic auto-reload (F-0053, task 1); Twitter denylist per rule #23 (F-0073) |
| **Defect-recoverable** | ~18 | Meta code=368 soft-block (~5); IG Container processing (~5); CDN preflight tmpfiles URL scheme (~3); Layer 4 attribution gate (~5); Threads container/timeout (~3) |
| **Capacity-limited** | ~30 | Movies content acquisition: 6 blueprints in 14d, 2 stuck at DRAFTED (0.43/day vs 1.0 target) |
| Stochastic residue | ~9 | — |

**Named hypothesis DISPROVEN.** `genlab-auto-approver` fired 2026-07-24 22:00 with `examined=0` across all 5 niches: 3/5 (gaming, movies, anime) `disabled=True` per CLAUDE.md "ai_creators only" policy; ai_creators + sports `disabled=False`. **41.4% is NOT auto-approval throttle** — operator is fully in the loop, manually approving ~1 reel/day/channel.

Mandate goes from **41.4% → ~85%** after (config + defect) fixes alone, no strategy change. Movies content-acquisition is the remaining third and needs a content-side fix (or scope reduction).

## 4. SpliceReel bottleneck: content acquisition

`blueprints` 14d for `movies`: 3 PUBLISHED + 2 DRAFTED + 1 ARCHIVED = 6 total. Half a day's worth per day, half of them publishing. Combined with 2.63 platforms/reel = worst-case channel on both funnel dimensions. **Root cause is upstream fetch/scoring, not the publisher** — 2 DRAFTED means no video assigned or scored to publishable threshold. This is F-0072's "capacity-limited ~30 posts/wk" bucket. Fix requires source-list expansion, fetcher tuning, or dropping movies from the mandate.

## 5. DECISION.md Rev 7 body written; SCORECARD sections separated

**DECISION.md Rev 7:** body fully rewritten. Recommendation reordered: **hit the existing mandate first** (2.4× observations for free, no strategy change) before revisiting cadence or channels. The "closure fix > pause channels" argument of Revs 1–6 is superseded (F-0071).

**SCORECARD:** Phase 7.6's "F-0071 is the largest single finding" now split into two lists — largest system findings (funding-decision weight: exposed-superuser chain, mandate 41.4%, Anthropic cascade) vs largest methodology findings (audit-trust weight: F-0071 fictional metric, F-0056 journald retention, F-0030 process leak). Different audiences, different weights.

## Twitter scope resolution (Part E)

Rule #23 = 4-platform focus (YT/FB/IG/Threads). Twitter has 21 publishes in 60d window with 8 recent failures (4x Layer 4 attribution + 4x 402). BlackboxBrief added `platforms.twitter.enabled: false` on 2026-07-23; other niches' denylist state should be audited. **Rule #23 is intent, not enforcement.** **F-0073 filed MEDIUM.**

## Operator tasks status (Part F, no re-analysis)

Tasks 1–4 in `.audit/OPERATOR_TASKS.md` all still **NOT DONE.** Task 1 (Anthropic) is a live dependency of Part B — the 402 cascade contributes to config-recoverable bucket; some portion of 41.4% cannot be measured until balance is fixed.

All shells exited. Only `.audit/` writes made this session.
