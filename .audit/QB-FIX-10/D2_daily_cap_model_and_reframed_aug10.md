# QB-FIX-10 D2 — daily_cap Lever Model + Reframed Aug 10 Decision Set

**Date:** 2026-08-07 16:10 IST
**Purpose:** modelling only. No config change. Supersedes QB-FIX-09 C2's cadence-options memo with corrected framing.

## 4.1 Current setting

**One config, uniform across all 5 niches, at `genlab-core/config/platform_caps.yaml`:**

```yaml
daily_post_cap:
  instagram: 1
  youtube: 1
  facebook: 1
  tiktok: 1
  twitter: 1
  threads: 1

multi_publish:
  enabled: false       # default OFF — R-09 "1 reel per channel per day" guarantee holds
  platforms: []

max_per_day_ceiling:  # only takes effect when multi_publish enabled AND optimal_time_learner has confidence
  instagram: 3
  youtube: 2
  facebook: 3
  tiktok: 3
  twitter: 5
  threads: 5
```

**Per-niche overrides:** none. Grep across all 5 channel `publishing.yaml` files returns zero `daily_post_cap` overrides. All niches inherit the global.

**Single-lever:** the approver's `_pick_next_available_slot()` and the publisher's `DailyCapEnforcer` both read the same `daily_post_cap` value. One config edit changes both.

## 4.2 Projection at cap 1 / 2 / 3

Using B1's create rates (14-day observed) and C1's decomposition:

### Model assumptions

- Approver's `_pick_next_available_slot()` walks IST days forward from `now+1h`, first-fit-forward against `daily_cap=N`
- Each auto-approval consumes one slot on the first day with headroom
- At steady state with approval rate A/day: median approver lag ≈ `queue_depth_ahead / N` days
- Publisher's `DailyCapEnforcer` uses the same value → publish rate matches cap when supply exists

### Per-niche projections

| niche | create rate/day (B1) | current cap=1 approver lag (C1) | cap=2 projected approver lag | cap=3 projected approver lag | net queue delta/14d at cap=2 | net queue delta/14d at cap=3 |
|-------|----------------------|---------------------------------|------------------------------|------------------------------|-----------------------------|-----------------------------|
| ai_creators | 2.29 | 87.7h (3.7d) | ~44h (1.8d) | ~29h (1.2d) | +4 (still growing slowly) | -10 (queue drains) |
| anime | 1.36 | 151.9h (6.3d) | ~76h (3.2d) | saturates (~24h) | -9 (drains) | -23 (drains fast) |
| gaming | 1.29 | 194.4h (8.1d) | ~97h (4.0d) | saturates | -10 (drains) | -24 (drains) |
| movies | 1.64 | 21.8h F4-artifact | (regresses to ~50h once F4 winds) | (regresses to ~30h) | -5 | -19 |
| sports | 1.14 | 169.1h (7.0d) | ~85h (3.5d) | saturates | -12 (drains fast) | -26 (drains) |

### Arithmetic

For each niche:
- `net_delta_14d(cap=N) = 14 × (create_rate - min(create_rate, N))`
- At cap=1: `net_delta = 14 × (create - 1)` for any create > 1
- At cap=2: `net_delta = 14 × (create - 2)` for create > 2, else `14 × (create - 2)` negative → drains
- At cap=3: `net_delta = 14 × (create - 3)` — negative for all niches, all drain

At cap=2, ai_creators is the only niche where net_delta stays positive (0.29/day × 14 = +4). Every other niche drains. At cap=3, ai_creators also drains (-10).

**Median approver lag at cap=N** ≈ `queue_depth / N` days at steady state. Under bursty arrivals + same-pass collision guard, lag is somewhat higher but within 2× of this floor.

**Movies at 11.5× ratio would need cap ≈ 12 to fully drain** — but that's against a 14-day snapshot including the F4 batch's manual approvals. Under normal auto-approver flow, movies' create rate is closer to anime's (~1.5/day), and cap=2 saturates.

### Concrete cap=2 outcome

If cap raised from 1 to 2 tomorrow across all 5 niches:
- ai_creators: near-saturated (queue grows slowly, ~1 more row per 3.5 days)
- anime + gaming + sports: queue drains within 2-3 weeks
- movies: post-F4 windows, drains
- Median freshness at publish: 1-4 days (down from 3.7-8.1)

If cap raised to 3:
- All niches drain
- Median freshness: 1-2 days
- Effective 3× throughput increase per niche

## 4.3 What the projection cannot tell you

Three limits, per prompt §4.3:

### The 1/day cap is a product constraint, not an accident

CLAUDE.md rule 10: "Never publish more than 1 reel per channel per day" — non-negotiable. `platform_caps.yaml` comment: "The cap is a guarantee, not a soft target."

