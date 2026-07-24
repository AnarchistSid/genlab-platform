# Phase 8 — Results Ledger

**Day 0 baseline: 2026-07-24 22:45 IST.** Day 7 and Day 30 readings added below when reached. Rev 7's argument (2.4× observation velocity) turns on Day 30.

---

## Day 0 baseline

### Mandate — 7d publishes per niche × platform (4 north-star only)

| niche | reels | posts | platforms_per_reel |
|---|--:|--:|--:|
| gaming | 5 | 14 | 2.80 |
| sports | 5 | 14 | 2.80 |
| ai_creators | 4 | 13 | 3.25 |
| anime | 4 | 13 | 3.25 |
| movies | 1 | 4 | 4.00 |
| **total** | **19** | **58** | **3.05** |

**Mandate: 58/140 = 41.4%.** Reels-side deficit: 16 reels short (movies 6, ai_c 3, anime 3, gaming 2, sports 2). Platforms-side deficit: 18 posts (gaming 6, sports 6, ai_c 3, anime 3).

### Archived-by-operator (quality proxy) — 7d

| niche | archived_unapproved |
|---|--:|
| sports | 4 |
| ai_creators | 2 |
| gaming | 2 |
| anime | 1 |
| movies | 0 |
| **total** | **9** |

**Human rejection rate:** 9 archived / 34 created = **26%**. This is the number Action 6's product decision turns on.

### Anthropic 402 count — 7d

| niche | platform | count |
|---|---|--:|
| ai_creators | twitter | 1 |

**Only 1 publishing_analytics row shows 402 in-error-message over 7d** — but the monitor reports `exhausted` at every 15-min fire since Phase 7.1. Discrepancy is because the 402 blocks publishes UPSTREAM (writer stage LLM calls fail → no blueprint → no PA row); the 402 shows in the pipeline logs, not in publishing_analytics. Baseline count for Day-7 comparison: **1 PA-visible 402 / 7d**.

### Observations per arm per niche — 30d (learning velocity proxy)

| niche | total_arms | avg_n | median_n |
|---|--:|--:|--:|
| gaming | 74 | 16.0 | 5.0 |
| sports | 71 | 15.1 | 5.0 |
| ai_creators | 77 | 10.9 | 3.0 |
| movies | 72 | 8.5 | 4.0 |
| anime | 75 | 8.5 | 2.0 |

Median arm has been touched 2–5 times in 30d. **Rev 7's 2.4× claim = median rises to 5–12 within 30d after mandate lifts to 100%.**

### Reward-signal completeness — pending_feedback rows 30d

| niche | pf_rows | with_reward | % |
|---|--:|--:|--:|
| ai_creators | 96 | 81 | 84 |
| sports | 66 | 59 | 89 |
| gaming | 66 | 60 | 91 |
| anime | 59 | 53 | 90 |
| movies | 51 | 49 | 96 |
| **total** | **338** | **302** | **89** |

Reward-fetch coverage is 84–96% — matches Phase 7.6 Part A finding (~100% on 60d window; the 89% on 30d reflects rows within the 48h reward-fetch delay that haven't landed yet).

### Cost — pipeline_run_costs 30d

Baseline not pulled this session; carry forward from prior audit: **$13.61 / 30d Anthropic spend (gaming 79% concentration)**, $0.063/run avg. Day 7 check: watch for step-change post Anthropic auto-reload.

---

## Day 7 (target: 2026-07-31)

**NOT YET RECORDED.** Metrics to capture:
- Mandate % — expect 41.4% → ??
- platforms_per_reel — expect avg 3.05 → 4.0 if defect fixes landed
- archived_by_operator — expect 9 → higher (if min_confidence lowered) or same (if not)
- 402 count — expect 1 → 0 (if Anthropic auto-reload landed)
- Any `[pf-instr]` WARN log lines in journalctl (would confirm Action 5 instrumentation deployed)

**Only fill this in if actions have actually shipped.** Do not re-baseline; the value of results.md is the before-after comparison.

---

## Day 30 (target: 2026-08-23)

**NOT YET RECORDED.** The number Rev 7's whole argument turns on:

- Observations per arm per niche — expect avg_n 10.9-16.0 → 26-38 (2.4×) if mandate reached 100%.
- Median_n — expect 2-5 → 5-12.
- Bandit arm churn: any newly-updated arm IDs that weren't in the Day-0 set.
- Closure ranking — expect stable (gaming > sports > ai_c > movies ≈ anime).

If Day 30 shows the velocity gain, the cadence and channel-count questions become answerable against real data rather than projections. **If it doesn't, revisit F-0076's ceilings.**

---

## Stop rule

Do not re-audit between readings. `.audit/` is closed until Day 7. If a question arises that this ledger can't answer with the current data, the question waits.
