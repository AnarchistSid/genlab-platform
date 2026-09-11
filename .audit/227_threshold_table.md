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
**0.715 → 22/30 clear** (not 14/30 as tabulated at 0.85).

History is not rewritten; this note is the correction of record.

### P3 reinterpretation

At 0.715, roughly three-quarters of ai_creators' 30 rendered blueprints cleared
the threshold, yet only 8 were approved. **ai_creators' narrowing was the
one-reel-per-niche-per-day cap and the approval queue — not the threshold.**

The threshold explanation stands unchanged for the other three: sports (0.986)
and movies (1.0) against maxima of 0.870 and 0.891, and anime (1.0) against
0.862 — all unreachable by construction.