Raising `daily_post_cap` is not solely a throughput knob — it's a **product decision about what these channels are**. Are they "one editorial reel per day" channels (current product), or "3 reels per day matching Shorts benchmark" channels? Those are different products with different audience expectations and different content-quality bars per post.

### Without per-metric outcome persistence (F-QB-0801), a cadence change is unmeasurable

F-QB-0801 established there is no per-post outcome persistence (which post drove which metric change). Raising cap and observing "more views" cannot distinguish:
- More posts → more views (throughput win)
- More posts → same total views, split across more posts (per-post dilution, no growth)
- More posts → algorithmic-penalty-for-volume (net negative)

Reward-path work is:
- NOT a prerequisite for MAKING a cadence change
- IS a prerequisite for LEARNING from it (evaluating whether the change helped)

**Additional interaction:** Neural-LinUCB observation threshold depends on post volume. Higher cadence → faster time-to-maturity for arm posteriors. This cuts the OTHER way — cadence increase helps the learning loop's data density, even before the reward-path fix.

### More posts on SpliceReel and FrameDrift compounds unmonitored fingerprint exposure

F3d-4 established no wire from any platform's audio-claim response into `compliance_events`. Cadence increase on movies + anime doubles or triples fingerprint surface without corresponding observability.

**Upstream dependencies for cap raise on movies + anime:**
- QB-FIX-03 W3 SpliceReel Option decision must be made first (Option 2b, 3, or 4)
- Same analysis applied to FrameDrift
- OR the operator accepts the unmonitored exposure explicitly

Cap raise on ai_creators + sports carries lower risk (fair-use commentary; league-fingerprinted-but-tolerated).

## 4.4 Reframed Aug 10 decision set

Supersedes QB-FIX-09 C2's cadence-options table. Three independent decisions:

### Q1 — Do the fixes hold in production?

**What the gate ACTUALLY authorises.** Answered by the Aug 10 watch check (`4b39af35`). Yes/no.

Independent of everything else. This is the fix-verification gate the whole cycle has been building toward.

### Q2 — What governance change, if any, should `rollout_pct` carry?

The originally planned Aug 10 action was `rollout_pct: 0.1` on movies and anime. **C2 established this has near-zero throughput effect** (0.14 vs 0.16 approvals/day — noise-level). What it actually does is shift SLOT ASSIGNMENT from F4's manual (23.6h median lag for movies) to auto_approver_v1's default (predicted 3-5 days median lag once F4 winds down).

Governance decision:
- **Keep rollout_pct: 0.0** — movies + anime remain manual-approval-only. Freshness stays whatever operator picks. Throughput stays ≤ what F4-style batches produce (currently 2-3 per 14d).
- **Flip rollout_pct: 0.1** — auto_approver takes over. Freshness regresses. Throughput stays similar. Governance shifts from operator to auto-approver. (This is what the ladder step is FOR — it starts calibration data flow so the operator can measure agreement over ~30 days before flipping to 0.25.)

Q2 is orthogonal to Q3.

### Q3 — Cadence lever?

Real freshness lever is `daily_post_cap`, NOT `rollout_pct`. Per §4.2:

- **Cap=1 (current):** freshness 3.7-8.1d, backlog growing on all niches
- **Cap=2:** freshness 1.8-4d, queue drains on 4-of-5 niches
- **Cap=3:** freshness 1.2-2d, queue drains on all 5

Upstream dependencies:
- W3 SpliceReel decision before raising on movies
- Same-analysis-for-anime before raising on anime
- F-QB-0801 reward-path work before wanting to EVALUATE the change

Q3 is orthogonal to Q1 and Q2. Can be answered with any combination of the other two.

### Gaming — separate

Gaming has zero renders in 14 days AND its slot picker (per Y1) chose 15:30 IST which the daily publisher timer at 06:35 UTC never reaches. **Cadence is irrelevant to a channel that has never published under normal automation.** Gaming needs its own session covering Y1's (c) defect AND F-QB-0002 (whether zero MP4s is a fetcher, download, or render problem).

## Summary table (decision set for Aug 10)

| decision | scope | current | option A | option B |
|----------|-------|---------|----------|----------|
| Q1 fixes hold? | verification gate | (Aug 10 watch answers) | proceed | investigate any incidents |
| Q2 rollout_pct | movies + anime | 0.0 (manual) | keep 0.0 | flip 0.1 (governance shift, ~0 throughput) |
| Q3 daily_cap | all 5 niches | 1 uniform | keep 1 | raise 2 or 3 (freshness lever, real throughput) |
| gaming | separate | dark | separate session post-Aug-10 | separate session post-Aug-10 |

Any of the 4 combinations of Q2 × Q3 is coherent. Aug 10's fix-verification (Q1) is a prerequisite for both.

## No recommendation

Trade-offs are operator-side. This memo assembles the numbers; the choice belongs to the Aug 10 decision.

## Commit

`docs(strategy): model daily_cap lever and reframe Aug 10 decision set`
