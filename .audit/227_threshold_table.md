# #227 — threshold table for the operator's value decision
**Read-only. No values applied.** 14-day rendered set, n=101, every blueprint
carries an `auto_approval_confidence`.

## Method

Primary: **largest adjacent gap** in each niche's sorted scores.
Cross-check: **1-D k-means, k=2**, initialised at min/max.
**Both methods produce the identical split on all five niches** — not just one,
as asked. Bimodality threshold: widest gap ≥ 0.10; all five qualify
(0.181–0.346), so no niche falls back to p25.

## The table

| niche | n | low-cluster max | main-cluster min | gap | midpoint | **proposed (clamped)** | clears @ proposed | low excluded | clears @ committed | clears @ tuner |
|---|---|---|---|---|---|---|---|---|---|---|
| ai_creators | 30 | 0.472 | 0.791 | 0.319 | 0.632 | **0.650** | 24/30 | 6 | 14/30 (0.85) | — |
| anime | 13 | 0.431 | 0.612 | 0.181 | 0.522 | **0.650** | 7/13 | 5 | 2/13 (0.85) | — |
| gaming | 23 | 0.385 | 0.731 | 0.346 | 0.558 | **0.650** | 15/23 | 8 | 3/23 (0.85) | — |
| movies | 24 | 0.391 | 0.682 | 0.291 | 0.537 | **0.650** | 18/24 | 6 | 3/24 (0.85) | **0/24** (0.925) |
| sports | 11 | 0.457 | 0.789 | 0.332 | 0.623 | **0.650** | 9/11 | 2 | 9/11 (0.732) | **0/11** (0.896) |

## Two things the table shows that the rule did not anticipate

**1. The [0.65, 0.80] clamp is binding on every niche.** Every midpoint falls
*below* 0.65 (0.522–0.632), so all five clamp to the floor and the rule returns
**the same value for all five niches**. The per-niche tailoring it intends does
not occur. Either the floor is wrong for this data, or the midpoint is the wrong
statistic.

**2. The tuner's own suggested values clear nothing.** movies 0.925 → **0/24**
of the rendered set; sports 0.896 → **0/11**. Its "achievable ceiling" is p90 of
*gate-approved* confidence, which is evidently a different and higher-scoring
population than the rendered set — see A.5. Its suggestions are still above
every score the pipeline actually produced, so they would remain off switches.

## An alternative statistic, for comparison only

If the intent is "admit the main cluster, exclude the low cluster," the tighter
statistic is **just below main-cluster min**, not the midpoint:

| niche | main-cluster min | alt proposal (min − 0.01) | clears | low excluded |
|---|---|---|---|---|
| ai_creators | 0.791 | 0.781 | 24/30 | 6/6 |
| anime | 0.612 | 0.602 | 7/13 | 5/5 |
| gaming | 0.731 | 0.721 | 15/23 | 8/8 |
| movies | 0.682 | 0.672 | 18/24 | 6/6 |
| sports | 0.789 | 0.779 | 9/11 | 2/2 |

Identical admit counts to the clamped midpoint, identical exclusions, but
**per-niche** and derived from the data rather than from a floor. Offered as a
comparison, not a recommendation — the choice is the operator's.

## Not touched

R-08 approval hardening, the safety gate, the strict video policy. Only the
confidence-prediction threshold is in scope. Nothing applied; both tuner timers
remain disabled.

---

## Correction, 2026-09-11 (OPS-19 §1)

**BlackboxBrief's active `min_confidence` was `0.715` (line 131), not `0.85`.**
The 0.85 cited for ai_creators throughout OPS-14 §5's drift table, OPS-15 §B's
revert verification, and this file's "clears @ committed" column came from a
**commented-out line at line 22**. Both sides of the "live == committed, no
drift" comparison read the same comment.

Corrected admit count for ai_creators at its true pre-change threshold:
**0.715 → 24/30 clear** (not 14/30 as tabulated at 0.85).

MEASURED, not interpolated. The first draft of this correction said 22/30 by
eyeballing between the 0.65 and 0.85 columns; that was an inference and it was
wrong. The table's own cluster bounds settle it: ai_creators' scores are bimodal
with low-cluster max **0.472** and main-cluster min **0.791**, and *nothing lies
between them*. 0.715 falls inside that empty gap, so it admits exactly the
main cluster — the same 24 that 0.65 admits.

### Consequence: 1b66168e is a no-op for two niches

Both thresholds sit in the same gap, so for ai_creators the change moves the
admit set not at all. The same holds for sports: committed 0.732 also lies
inside its gap (0.457 → 0.789), clearing 9/11 either way — as the table's own
"clears @ committed" column already recorded.

| niche | pre-change (ACTIVE, verified at 1b66168e^) | clears before | clears at 0.65 | moved? |
|---|---|---|---|---|
| ai_creators | 0.715 (line 131) | 24/30 | 24/30 | **no — same gap** |
| sports | 0.732 (line 98) | 9/11 | 9/11 | **no — same gap** |
| gaming | 0.85 (line 122) | 3/23 | 15/23 | yes, +12 |
| movies | 0.85 (line 88) | 3/24 | 18/24 | yes, +15 |
| anime | 0.85 (line 86) | 2/13 | 7/13 | yes, +5 |

Verified by reading the ACTIVE (non-comment) line at `1b66168e^` for all five
files: **only BlackboxBrief carried the comment defect**; gaming, movies and
anime were genuinely 0.85 and sports genuinely 0.732.

