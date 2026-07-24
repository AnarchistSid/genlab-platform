# GenLab Phase 7 — Decision Memo (Rev 7, 2026-07-24)

**Status:** input to a decision the operator makes. Body fully rewritten this revision; the "closure fix vs pause channels" argument of Revs 1–6 is superseded.

## The question — reordered

Should GenLab consolidate channels, raise cadence, or fix its own delivery? Phase 7.6 established there is no closure gap (F-0071 methodology error dissolved four sessions of debate). Phase 7.7 establishes the shortfall the closure debate obscured: **the system publishes at 41.4% of its own mandate.** Going from 58/140 to 140/140 is a **2.4× observation increase with zero strategy change, zero new brand risk, and zero paused channels.** That beats every consolidation option and it uses capacity already built.

## What is stable across all seven revisions

- **Closure ranking:** gaming > sports > ai_creators > movies ≈ anime. Direction is trustworthy.
- **Copyright evidence (F-0060):** 1 REMOVED_BY_META in 120d (sports/FB). The "high copyright risk on 4 channels" prior is not supported.
- **Creative diversity:** 33/33 opening-token uniqueness, zero banned formulations. Gaming's 51.2% source-title passthrough (F-0054) is real and channel-specific.
- **Video-first mandate:** 5/5 sampled reels are 1080×1920 h264+aac with real content.
- **Reward loop is closed:** PF ≈ PA on all four north-star platforms (YT/FB/IG/Threads), 100% reward coverage. Bandit active at 67-68 arms/day updated.

## Recommendation — hit the existing mandate first

**F-0072 (Phase 7.8 correction) decomposes the 82-post gap ALGEBRAICALLY:**

- **18 posts/wk from missing platforms** — exactly matches the named defect clusters (gaming/sports 6 each, ai_c/anime 3 each): Meta code=368 soft-block, IG Container processing, CDN preflight, Layer 4 attribution gate, Threads container/timeout. Defect fixes recover this precisely.
- **64 posts/wk from missing reels** — splits:
  - **Movies 24/wk: content-starved** (6 blueprints/14d vs 14 target). Only movies has this problem; other 4 channels create blueprints at or above target.
  - **Other 4 channels 10/wk: approval-gated** (19 archived-unapproved in 14d = operator manually approves and doesn't reach all).
  - **Remainder ~30: Anthropic-outage days + stochastic** (F-0053 cascade blocks scoring on some days).

**Three ceilings (F-0076, Phase 7.9 corrects Rev 7's 65% cap):**

| Path | Recovery | Mandate % | Type |
|---|--:|--:|---|
| 1. Platform/defect fixes | +18/wk | **54%** | code |
| 2. + lower `min_confidence` on 2 already-enabled auto niches (ai_c, sports) | +~24/wk | **~71%** | **config** (YAML edit) |
| 3. + enable auto-approve on 3 manual niches (gaming, anime, movies) | +~12/wk | **~80%** | **product decision** |
| 4. Movies + anime content-supply expansion | +up to 24/wk | **~97%** | content decision |

Phase 7.7's "rollout_pct=0.1" was stale — current prod state is rollout_pct=**1.0** on ai_c + sports (`auto2-ramp` completed the ramp). The remaining gate on those two is `min_confidence: 0.70`. On gaming/anime/movies the gate is `enabled: false` per CLAUDE.md "ai_creators only until calibration" policy.

**Order matters:** path 1 is engineering hours; path 2 is a YAML edit that does not require a product call; path 3 is a product call about whether machine-approved content should publish unreviewed on channels where a human currently reviews every reel. Path 4 needs a content-side intervention or mandate reduction.

**Movies options (F-0075):** expand sources (copyright exposure same class); lower scoring threshold (quality cost); reduce mandate 7→3 reels/wk (denominator 140→124, achievable becomes 61% baseline); pause channel on content-supply grounds (different rationale from Phase 7.5's withdrawn survival-pause).

**Stop rule (from Phase 7.9):** Rev 7 changes next only when something has SHIPPED. Not when a number is recounted. If the next session is not a deploy or a decision, there should be no next session.

## Why the ordering changed from Rev 6

Rev 6 recommended "fix closure > pause channels." F-0071 revealed there is no closure to fix — the reward loop was always closed at ~100%. That left cadence-or-channels as the framing, but F-0072 shows a third option beats both: **fix the existing mandate first.** Consolidation trades brand-portfolio for observation velocity; hitting the existing mandate gets 2.4× velocity for free.

The audit's own recommendation was chasing a fictional plumbing bug for four sessions while an obvious capacity gap sat in plain sight. That is the meta-lesson worth carrying: always check whether existing capacity is fully used before proposing new capacity or a change of strategy.

## Measurement plan

- **Day 0:** operator flips tasks 1 + 2 (Anthropic + port bind). Baseline observations per arm per niche recorded.
- **Day 30:** re-measure observations per arm per niche. Expect ~2.4× if (a)+(b) landed.
- **Day 30+:** re-open cadence question if velocity is still slow after the free lift.

## What would change the recommendation

- A takedown wave (2+ REMOVED_BY_META on any channel) → up-weight consolidation.
- Movies content acquisition proves un-fixable → drop movies from the 5-channel mandate; that alone converts the 30 posts/wk from "capacity-limited" to "removed from denominator."
- Revenue-per-publish data becomes measurable → the recommendation shifts from convergence velocity to affiliate reward ranking.

## Revision history

| Rev | Date | Change |
|---|---|---|
| 1 | 2026-07-24 | Original: Option 3, raise ai_creators to 2/day, pause SpliceReel + ClutchWire |
| 2 | 7.1 | F-0062 SQL scope-shadow: closure ranking phantom |
| 3 | 7.2 | F-0064 systemic 505 write-gap; pause list withdrawn |
| 4 | 7.3 | F-0064 re-scoped Threads-specific |
| 5 | 7.4 | F-0068 status-filter shape: prior 4 metrics invalidated |
| 6 | 7.5 | F-0066 upgraded CRITICAL as primary bug; instrumentation-first plan |
| 7 | 7.6/7.7 | **F-0071: no closure gap ever existed. F-0072: mandate 41.4% is the real problem.** Body rewritten; recommendation reordered to hit existing mandate first. |

**Read-only measurement has reached its limit.** Every remaining action needs either an operator deploy or a decision. See `.audit/OPERATOR_TASKS.md` for the four blocked items.
