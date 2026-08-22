# Full capability audit — every skill, measured against four gates

**Gates:** built → wired → enabled → producing output → **reaching an audience**.
The recurring failure all week has been capabilities that clear the first three
and stop. Evidence is from prod: 62 tables (38 live, 16 stale, 8 never written),
116,791 delivered views, 1,350 published reels since 2026-03-31.

---

## 1. Multi-channel content generation — **WORKS**

89 reels in 30 days across 5 channels, daily, unattended. 81 distinct source
clips. Compliance-checked, attributed, published to 4–5 platforms each.

**But what it produces is structurally thin.** The system's own aesthetic
analyzer, on its own output:

```
cut_frequency   movies 0.042 · anime 0.141 · ai_creators 0.164 · sports 0.164
motion_energy   0.868 – 0.990
brand_consistency  anime 0.228 · ai_creators 0.390 · sports 0.601 · movies 0.645
```

Competitive short-form cuts every 1–3 seconds. These are **essentially static
single-shot reels** — the trimmed source clip with overlays. Measured, not
asserted.

## 2. Trend & content intelligence — **PARTIAL**

Ingestion works: 551 YouTube-trending, 167 Twitch, 88 AniList, ~120 Reddit
posts. `content_pool` 796 available / 149 claimed — genuinely consumed.
`competitor_content_deltas` 500 rows collected.

**But the intelligence does not predict.** `composite_score` correlates
**−0.193** with realised reach; `virality_score` 0.043; source velocity −0.088.
Within Instagram alone (n=420) nothing content-side reaches 0.07. The system
selects competently and scores meaninglessly.

## 3. Continuous learning — **DEGRADED**

459 bandit arms, 1,572 feedback rows, 1,503 scored. Reward mean **0.0746**,
**43% exactly zero**.

* Reward tracks **views r=0.488**, follower growth r=0.108–0.273 — 3× better at
  the metric that does not compound.
* **`ab_tests`: 323 rows, ALL `active`, ZERO concluded.** The experimentation
  capability starts tests and never finishes one.
* `learning_findings` 84 rows, newest 2026-08-16 — 6 days stale.
* Per an earlier audit, only the `style` family demonstrably learns; ~255
  `transform__*` arms are arithmetically unlearnable at current volume.

## 4. Multi-channel growth — **FAILING**

The headline number of the whole system:

```
~1,350 reels · 116,791 views  →  +170 net Facebook followers over 5 months
conversion ~0.26%   (healthy short-form: 1–3%)
ai_creators: −0.50 followers/day, every day, for 113 days
```

The two large audiences (10,099 and 8,502) were **present at the first
snapshot** — never earned by the pipeline.

## 5. Professional-grade judgment — **DEGRADED**

* `gate_examinations` 3,394 — runs reliably. But `min_confidence` sat **above
  the achievable confidence ceiling** on 3 of 5 niches until yesterday: 701
  gate-approved blueprints in 7 days, zero auto-approved.
* **Ensemble is expensive agreement**: 16,000 votes over 181 blueprints —
  3,970 per component, ~22 re-votes each — at an average disagreement of
  **0.045**. Four components agree 95.5% of the time; the machinery exists to
  surface disagreement that essentially never occurs.
* **`content_quality_scores`**: visual/audio/joint computed on 89 reels, but
  `aesthetic_score` is **0/89** and `aesthetic_model_versions` is **empty** —
  the aesthetic model was never trained or versioned.
* And the analyzer does not predict reach either: joint −0.041, cuts −0.191,
  motion 0.077, brand 0.124 (n=37). **Every scoring system in the platform is
  uncorrelated with outcomes.**

## 6. Monetization — **MOSTLY DARK**

* 182 affiliate clicks lifetime, newest 2026-08-19. **`blueprint_id` NULL on
  100%** — no click can be attributed to the reel that produced it.
* `sponsorship_pipeline` 1 row, `sponsorship_brand_targets` 1 row — both stale
  since 2026-08-14.
* `product_embeddings` 50 rows, stale since 2026-06-16.
* 20 of 50 catalog products are selectable; the high-commission end is excluded
  by `max_price_inr`.

## 7. Engagement automation — **DARK BY STARVATION**

`pending_engagement`: **24 rows, all `auto_archived_stranded`, newest
2026-08-09.** The classifier, pollers, toxicity gate and reply clients are all
built and wired — and there is nothing to reply to. Comments per 1,000 views:
sports 0.95, ai_creators 1.35, movies 4.35. The capability is correct and
starved.

---

## Infrastructure

| capability | state | evidence |
|---|---|---|
| **Compliance** | **WORKS** | 3,051 pre-publish checks, 1,640 AI disclosures, 6 policy blocks |
| **Attribution** | **WORKS** | writer wire 5/5 recent publishes at 100% |
| **Publishing** | works, leaky | 15% of IG publishes FAIL, 20% on Threads, 2 REMOVED_BY_META |
| **Observability** | **fixed today** | webhook delivery proven HTTP 200; silent through six outages before |
| **Alerting** | partial | `zero_blueprints` dual-severity; warning variant does not route |
| **Cost tracking** | works | `pipeline_run_costs` 538 rows, MTD $4.16 |

## Never written — capabilities that have produced nothing, ever

```
flag_flip_proposals        the autonomous flag manager has proposed nothing
meta_strategist_reports    empty
drift_signals              empty
universal_playbook         empty
aesthetic_model_versions   empty → aesthetic_score is 0/89
email_subscribers          empty
```

---

## The pattern across all seven capabilities

Ranked by where each stops:

* **Reaches an audience and works:** content generation, compliance,
  attribution, publishing, cost tracking.
* **Produces output nobody consumes:** ideation (200 ideas, all `pending`, 0
  became reels), ensemble (16k votes, 0.045 disagreement), competitor deltas,
  A/B tests (323 started, 0 concluded), learning findings.
* **Built, wired, enabled, never fired:** narration (8/8 failures), generative
  tools (0/89 published reels), `inference_utilities` (zero callers).
* **Correct but starved:** engagement automation.
* **Structurally mis-measured:** every scoring system — composite (−0.193),
  virality (0.043), joint quality (−0.041), cut frequency (−0.191).

**The system's engineering is not the weak part.** Ingestion, rendering,
compliance, publishing, attribution and cost control all work reliably and
unattended. What fails is the layer that decides *what is good*: five
independent scoring systems, none of which correlates with reach, feeding a
reward that tracks views 3× better than the audience growth those views are
supposed to produce.

Adding capability to a system that cannot rank its own output means adding
things it also cannot evaluate. That is why supplying every generation tool
changed nothing measurable — and why the next useful move is a measurement one,
not a capability one.