### Correction to this correction (OPS-20): two legs, not one

The paragraph originally here said sports approving 0 would not be evidence of
failure. That was over-broad — it generalised a Q2-only exclusion into a blanket
exemption, and it is wrong for Q1.

Each niche moved in **two legs**, and they answer different questions:

| niche | leg 1 — parity revert | leg 2 — 1b66168e | in Q1? | in Q2? |
|---|---|---|---|---|
| sports | **0.986 → 0.732** (large) | 0.732 → 0.65 (no-op, same gap) | **yes** | no |
| movies | **1.0 → 0.85** (large) | 0.85 → 0.65 (+15) | **yes** | **yes** |
| anime | **1.0 → 0.85** (large) | 0.85 → 0.65 (+5) | **yes** | **yes** |
| gaming | 0.85 (unchanged) | 0.85 → 0.65 (+12) | **yes** | **yes** |
| ai_creators | 0.715 (unchanged) | 0.715 → 0.65 (no-op, same gap) | **yes** | no |

**Q1 — is approval unblocked at all?** Scope: **all five niches**. The relevant
comparison is against the 14-day state, in which the tuner had driven sports to
0.986 and movies/anime to 1.0 — thresholds above every score the pipeline
produced, i.e. off switches. Sports approving 0 while candidates clear 0.65 is a
**failure of something**, and the log must name what.

**Q2 — did 0.65 specifically admit more than 0.85 would have?** Scope: **gaming,
movies, anime only**. ai_creators and sports are excluded by construction,
because 0.715 and 0.732 sit inside their own empty gaps and admit the identical
set at 0.65.

The no-op finding above is sound; what was wrong was treating it as an exemption
from Q1. A niche can be excluded from Q2 and still be a hard failure under Q1.

History is not rewritten; this note is the correction of record.

### P3 reinterpretation

At 0.715, roughly three-quarters of ai_creators' 30 rendered blueprints cleared
the threshold, yet only 8 were approved. **ai_creators' narrowing was the
one-reel-per-niche-per-day cap and the approval queue — not the threshold.**

The threshold explanation stands unchanged for the other three: sports (0.986)
and movies (1.0) against maxima of 0.870 and 0.891, and anime (1.0) against
0.862 — all unreachable by construction.

---

## OPS-21 — scheduled-job label audit, 2026-09-11 23:20 IST

**Scheduler TZ: Asia/Kolkata (+0530), determined by discriminating test.**
Host `/etc/localtime` → `Asia/Kolkata`, `TZ` env unset, and no DST shift on
09-11/12/13 (all +0530). The discriminating evidence is `9a4b93a2`: its cron
names day-of-month **12** while its label says **09-11** 18:55 UTC. Only local
IST interpretation produces that rollover (00:25 IST Sep 12 = 18:55Z Sep 11);
under UTC interpretation the cron would fire 00:25Z Sep 12 and could not carry a
Sep 11 label. Consistency across the other three is corroboration, not proof.

| job | cron (raw) | tz evaluated in | resolved UTC | label claims | match? |
|---|---|---|---|---|---|
| 9a4b93a2 | `25 0 12 9 *` | Asia/Kolkata | **2026-09-11 18:55Z** | 09-11 ~18:55 UTC | ✅ |
| 4157f9c6 | `2 10 12 9 *` | Asia/Kolkata | **2026-09-12 04:32Z** | 09-12 04:32Z | ✅ |
| 58a4b47d | `27 12 12 9 *` | Asia/Kolkata | **2026-09-12 06:57Z** | 09-12 06:57Z | ✅ |
| 6092026c | `25 12 13 9 *` | Asia/Kolkata | **2026-09-13 06:55Z** | 09-13 ~06:55 UTC | ✅ |

**No mismatches. No job replaced.** The only mislabel in the set was `b674da45`,
already retired under OPS-20 §1 and now filed as T-32.

### Order-dependent pairs — MEASURED, not assumed

Upstream durations from the live journal (UTC-forced):

* **auto-approver**: starts :00/:30, exits in **3–24 s** (six consecutive runs
  15:00–17:30 all ≤ 24 s).
* **publisher**: 09-11 06:35Z fire finished **06:39:53** (4m53s); 09-11 12:05Z
  fire finished **12:17:01** (**12m00s**). Duration is variable, 5–12 min.

| pair | requirement | margin | verdict |
|---|---|---|---|
| 4157f9c6 (04:32Z) after ~04:30Z approver pass | approver exits ≤ 04:30:25 | **~95 s** | ✅ |
| 4157f9c6 (04:32Z) before 06:35Z publisher | — | **2h03m** | ✅ |
| 58a4b47d (06:57Z) after 06:35Z publisher | worst observed exit 06:47Z | **10 min** | ✅ |
| 6092026c (06:55Z) after 06:35Z publisher | worst observed exit 06:47Z | **8 min** | ✅ |

**Contention: none.** `58a4b47d` fires 09-**12**, `6092026c` fires 09-**13** —
different days, not two minutes apart. No fold-in required; `6092026c` stands.

**Residual risk, named:** the publisher's 5→12 min spread is two samples. A
06:35Z fire running >20 min would put both 06:55Z and 06:57Z reads mid-run.
Mitigated, not eliminated: both jobs gate on `ExecMainExitTimestamp` at STEP 0,
so that case reports "not yet run" rather than a false zero — which is the whole
point of the three-meanings gate.
