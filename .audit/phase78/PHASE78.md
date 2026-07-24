# Phase 7.8 — Reconcile, Re-score AUTOMATION

**Findings:** 75 (8C / 22H / 28M / 14I / 3L). New: **F-0074 HIGH** (AUTOMATION 4→3), **F-0075 MEDIUM** (movies content-starved).

## 1. Exact per-channel decomposition — reconciles to 58/82

| niche | reels_a | posts_a | plat_a | gap | from_reels | from_plat |
|---|--:|--:|--:|--:|--:|--:|
| movies | 1 | 4 | **4.00** | 24 | **24** | **0** |
| ai_c | 4 | 13 | 3.25 | 15 | 12 | 3 |
| anime | 4 | 13 | 3.25 | 15 | 12 | 3 |
| gaming | 5 | 14 | 2.80 | 14 | 8 | 6 |
| sports | 5 | 14 | 2.80 | 14 | 8 | 6 |
| **total** | 19 | **58** | — | **82** | **64** | **18** |

Sums verified. **Movies achieves perfect 4/4 platform coverage** on the 1 reel it produces; its entire gap is missing reels. Rev 7's "movies content-acquisition ~30/wk" was arithmetically impossible (mandate = 28/wk).

## 2. Achievable percentage — real number

**Defect fixes recover EXACTLY 18 posts/wk** (missing_platforms matches defect-cluster count precisely: gaming 6 + sports 6 + ai_c 3 + anime 3). **Achievable = 58 + 18 = 76/140 = 54%** from defect fixes alone — NOT 85% as Rev 7 claimed. Adding Anthropic auto-reload may recover ~5–10 more reels worth (LLM outages block scoring): **65% realistic ceiling.**

## 3. Approval-gated vs content-starved, per channel

| niche | bp_created 14d | published | archived_unapproved | stuck_drafted | VR_unapproved |
|---|--:|--:|--:|--:|--:|
| ai_c | 13 | 3 | **5** | 0 | 0 |
| anime | 12 | 4 | 3 | 2 | 0 |
| gaming | 19 | 7 | 4 | 0 | **3** |
| **movies** | **6** | 3 | 1 | 2 | 0 |
| sports | 19 | 8 | **6** | 1 | 0 |

**Only movies is content-starved** (6/14 target). Other four create at 86–136% of target — their reels shortfall is **approval-gated** (19 archived-unapproved in 14d = operator manually approves and skips/rejects the rest).

## 4. AUTOMATION 4 → 3

**B.1 verified:** auto-approver 3d journal, all fires (21:30 + 22:00 across days) show `examined=0` on every niche. Stable, not a timestamp artifact. gaming/movies/anime `disabled=True` by policy; ai_c/sports `disabled=False` but rollout_pct=0.1 + min_confidence=0.85 keep examined=0.

**B.2 honest 30-day-without-operator answer, per channel:**

| Channel | Day 1 | Day 5 | Day 30 |
|---|---|---|---|
| gaming | STOPS (needs manual approval) | stopped | stopped |
| movies | STOPS (needs manual approval) | stopped | stopped |
| anime | STOPS (needs manual approval) | stopped | stopped |
| ai_c | continues @ ~10% (rollout_pct) | Anthropic empties → stops | stopped |
| sports | continues @ ~10% (rollout_pct) | Anthropic empties → stops | stopped |

**Nothing survives 30 days.** What GenLab IS: autonomous at content generation (writing/scoring/rendering), **human-gated at publishing on 3 of 5 channels**, non-autonomous at billing (F-0053). AUTOMATION 4 was scored on the implicit claim "loop runs itself." That claim is false. **Score honestly moves 4 → 3.** F-0074 filed. SCORECARD updated.

## 5. Movies: content-starved, three options

sources.yaml has 8 configured sources (TMDB + 6 RSS + 2 YouTube). Pipeline journal returned no fetch/scored/rejected/empty lines in 14d — either logs elsewhere or pipeline silent-failing at fetch. Diagnosis of *which* cause requires deploy-and-observe (F-0075). **Options (do not decide):** (a) expand sources — same copyright class; (b) lower scoring threshold — quality cost; (c) reduce mandate 7→3 reels/wk (denominator 140→124, achievable becomes 61%); (d) pause channel on content-supply grounds — different rationale from Phase 7.5's withdrawn survival-pause. **Not a genlab-core issue** — the other four niches create at 86–136% of target; content starvation is movies-specific.

## Operator tasks — no re-analysis

Tasks 1–4 all **NOT DONE.** Task 1 (Anthropic auto-reload) is the cheapest post in the entire gap: one console click enables the 30-day AUTOMATION survival window on the two auto niches.

All shells exited. Only `.audit/` writes made this session.
