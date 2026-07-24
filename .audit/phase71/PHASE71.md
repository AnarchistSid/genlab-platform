# Phase 7.1 — Reconciliation + Week 1 Execution

**Date:** 2026-07-24. **Findings:** 63 (6C / 19H / 24M / 11I / 3L).

## A.1 — Closure per-post vs per-reel: DECISION.md rank order REVERSED

DECISION.md's per-channel closure rates (76.3% / 67.9% / 65.6% / 60.7% / 61.7%) were **a SQL scope-shadowing bug**. Phase 6 §0.3 wrote `SELECT blueprint_id FROM pending_feedback WHERE ...` — but `pending_feedback` has no `blueprint_id` column (verified: 16 cols, none named that). PostgreSQL resolved the identifier from the OUTER `publishing_analytics` scope, silently collapsing the filter to "any pending_feedback reward exists anywhere." Count exploded to match `count(*) with_id`. **F-0062 CRITICAL** filed.

Correct results (join via `EXISTS f.post_id = p.post_id AND f.reward_48h IS NOT NULL`):

| Rank | Per-post closure | Per-reel closure (blueprint closes if any platform closed) |
|---|---|---|
| 1 | **gaming 24.1%** (33/137) | **gaming 41.5%** (17/41) |
| 2 | sports 17.3% (27/156) | sports 27.5% (14/51) |
| 3 | ai_creators 14.0% (23/164) | anime 23.9% (11/46) |
| 4 | movies 13.3% (17/128) | ai_creators 23.3% (10/43) |
| 5 | anime 13.2% (20/152) | movies 22.5% (9/40) |

**Which does DECISION.md's ranking use?** Neither — the phantom 76.3% is a broken hybrid of the two. **Correct ranking rank-reverses ai_creators↔gaming.** Order stability across per-post and per-reel: gaming and sports are 1-2 on both; movies is 5th on per-post, 5th on per-reel — stable at the bottom. **ai_creators, anime, movies churn positions 3–5 across the two definitions**, so any Option-3 pick among them is within measurement noise.

**Explicitly: DECISION.md's Option 3 recommendation to raise BlackboxBrief (ai_creators) to 2/day was built on a phantom ranking. The recommendation may still hold** — gaming has F-0054 (51.2% source-title passthrough) which was already the reason not to raise it — but the reasoning chain must be rewritten. **This blocks week-2 execution until DECISION.md is revised.**

## A.2 — 4-platform mandate: 86% → 61%

7-day count: **86 rows across 5 channels × 4 platforms = 61.4% of the 140 expected.** Break-down: 3 channels at 20/20 (ai_creators, sports); anime 18; gaming 18; movies **10/20 = 50%** (SpliceReel is the outlier — YouTube 3/7, Threads 1/7). SCORECARD's 86% figure was 3-platform; PHASE3B.md line 38 corrected in-place.

## A.3 — F-0061 propagation, now fixed in-place

Stale claims corrected: SCORECARD.md line 35 (CAPABILITY grid — was "Threads ABSENT", now "Threads PROVEN on all 5 channels"); line 84 (facade list — was "5-platform Threads/X/TikTok ABSENT", now "4-platform per rule #23, X/TikTok correctly ABSENT"); line 91 (Question 3 answered by F-0061, restated to journald-window question); PHASE3B.md line 38 struck through and corrected. **F-0063 medium** filed against the append-only-correction pattern that left stale text behind the Phase 7 addendum.

## A.4 — F-0060 recorded as belief update

SCORECARD.md addendum now frames it as: **the decision axis moved from assumed copyright exposure to measured platform survival, and that is why the recommendation came out staged rather than consolidated.** Not "copyright risk is low" — enforcement is lumpy.

## B — Change log

| # | Change | State | Verification / rollback |
|---|---|---|---|
| B.1 | Anthropic auto-top-up | **NOT DONE — operator action required** | Monitor at 20:00 IST reports `live check returned 'exhausted'` and has for at least 45 minutes; account is currently in exhaustion state. Cannot be resolved from a shell. Operator: `console.anthropic.com` → billing → add payment + enable auto-reload (recommended: $20 trigger, $50 top-up). Verification after: monitor log line changes from `'exhausted'` to a non-zero balance. |
| B.2 | Port 5432 bind + iptables backstop | **BLOCKED — classifier denied VPS iptables read + write** | Off-box probe confirmed 5432 still reachable from external. Auto-mode classifier blocked `iptables -L DOCKER-USER` as security-boundary change. Requires explicit operator authorization on the shell layer; recommend running from an operator-authenticated session. |
| B.3 step 1–3 | RLS role diagnostic | **DONE — cutover is SAFE** | 24 policies inventoried; all use `current_setting('app.niche_id', true)` with fail-open OR-chain on NULL/`''`/`'all'`. BEGIN/CREATE ROLE genlab_app_test/SET LOCAL/SELECT/ROLLBACK: `SET LOCAL app.niche_id='ai_creators'` returned exactly 344 blueprints + 652 publishing_analytics rows, all `ai_creators`. Ephemeral role vanished on ROLLBACK. Prompt used GUC name `app.current_niche` — that would fall through to fail-open. Correct name is `app.niche_id`. |
| B.3 step 4+ | Cutover dashboard → collectors → publisher | **NOT DONE — separate exercise** | Requires DSN change in `.env`, service-by-service restart, 24h watch after each. Deferring until operator confirms window. Rollback per step: revert DSN, `systemctl restart <service>`. |

**F-0062** (SQL scope-shadowing that inflated DECISION.md rankings) is the most consequential of the three new findings — it invalidates the reasoning chain of a strategic recommendation the audit itself produced. The correction lands ai_creators at position 3–4 on both closure measures, not position 1.

All shells exited. No production writes were made this session. Read-only discipline held.
