# QB-FIX-04 X0-a — Pre-Fix Blueprint Sweep Report

**Date:** 2026-08-06 22:35 IST
**Trigger:** ai_creators Gemini Robotics 2 blueprint (`668e113c`) discovered to be a 5-day-old pre-fix render scheduled for 2026-08-07 publish. Investigation revealed 17 other approved pre-fix rows across ai_creators (9) and gaming (8) — a 7-day queue of pre-fix ai_creators publishes plus a stuck gaming cohort.

## Enumeration

Pre-fix rows (created < 2026-08-06) in `VISUAL_READY` or `DRAFTED`:

| niche | status | n | with_affiliate_url | oldest | newest |
|-------|--------|---|--------------------|--------|--------|
| ai_creators | VISUAL_READY | 11 | 0 | 2026-07-28 | 2026-08-05 |
| anime | DRAFTED | 6 | 0 | 2026-07-31 | 2026-08-04 |
| gaming | DRAFTED | 2 | 0 | 2026-08-04 | 2026-08-05 |
| gaming | VISUAL_READY | 10 | 0 | 2026-07-28 | 2026-08-05 |
| movies | DRAFTED | 5 | **4** | 2026-08-01 | 2026-08-05 |
| movies | VISUAL_READY | 1 | **1** | 2026-07-30 | 2026-07-30 |
| sports | DRAFTED | 4 | 0 | 2026-07-31 | 2026-08-04 |

Total: 39 pre-fix rows. **5 movies rows carry affiliate_url** — all in DRAFTED or unapproved VISUAL_READY, so not shipping today, but would ship the pre-F1 CTA baked into caption if ever promoted.

## Approved pre-fix (17 rows — the immediate danger)

- **ai_creators — 9 approved:**
  - 2 by `nightly_scheduler`, scheduled Jul 29/30 (past, missed slots)
  - 7 by `auto_approver_v1` with **FUTURE scheduled_for**: Aug 7 → Aug 13
  - Pattern: **the ai_creators queue was pre-loaded with 7 consecutive days of pre-fix reels**
- **gaming — 8 approved:**
  - All by `nightly_scheduler`, all with PAST scheduled_for (Jul 29 → Aug 6)
  - Titles: "Dark and Darker", "Dead by Daylight", "League of Legends" (×2), "Fortnite", "Rust", "Overwatch", "Mistfall Hunter"
  - This is the F-QB-0606 bare-title cohort. Also corroborates X1's Option (c) suspicion: the 15:30 IST scheduler slot never actually publishes — 8 approved rows sat stale for 1-9 days.

Zero approved pre-fix on movies + anime — F4 session's cleanup on movies held; anime never had auto-approver enrollment.

## Archive execution (X0-a Step 2)

```sql
UPDATE blueprints
SET status = 'ARCHIVED',
    action_taken_source = 'auto_archived_qb_fix_04_pre_fix'
WHERE niche_id = <each>
  AND status IN ('VISUAL_READY', 'DRAFTED')
  AND created_at < '2026-08-06'::timestamptz;
```

Per-niche row counts affected:
- ai_creators: **11 archived**
- anime: **6 archived**
- gaming: **12 archived**
- movies: **6 archived**
- sports: **held** (X2 diagnostic exception per §2)

**Total: 35 non-sports rows archived.**

## Verification (X0-a Step 3)

```sql
SELECT niche_id, status, COUNT(*) FROM blueprints
WHERE status IN ('VISUAL_READY', 'DRAFTED')
  AND created_at < '2026-08-06'::timestamptz
GROUP BY niche_id, status;
```

Result: only 4 sports DRAFTED remain. Queue clean.

## Dedup consequence — REAL DEFECT FOUND

Per §2 Step 3 audit: verify ARCHIVED is treated as inactive by dedup.

**It is not.** `video_id_dedup.is_blocking()` at line 58-91 has TWO independent gates:

1. `status in LIVE_OR_PENDING` → does NOT include ARCHIVED ✓
2. `action_taken='approved' AND scheduled_for populated` → **triggers on ARCHIVED rows too**

