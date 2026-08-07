# QB-FIX-09 C2 — Aug 10 Cadence Decision Inputs

**Date:** 2026-08-07 16:00 IST
**Purpose:** assemble the numbers. **No recommendation.** The trade-offs are operator-side.

## What the Aug 10 gate actually answers

The gate answers exactly **one** question: do the F1–F3d–V3–V4–Y0–A1 fixes hold in production over 48 hours across 4 posts + collateral?

The gate does NOT answer:
- Whether to raise cadence
- Whether to flip `rollout_pct: 0.1` on movies/anime specifically
- Whether the freshness lever (C1's approver walk) should change

Keep those decisions separate in the write-up. Aug 10 authorizes a fix-verification-passed state; every cadence question is downstream and independent.

## The measurements C0/B1/C1 assembled

| axis | ai_creators | anime | gaming | movies | sports |
|------|-------------|-------|--------|--------|--------|
| **create rate 14d** | 32 | 19 | 18 | 23 | 16 |
| **publish rate 14d (reels)** | 12 | 2 | 0 | 2 | 5 |
| **net queue delta 14d** | +20 | +17 | +18 | +21 | +11 |
| **create/publish ratio** | 2.7× | 9.5× | ∞ | 11.5× | 3.2× |
| **median age at publish (h)** | 87.8 | 152.5 | 194.6 | 23.6 (F4) | 169.3 |
| **approver segment share** | 100% | 99.6% | 99.9% | 92% (F4) | 99.9% |
| **current rollout_pct** | 1.0 | 0.0 | (unknown) | 0.0 | 1.0 |
| **auto_publish.enabled** | true | false | (unknown) | false | true |

Movies' 23.6h median is F4 discipline; it will regress to the multi-day pattern once F4 winds down. Do not let it inform the decision.

## Cadence options with numbers attached

### Option 1 — Hold at 1/day

- Backlog delta continues at +11 to +21/niche per fortnight
- Median age at publish stays 3.7–8.1 days
- Freshness continues degrading indefinitely
- **Risk added:** zero
- **Freshness change:** none (baseline)

### Option 2 — Flip movies + anime to `rollout_pct: 0.1`

The originally planned Aug 10 step.

**What 0.1 actually does:** the auto-approver applies its evaluation to only 10% of eligible blueprints (per CLAUDE.md AUTO #2 rollout ladder). With movies producing 23 blueprints in 14 days = 1.6/day, `rollout_pct=0.1` allows auto_approver to consider ~0.16 blueprints/day (roughly 1 every 6 days). The current publish rate (2 in 14 days) is already ~0.14/day, so **rollout_pct=0.1 barely changes anything on movies**.

Same math for anime: 19/14d = 1.4/day, × 0.1 = 0.14/day. Publish rate is 2/14d = 0.14/day. **Movement: essentially zero.**

Net effect: cadence UNCHANGED on movies + anime. The rollout ladder's 0.1 is a safety step, not a throughput lever.

**Risk added:** low — auto-approver takes over selection which had been F4 manual; freshness will regress from movies' 23.6h to whatever the approver picks (probably 3–5 day median).
**Freshness change:** WORSE (movies regresses from 1d to ~3–5d as auto-approver takes over the slot).

### Option 3 — Raise `daily_cap` on proven channels

Not currently spec'd but is the most direct freshness lever per C1.

**Predicted effect on ai_creators** (current: cap=1, 87.7h approver lag):
- cap=2 → approver lag ~44h (half the forward-walk length)
- cap=3 → approver lag ~29h
- cap=4 → approver lag ~22h — where it starts saturating (create rate is ~2.3/day, so cap=3 already matches supply)

**Same math for sports** (create rate 1.14/day):
- cap=2 → approver lag ~85h (half of 169)
- cap=3 → saturates

**Backlog change** (per niche, 14d):
- cap=2: publish rate doubles to ~2/day; delta shifts from +20 to +6 (ai_creators)
- cap=3: publish rate ~3/day; delta shifts to −8 (ai_creators shrinks queue)

**Risk added:** MEDIUM — more posts/day on ai_creators + sports means:
- More fingerprint surface (see §4.4 below)
- 2× the operator moderation load per niche
- Existing platform daily-cap-per-niche of 1 in platform_caps.yaml must be raised too
- 2–3 posts/day per channel matches "modest cadence" Shorts benchmarks per §4.3

**Freshness change:** MATERIALLY BETTER (halves or thirds the approver lag).

### Option 4 — Cap production instead

Match create rate to publish rate: reduce fetcher yield or dedup more aggressively.

**Predicted effect:**
- ai_creators: reduce create from 2.3/day to 1/day → net delta 0, backlog stops growing
- anime: reduce from 1.4/day to 1/day → similar
- Median age at publish approaches the fetcher segment (11–24h) + minimal approver lag

**Risk added:** LOW-MEDIUM — discards candidates the scoring stage may have ranked highly; reduces bandit exploration surface.
**Freshness change:** MATERIALLY BETTER (matches supply to demand; approver walk shrinks to ~1 day).

### Option 5 — Slot-reshuffle on higher-scoring fresh approval

C1 Follow-up option. Add reshuffling logic to `_pick_next_available_slot()`: when a fresh candidate scores above the head of the queue, push the queue back a day and put the fresh one first.

**Predicted effect:** freshest content publishes first; older content stays scheduled but drifts back.
**Risk added:** LOW — pure ordering change, doesn't affect throughput.
**Freshness change:** BETTER for the top of the queue; WORSE for the tail (may age out beyond the 7-day ceiling and get silently deferred).

### Option combinations

Options 3 + 4 combine cleanly (cap production AND raise publish cap; queue stays flat, freshness minimized).

Options 3 + 5 combine cleanly (raise cap AND reshuffle; maximum freshness leverage).

Option 2 (rollout_pct 0.1) is independent of all others; it governs which niches auto_approver considers, not throughput.

## The unmeasured constraint (§4.3)

**No evidence about what cadence the platforms reward.** Section 1.2 benchmarks concern retention + engagement per post, NOT posting frequency. General industry guidance ("2–3 Shorts/day roughly triples growth velocity") is not a measurement of GenLab's specific niches or audiences.

**F-QB-0801 (per-metric outcome persistence) is a prerequisite for evaluating any cadence change.** Without it, raising cadence and observing "more views" won't tell you whether the added posts drove the increase or dilution effects offset a per-post drop.

Reward-path work is:
- NOT a prerequisite for MAKING a cadence decision
- IS a prerequisite for LEARNING from it

Flag this before shipping any cadence change. The decision can be made without it; the outcome cannot be evaluated without it.

## The copyright interaction (§4.4)

**More posts on SpliceReel and FrameDrift = more fingerprint surface.**

- F3d-4 established there is still no wire from any platform's audio-claim response into `compliance_events`
- QB-FIX-03 W3 SpliceReel decision remains OPEN (Option 1 continue is default, advised against per Screen Culture / KH Studio precedent)
- Any cadence increase on those two channels compounds an unmonitored exposure

**Upstream dependencies for Option 3 on movies+anime:**
- QB-FIX-03 W3 SpliceReel Option decision made first (Option 2b, 3, or 4)
- Same analysis applied to FrameDrift
- OR accept the unmonitored exposure explicitly

Options 3 and 4 on ai_creators + sports are lower-risk since those niches carry fair-use commentary (ai_creators) or league-fingerprinted-but-tolerated content (sports).

## Decision axes for the operator

Split the Aug 10 decision into 3 independent questions:

**Q1 — do the fixes hold?**
Answered by the Aug 10 watch. Yes/no. Independent of cadence.

**Q2 — flip movies + anime to rollout_pct 0.1?**
Numeric answer: nearly no effect (2× per-day movement on niches producing 1.4–1.6/day is negligible). Real effect is: auto_approver takes over slot assignment from F4, movies+anime freshness regresses from 1d to 3–5d.
Recommend addressing SEPARATELY from cadence.

**Q3 — cadence lever?**
Real freshness lever is per C1: raise `daily_cap` (Option 3) or cap production (Option 4) or both.
Upstream dependency: W3 SpliceReel decision if raising cadence on movies. F-QB-0801 reward persistence if wanting to learn from the change. Neither is a hard blocker; both are recommended.

## Summary

| option | throughput | freshness | risk | dependencies |
|--------|-----------|-----------|------|--------------|
| 1 Hold 1/day | 1/day | 3.7–8.1d (steady) | none | none |
| 2 rollout_pct 0.1 (mov+ani) | ~unchanged | movies ↑ to 3–5d | low | none |
| 3 daily_cap ↑ to 2–3 | 2–3/day | 1–2d | medium | W3 for mov+ani; F-QB-0801 for evaluation |
| 4 Cap production | 1/day (matched) | 1d | low | operator-side score triage acceptance |
| 3+4 combined | 2–3/day matched | 0.5–1d | medium | same |
| 5 Slot reshuffle | 1/day | best top, worse tail | low | none |

## No recommendation

The trade-offs are operator-side. This memo assembles the numbers; the choice belongs to the Aug 10 decision.

## Filed

`.audit/QB-FIX-09/C2_cadence_options_memo.md` (this file).

## Commit

`docs(strategy): cadence decision inputs for Aug 10 gate`
