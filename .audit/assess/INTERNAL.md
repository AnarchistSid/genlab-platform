# GenLab — Internal Assessment (2026-07-26 IST)

Read-only against prod. Current numbers only.

## A.1 INTELLIGENCE — learning, not decorative

**Arms moving:** 71–77 arms per niche, 99–100% updated in last 30d. Not static.

**Reward velocity 30d (rewards/day):** ai_c 2.70, gaming 2.07, sports 2.03, anime 1.80, movies 1.53. Aggregate ~10/day across 5 niches. Matches audit's ~2/niche/day figure.

**Reward signal is discriminating**, not flat:

| niche | mean | max | sd |
|---|--:|--:|--:|
| ai_c | 0.086 | 1.000 | 0.197 |
| anime | 0.089 | 1.000 | 0.182 |
| movies | 0.087 | 0.581 | 0.134 |
| gaming | 0.057 | 0.502 | 0.107 |
| sports | 0.044 | 0.572 | 0.108 |

Max is 6–20× the mean; SD 0.11–0.20. Bandit has real signal to learn from.

**ML ceiling: zero neural refs in `genlab-core/src/genlab_core/learning/`.** Only threshold constants are `MIN_OBS=15` in `publishing/cross_platform_gate.py` (publish-side, not ML-upgrade). Neural-LinUCB documented in the roadmap; nothing implemented. INTELLIGENCE ceiling stays capped at plain LinUCB + Thompson.

**Verdict:** learning is real, closure is real, cap is real.

## A.2 AUTOMATION — the 30-day survival test

Per-niche auto-approve config on prod (verified via `grep enabled|rollout_pct|min_confidence`):

| niche | enabled | rollout_pct | min_confidence | 30-day-alone verdict |
|---|:-:|:-:|:-:|---|
| ai_creators (BlackboxBrief) | **true** | 1.0 | 0.70 | continues at ~100% until Anthropic empties |
| sports (ClutchWire) | **true** | 1.0 | 0.70 | continues at ~100% until Anthropic empties |
| anime (FrameDrift) | **false** | — | — | **stops day 1** — manual approval required |
| movies (SpliceReel) | **false** | — | — | **stops day 1** — manual approval required |
| gaming (CriticalRush) | *(no auto_publish block found)* | — | — | **stops day 1** — no config = no auto |

**Anthropic still `exhausted`** — monitor at 13:45 IST 2026-07-26 reports `live check returned 'exhausted'` with `matches_found: 2`. Session 8 of un-done escalation.

**Honest 30-day answer if operator vanishes today:** 3/5 channels stop the same day (manual gate). ai_c + sports continue until the Anthropic balance empties — with the balance already exhausted, they stop **today, not day 30**. **Nothing survives.**

## A.3 CONTENT GENERATION — variety collapsed

100 posts, last 30d:
- **26/100 distinct hooks** (previous audit: 33/100)
- **16/100 distinct opening tokens** (previous: 33/33 opening uniqueness)
- **25 posts open with "Why"** — one token accounts for 25% of the sample
- Caption len: 111–291 chars, mean 218 (target 150–200; running long)

Per-niche opening uniqueness: ai_c 5/27, anime 4/22, gaming 3/19, **movies 1/8** (all 8 posts open with the same token), sports 6/24.

**Passthrough (hook contains source-title substring, or vice versa, length ≥ 12) 30d:**

| niche | passthrough | total | % |
|---|--:|--:|--:|
| ai_creators | 14 | 29 | **48.3** |
| gaming | 19 | 40 | 47.5 |
| movies | 11 | 25 | 44.0 |
| sports | 9 | 25 | 36.0 |
| anime | 10 | 32 | 31.3 |

**MATERIAL MOVE from Phase 7.7's F-0054 finding (gaming 51.2%, ai_creators 0/43).** ai_creators regressed from 0% → 48% in ~2 days. Every niche now sits at 31–48%. The gaming-specific defect is now systemic. Movies opening-token collapse to 1 corroborates: the writers have stopped generating.

**Likely root cause:** Anthropic exhaustion — LLM stages are failing upstream; the writer falls through to source-title verbatim. Aligns with A.2.

## A.4 OWN QUALITY — operator rejection rate 30d

| niche | rejected | created | % |
|---|--:|--:|--:|
| anime | 33 | 57 | **57.9** |
| ai_creators | 23 | 49 | 46.9 |
| movies | 16 | 36 | 44.4 |
| gaming | 19 | 47 | 40.4 |
| sports | 14 | 37 | 37.8 |

**Prior audit baseline was 26% aggregate (Phase 7.7). Current is 38–58%.** Aligns with A.3's diversity/passthrough collapse: the writer is producing more content the operator finds unfit. The Anthropic-outage attribution explains all three signals (A.2 + A.3 + A.4) simultaneously.

**Bottom line:** intelligence is healthy; automation and content quality are both currently degraded by the same upstream failure (Anthropic balance exhausted for 8+ sessions). Fixing that one console click likely returns A.3 + A.4 to Phase 7's baselines. Until it lands, further assessment is measuring the outage, not the system.