Rule 2 was added 2026-07-06 to catch commitment-to-publish before status migrates (task #525). It has no ARCHIVED short-circuit. My 17 archived-approved-scheduled rows continued to lock their URLs even though the status flipped.

**Fixed for X0-a's rows:** nulled `scheduled_for` on the 17 archived rows. Verified: 0 rows in my archive set still trigger rule 2.

```sql
UPDATE blueprints SET scheduled_for = NULL
WHERE action_taken_source = 'auto_archived_qb_fix_04_pre_fix'
  AND scheduled_for IS NOT NULL;
-- UPDATE 17
```

**Broader finding — NOT acted on in this pass:**

99 additional ARCHIVED rows across all niches (ai_creators 38, gaming 15, movies 20, sports 18, anime 8) still carry `approved + scheduled_for` from prior archives. They contribute phantom dedup blocking. The audit surface is stable but should be either:
- (a) Same treatment: null `scheduled_for` on all ARCHIVED-approved-scheduled rows. One SQL statement. Safe.
- (b) Code fix: add ARCHIVED short-circuit to `is_blocking()` — ARCHIVED is terminal, no code path publishes an ARCHIVED row, so rule 2 should not fire on it.

Recommend both (b as primary, a as backfill for correctness). Filed as follow-up. Neither shipped in X0-a per operating-rule constraint on scope.

## Meta-finding (class-of-bug)

This is the same class as ME-11 (aggregates hiding composition defects): status is one input to `is_blocking()`, but rule 2 bypasses status entirely. An "archive" operation as most operators would understand it — remove from active consideration — did not fully take effect because a *different* input (scheduled_for) still fired the block. Detection heuristic: any composite predicate with N independent OR-gates needs an audit for what values on each gate cause user-observed behavior to diverge from user-intended semantics.

## Watch implications (per §4)

- Tomorrow (Aug 7 12:05 IST) the ai_creators auto-approver has no VISUAL_READY-approved candidate left. It may auto-approve a fresh candidate if X0-b's ai_creators pipeline run produces one. If not, ai_creators does not publish tomorrow.
- Movies + anime F4 batch 1 posts are unaffected — they were created 2026-08-06 (post-fix) and pass the created_at cutoff.
- Sports: 4 DRAFTED preserved for X2 diagnostic.

## Row-level list (approved pre-fix that were archived)

```
ai_creators / b87b319f "AI can't READ this"                   / created 2026-07-28 / sched 2026-07-29
ai_creators / f88c9e79 "Taste the Hooney Sauce Jingle"        / created 2026-07-28 / sched 2026-07-30
ai_creators / 668e113c "Advanced dexterity with Gemini R2"    / created 2026-08-01 / sched 2026-08-07  ← the trigger
ai_creators / 696eab43 "Google Just Unveiled Its Most..."     / created 2026-08-01 / sched 2026-08-08
ai_creators / 550daeb8 "NVIDIA's AI Learns Why Copying..."    / created 2026-08-03 / sched 2026-08-09
ai_creators / c841a086 "Can you still trust Reddit?"          / created 2026-08-05 / sched 2026-08-10
ai_creators / 8ce02a08 "Meet Birding Pal"                     / created 2026-08-05 / sched 2026-08-11
ai_creators / be29b51f "The Best AI Short Film You'll See..." / created 2026-08-04 / sched 2026-08-12
ai_creators / d8300037 "Another DeepSeek Moment Has Arrived"  / created 2026-08-04 / sched 2026-08-13
gaming      / 8f8f4c95 "Dark and Darker"                      / created 2026-07-28 / sched 2026-07-29
gaming      / b95ed779 "Dead by Daylight"                     / created 2026-07-29 / sched 2026-07-30
gaming      / a8ba7b3a "League of Legends"                    / created 2026-07-30 / sched 2026-07-31
gaming      / 356d6015 "Mistfall Hunter"                      / created 2026-08-01 / sched 2026-08-02
gaming      / 86113235 "Fortnite"                             / created 2026-08-01 / sched 2026-08-05
gaming      / 2b0764d0 "Rust"                                 / created 2026-08-02 / sched 2026-08-03
gaming      / 020341a3 "Overwatch"                            / created 2026-08-03 / sched 2026-08-04
gaming      / 76854a00 "League of Legends"                    / created 2026-08-05 / sched 2026-08-06
```

## Commit

`chore(backlog): archive pre-2026-08-06 blueprints carrying pre-fix renders`
