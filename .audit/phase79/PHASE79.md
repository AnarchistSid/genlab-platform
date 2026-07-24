# Phase 7.9 — Where the 64 Actually Sits

**Findings:** 76 (8C / 23H / 28M / 14I / 3L). New: **F-0076 HIGH** — three ceilings, rollout_pct claim reversed.

## 1. posts_lost_to_reels sums to 64 ✓

| niche | reels_pub | reels_short | posts_lost |
|---|--:|--:|--:|
| movies | 1 | 6 | 24 |
| ai_c | 4 | 3 | 12 |
| anime | 4 | 3 | 12 |
| gaming | 5 | 2 | 8 |
| sports | 5 | 2 | 8 |
| **total** | 19 | 16 | **64** |

## 2. created_not_published — the approval-gate size

| niche | bp_created 7d | bp_published | not_pub |
|---|--:|--:|--:|
| ai_c | 8 | 4 | 4 |
| gaming | 9 | 5 | 4 |
| sports | 9 | 5 | 4 |
| movies | 3 | 1 | 2 |
| anime | 5 | 4 | 1 |
| **total** | 34 | 19 | **15** |

**Creation is basically AT target** (34 vs 35). Only movies (3/7) and anime (5/7) are content-undersupplied; ai_c/gaming/sports overproduce. **The shortfall is publishing yield, not creation.** State of the 15 unpublished: 6 VISUAL_READY approved (in queue), 3 VR unapproved (gaming waiting for operator), 4 DRAFTED (movies+anime stuck upstream), 9 archived unapproved (operator quality-declined). Approval-gate bucket = ~12 blueprints × 4 platforms = **~48 posts/wk theoretical** if the auto-approver caught them or operator raised throughput.

## 3. Manual, not throttled

**B.1 hour-of-day:** 56 approvals concentrated at hour **06:00 IST** across all 5 niches; scattered 5–7 at other hours. Auto-approver fires every 30 min uniformly — this pattern is a human doing morning batch approvals.

**B.2 time-to-approve avg (14d):** anime 151.3h (6.3 days), gaming 86.8h (3.6d), ai_c 83.2h (3.5d), sports 76.5h (3.2d), movies 40.0h (1.7d). Blueprints wait 1.7–6.3 days before operator sees them.

## 4. Ramp WORKED. Disabled is policy, not drift.

`genlab-auto2-ramp.service`: ExecMainExitTimestamp 2026-07-20 15:03 IST, Status=0. Journal empty (F-0056 vacuumed) but exit-code succeeded. **BlackboxBrief (ai_c) + ClutchWire (sports) have `rollout_pct: 1.0` in current prod config** — ramp brought them from 0.1 → 1.0 over 4 weeks. **Phase 7.7's rollout_pct=0.1 claim was stale.**

Gate on ai_c + sports is now `min_confidence: 0.70`. Gate on FrameDrift (anime), SpliceReel (movies), CriticalRush (gaming) is `enabled: false` — a **CLAUDE.md product decision** ("ai_creators only until calibration proves other niches ready"), not a rollout that failed. auto2-ramp is not decorative.

## 5. Three ceilings

| Path | Recovery | Mandate % | Type |
|---|--:|--:|---|
| 1. Platform/defect fixes | +18/wk | **54%** | **code** |
| 2. + lower `min_confidence` on 2 auto niches | +~24/wk | **~71%** | **config** (YAML edit) |
| 3. + enable auto-approve on 3 manual niches | +~12/wk | **~80%** | **product decision** |
| 4. Movies + anime content-supply | +up to 24/wk | **~97%** | content decision |

**Path 2 does not require a product call** — just lower the confidence threshold on the two channels the operator already trusts machine-approval on. **Path 3 is a product decision** about whether machine-approved content should publish unreviewed on channels currently requiring human review. Path 4 is F-0075's movies/anime content expansion.

Rev 7's "65% engineering cap" was pessimistic by ~15 points because it did not distinguish config from code within the engineering path. **Corrected engineering ceiling: ~71% via config edit alone.**

## Operator tasks — status only, one dependency

All four items in `.audit/OPERATOR_TASKS.md` still **NOT DONE**. Task 1 (Anthropic auto-reload) is now a **prerequisite** for Path 2+: raising approval throughput against an empty LLM balance recovers nothing (F-0053 blocks the upstream scoring stage).

All shells exited. Only `.audit/` writes made this session. **Rev 7 is done. Next action is deploy or decision.**
