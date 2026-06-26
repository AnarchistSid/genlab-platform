# Learning engines for the GenLab agent

**Author:** Synthesis from 4 parallel deep-research agents — predictive engines, preference-learning engines, generative engines, strategic/meta engines
**Date:** 2026-06-26
**Audience:** GenLab operator
**Scope:** ~24 candidate "learning engines" rated by feasibility at GenLab's specific scale (~17 publishes/day, ~263 calibration rows accumulated, 5 niches, 4 GB Hetzner VPS, ~$10-15/month current LLM spend)
**Status:** Research deliverable — synthesised priority ordering, costs, prerequisites, failure modes
**Companion to:** [`docs/AGENT-AUTONOMY-RESEARCH.md`](AGENT-AUTONOMY-RESEARCH.md) (broader autonomy roadmap)
**Length:** ~9,000 words, dense, structured for skimming

---

## TL;DR — the 5 engines to ship first

Ranked by `expected_value × feasibility ÷ risk` at GenLab's actual scale:

| # | Engine | Why it's first | Cost | Prereq |
|---|---|---|---|---|
| 1 | **Diversity Penalty / DPP filter** | Zero cost, zero new infra, fixes the documented "Cinema is back" template-overfit failure mode that already required hardcoded banned-phrase lists | $0/month | None |
| 2 | **Conformal selective prediction** | Works at 263 rows TODAY (distribution-free guarantee), reduces operator clicks 30-50%, safest first ship of any preference-learning engine | $0/month | None |
| 3 | **Critique-Rewriter loop (extend Lever K)** | Catches single-candidate hook failures the current Lever K silently passes; ~$0.50/month; single PR | $0.50/month | `GENLAB_HOOK_CRITIC_ENABLED` activated |
| 4 | **Click-rationale taxonomy via LLM** | Cheap (<$100/year), unlocks `feedback_category` enrichment, reveals systemic rejection drivers across niches | $0.30/year | None |
| 5 | **Bayesian LR with Laplace approximation** | Posterior over confidence threshold gives natural Thompson-sampling rollout for AUTO #2; replaces hand-tuned `rollout_pct` | $0/month | None |

**All five ship without depending on the engagement_rate=0 bug being fixed.** They learn from operator clicks (clean signal) rather than engagement metrics (corrupted signal). This is the critical leverage point: **operator-click-driven learning is the recovery path while engagement-driven learning is being repaired**.

---

## Part 1 — The shape of the question

### What is a "learning engine"?

A learning engine is any component that:
1. **Takes signal** from the environment (engagement, operator clicks, platform feedback, internal metrics)
2. **Updates an internal model** (bandit posterior, classifier weights, embedding index, rubric document)
3. **Influences future decisions** (gate verdict, ranking, content generation, scheduling)

GenLab today has **9 active learning engines** (mapped in detail in the prior autonomy research):
- LinUCB contextual bandit (12D context, content-arm selection)
- Thompson Sampling fallback (cold-start `<50` observations per arm)
- XGBoost hook classifier (per-niche, 8 text features)
- Calibration logger (operator-click vs gate-verdict pairs, accumulating since 2026-06-13)
- Gate tuner (nightly ±0.02 step adjustment of per-niche thresholds based on FP/FN rate)
- Reward shaper (metric → bandit reward, with monetisation-threshold proximity boosting)
- Hook style bandit (Thompson sampling over 5 style arms with Beta(α,β) posteriors)
- Relevance filter (keyword-based niche fit scoring)
- Optimal time learner (per-niche per-hour engagement bandit)

This research proposes adding **~24 more candidate engines**, prioritised based on GenLab's actual data shape and operator-time constraints.

### The two signal channels

The most important architectural insight from this research:

**Channel A: operator clicks** (`auto_approval_calibration` table, ~17 rows/day, ~263 total). **CLEAN.** Not affected by the engagement_rate=0 bug because it's written by the dashboard's `_execute_review_action` and never touches engagement metrics.

**Channel B: engagement metrics** (`analytics` jsonb, `publishing_analytics` rows). **PARTIALLY CORRUPTED.** The engagement_rate field has been broken since commit `f32b6189` (2026-06-09, 17 days). Fixed today in PR #588 + backfilled in PR #600 (33 rows updated, 101 clamp updates, 1923 viral_score recomputes), but the system needs 2-3 weeks of fresh clean data before engagement-driven learners can trust the signal.

**Practical implication for engine ordering:**
- **Engines that learn from Channel A** can ship immediately, regardless of engagement-data state. These dominate Tier 1.
- **Engines that learn from Channel B** must wait for fresh data to accumulate. Most of Tier 2.
- **Engines that learn from BOTH channels** are most powerful but have the longest critical path.

### Why scale matters

GenLab's specific scale invalidates many "industry standard" learning techniques:

| Technique | Industry minimum data | GenLab's data | Verdict |
|---|---|---|---|
| Full SFT of 70B model | ~100k labeled examples | ~150 / niche | Don't build |
| DPO fine-tune | ~1000 pairs / niche (AWS guidance) | ~53 / niche | Don't build before Q4 2026 |
| RLHF with PPO | ~10k pairs minimum | ~263 total | Don't build |
| LoRA / QLoRA on 8B | ~500-1500 examples / niche | ~50 / niche | Premature, cost-negative |
| Gaussian Process preference learning | O(n³) — works at small n but premature | 263 rows | Use Bayesian LR instead |
| Active learning beyond conformal | Calibrated uncertainty required | Not available | Use conformal as substrate |
| Conformal prediction (split) | 200-300 rows per niche stratum | 263 rows total | Ships today (Mondrian-stratified) |
| Thompson Sampling | Beta(1,1) priors, 30-50 plays/arm | Working today | Ships |
| Bayesian LR + Laplace | Works at n=1 with proper prior | 263 rows | Ships today |
| Hierarchical bandits | Tens-of-thousands of decisions | Possible at this scale | Tier 2-3 |

The pattern: **techniques calibrated for small-data ship now; techniques calibrated for big-data ship after scale or are skipped entirely.**

---

## Part 2 — The 24 candidate engines surveyed

Grouped by learning-family. Each is summarised in 2-3 lines; detailed analysis from agent reports follows in Part 3.

### Family A: Predictive / forecasting engines (6)

Engines that predict an outcome BEFORE a decision is made.

1. **Pre-publish engagement forecaster** — Multimodal regression head predicts `(μ, σ) = E[engagement_rate@24h | candidate]` from hook embedding + video features + niche + time. Feeds gate as a lower-confidence-bound signal and reward shaper as a residual baseline.

2. **Virality / breakout predictor** — Different from #1; predicts P(breakout) rather than expected engagement. Heavy-tail prediction via extreme-value theory or quantile regression forests. Critical insight: average engagement and virality are orthogonal — most content optimises for the wrong objective.

3. **Retention curve modeler** — Predicts the watch-time decay curve before publish. Influences whether to trim a clip to its peak vs render full-length. TikTok/YouTube have shipped this internally; replicable via Cox proportional hazards on watch-percent buckets.

4. **Trend lifecycle predictor (Hawkes Intensity Process)** — Given a topic appearing across source channels, predicts `(time_to_peak, peak_magnitude, decay_half_life)`. HIP achieves **28.6% prediction-error reduction** vs popularity-history baselines on YouTube videos with Twitter exogenous signals (Rizoiu WWW'17).

5. **Source quality / LTV model** — Per source channel, predicts future contribution to engagement. RFM + XGBoost + Cox proportional hazards. Drives auto-pruning of dead sources (currently 68 dead per audit) and auto-promotion of rising ones.

6. **Sponsorship-tier trajectory predictor** — Per niche, predicts days-to-Tier-2-monetisation eligibility AND which content types most accelerate the trajectory. Counterfactual estimation via T-learner / uplift modeling on strategy archetype.

### Family B: Preference learning engines (7)

Engines that extract signal from operator approve/reject clicks.

7. **DPO / IPO / KTO on hooks** — Direct preference optimization treating (approved_hook, rejected_hook) as preference pairs. Replaces XGBoost hook_classifier with DPO-trained reward model. KTO (unpaired binary) is the right algorithm for GenLab's data shape. **DON'T BUILD before Q4 2026** — sample size 4× below AWS minimum.

8. **LLM-as-judge with rubric** — Claude Haiku scores blueprints against a rubric synthesized from operator click patterns. Hybrid two-stage: weekly Opus rubric synthesis from false-positive examples + online Haiku judging. **Already partially shipped** (Lever C's borderline judge); extend with explicit calibration-anchored rubric.

9. **Conformal selective prediction** — Split conformal classification gives the gate a third output (ABSTAIN) when uncertain. Blueprints with prediction set `{approved, rejected}` route to operator; singletons get auto-decided. Distribution-free coverage guarantee. **30-50% operator-click reduction at 90% coverage.**

10. **IPS / Doubly Robust off-policy evaluation** — Before flipping `rollout_pct` from 0.1 → 0.5 in AUTO #2, replay history under proposed new policy to estimate effect WITHOUT deploying. **Critical blocker:** requires making the gate stochastic first (today's gate is deterministic → propensity is 0 or 1 → IPS variance is infinite).

11. **Click-rationale taxonomy extraction** — For each operator rejection, Claude Haiku classifies WHY (from post content) into a fixed taxonomy (`weak_hook`, `too_generic`, `unsupported_claim`, `bad_fit`, `too_long`, `low_value`). Populates `feedback_category` automatically; unlocks per-category gate tuning.

12. **Bayesian logistic regression with Laplace approximation** — Model operator preference as a posterior distribution over LR weights. Sample at decision time (Thompson sampling) — natural exploration/exploitation. Particularly powerful for low-data niches (anime: ~30 rows today).

13. **Operator-shadowing imitation (XGBoost pairwise rerank)** — Train tabular pairwise-ranking model on operator decisions with session-context features. Use to (a) sort focus-review queue by margin, (b) feed as 7th gate signal. XGBoost `rank:pairwise` works at ~150-300 pairs/niche.

### Family C: Generative engines (7)

Engines that improve the quality of content generation.

14. **Hook-winner retrieval RAG** — Embedding-based retrieval of top-K winning hooks per niche, injected as few-shot examples into the hook generation prompt. Hybrid sparse+dense + cross-encoder rerank. **Estimated 20-35% reduction in generic-output rejection rate.**

15. **LoRA / QLoRA fine-tune of open-source hook generator** — Fine-tune Llama-3.1-8B with QLoRA on per-niche winning hooks. Replaces Claude Haiku for hook-only step. **DON'T BUILD at current scale** — needs 500-1500 examples/niche (GenLab has 50/niche); cost-negative vs Haiku ($100/mo vs $10/mo current).

16. **DSPy MIPROv2 programmatic prompt optimization** — Bayesian optimizer over (instruction, demonstration) space against HookClassifier as held-out metric. Production case studies report **10-40% quality lift over hand-written prompts on structured tasks** at 40 labeled examples (GenLab has 40+/niche today).

17. **Constitutional critique-rewriter loop** — Extend Lever K from binary verdict + fallback-to-#2 into a critique → rewrite loop. When `len(candidates) == 1` AND critic rejects, run a second LLM pass that addresses the critique reason. Self-Refine paper: 5-40% absolute improvement on similar tasks.

18. **Synthetic preference data generation** — Claude Opus generates synthetic (winner, loser) hook pairs to inflate DPO training data 10-50×. **HIGH RISK** — "More is Less" paper (arXiv 2504.02193) shows synthetic preference data exhibits linear separability that DPO exploits as a shortcut. Only build if Engine 7/15 ships.

19. **Multi-arm bandit over prompt templates** — Each prompt template variant is a bandit arm; LinUCB learns which template produces highest-engagement hooks per niche. Extension of existing `style:*` arms in `pick_hook_style`. Production case studies: 10-25% lift over fixed prompts.

20. **Hook diversity penalty (embedding-distance rejection + DPP)** — At generation time, compute cosine similarity of candidate to last-30 published hooks; reject if >0.85. Directly addresses the GenLab failure mode documented in `llm_hook_generator.py:148-211` (template overfit requiring hardcoded banned-phrase lists).

### Family D: Strategic / meta learning engines (7)

Engines that learn at the cross-niche, cross-strategy, or cross-decision level.

21. **Hierarchical contextual bandit (strategy → arm)** — Two-level bandit. Top level chooses strategy archetype (`highlight_clip` / `explainer_clip` / `trending_moment` / `compilation`); bottom level chooses specific arm within the strategy. HierTS / Mixed-Effect Thompson Sampling. Dream11 reports +0.4% A/B + 0.5% post-launch revenue lift.

22. **Cross-niche prototypical-network transfer** — For new niche cold-start, retrieve K most similar `(niche, hook-template)` prototypes from existing 5 niches. Avoid negative transfer by gating on niche-similarity score. **Only useful at niche #6 — defer until SaaS expansion.**

23. **Audience segmentation per niche** — Cluster blueprints by engagement signature (time-of-day pattern, content-type, comment style) via BERTopic + GMM. Per-cluster optimal posting time. **Premature** without YouTube Analytics API hourly geo break.

24. **Posting-time bandit v2 (hour × content × segment)** — Extend `optimal_time_learner` to multi-dimensional contextual bandit on hour-of-week × content-type × audience-segment with Bayesian shrinkage across adjacent cells. Discounted Thompson sampling for non-stationarity. Mixed-Effect Thompson Sampling.

---

## Part 3 — The synthesised build queue

Ranked across all 24 engines by `expected_value × feasibility ÷ risk`, accounting for which signal channel each uses and what prerequisites must be met.

### Tier 1 — Ship now (5 engines)

**These five engines can ship in the next 1-3 weeks regardless of engagement_rate fix landing.** They all learn from operator clicks (clean signal) or are pure-inference-time improvements requiring no learning.

#### 1.1 Diversity Penalty / DPP filter (Engine #20)

**Algorithm.** Cosine-distance rejection (simple) or Determinantal Point Process sampling (sophisticated). At generation time, embed the candidate hook via sentence-transformers MiniLM (already in dep tree), compute max cos-sim against last-30 published hooks for that niche, reject if >0.85.

**Why first.**
- **Zero cost, zero new infrastructure.** Sentence-transformer runs local CPU.
- **Directly addresses the documented failure mode** — `llm_hook_generator.py:148-211` already maintains hardcoded banned-phrase lists ("Cinema is back", "absolutely insane", "No more excuses") that grow every few weeks. This is a reactive bandage; embedding-distance is the proactive root-cause fix.
- **Ships in a single PR**, ~80 LOC + a cache module.

**Integration.** Insert filter at `llm_hook_generator.py:520-574` (the `scored` candidate-ranking step). New module `genlab_core/learning/hook_diversity_cache.py` reads last 30 published hooks per niche from Postgres, embeds with 5-min TTL cache (mirror `learning/rejection_rag.py` pattern).

**Cost.** $0/month. Local CPU inference, no API spend.

**Failure modes & mitigations.**
- Threshold too aggressive → all candidates rejected → fall back to highest-scored. Start at 0.90, tighten if too many duplicates pass.
- Cold-start over-rejection (new niche, no history) → first 30 hooks unaffected. Acceptable.
- MiniLM is English-only and weak on slang. Acceptable for v1.

**References.** [Enhancing Diversity in LLMs via DPP (arXiv 2509.04784)](https://arxiv.org/html/2509.04784v1), [GDPP for diverse generations](https://arxiv.org/abs/1812.00068).

---

#### 1.2 Conformal selective prediction (Engine #9)

**Algorithm.** Split conformal classification (Angelopoulos & Bates 2023). Split calibration data 50/50 into proper-training and calibration set. Train any base model `f(x) → P(approved | x)` — could be the existing rule-based gate, an XGBoost on `(composite, virality, niche, passed_checks_count)`, or a small NN. At test time, compute prediction set `{y : 1 - f(x)[y] ≤ q̂}` where `q̂` is the `⌈(n+1)(1-α)⌉/n` quantile of calibration non-conformity scores.

**Why first.**
- **Works at 263 rows today.** Distribution-free coverage guarantee; survives drift better than Cohen's κ.
- **Directly attacks the highest-leverage automation goal** (operator-click reduction).
- **At α=0.10, with current gate confidence distribution, ~60-70% of blueprints become singletons (auto-decided) and ~30-40% route to operator. Operator click reduction: 30-50%** while preserving 90% coverage guarantee.

**Integration.** New module `genlab_core/scheduling/conformal_router.py`. Wraps `auto_approval_gate.evaluate()`. Returns `(action, prediction_set, q_hat)`. If set = `{approved}` AND `auto_publish.enabled=True` for the niche → auto-publish. If set = `{rejected}` → auto-archive. If set = `{approved, rejected}` → route to focus-review queue with priority inversely-proportional to distance from decision boundary.

**Cost.** $0/month. Logistic regression fits in milliseconds on 263 rows.

**Failure modes & mitigations.**
- Coverage gap on small n — at n=263, real coverage is `1-α − 1/264 ≈ 89.6%` instead of 90%. Acceptable but measure empirically per niche.
- Exchangeability violation when operator preferences drift. Use **weighted-conformal** (recent rows weighted higher per Tibshirani et al.).
- Marginal vs conditional coverage — marginal doesn't promise per-niche coverage. Use **Mondrian-conformal stratified on `niche_id`**.

**References.** [Gentle Intro to Conformal Prediction — Angelopoulos & Bates](https://people.eecs.berkeley.edu/~angelopoulos/publications/downloads/gentle_intro_conformal_dfuq.pdf), [Reliable Statistical Guarantees for Small Datasets (arXiv 2512.04566)](https://arxiv.org/abs/2512.04566), [Conformal Selective Prediction with Cost-Aware Deferral (Nature Sci Reports 2026)](https://www.nature.com/articles/s41598-026-40637-w).

---

#### 1.3 Constitutional critique-rewriter (Engine #17)

**Algorithm.** SELF-REFINE (Madaan et al. 2023) pattern. Extend the existing hook critic (Lever K, `_critique_hook_grounded`) from binary verdict + fallback-to-#2 into a critique → rewrite loop. When `_critique_hook_grounded` returns `grounded=False` AND no `#2` candidate exists, call a new `_rewrite_hook(hook, story, critique_reason)` function. Max 2 rewrite rounds.

**Why first.**
- **Lever K already shipped** (`GENLAB_HOOK_CRITIC_ENABLED=1`, activated today).
- **Catches the highest-visibility failure class** — hallucinated entities (the "Pixar's Pressure" incident the code comments explicitly cite).
- **Currently when `len(candidates) == 1` AND Lever K rejects, the bad hook silently passes.** Estimated ~5-8 saves per week.

**Integration.** `llm_hook_generator.py:600-610`. ~30 LOC + one new function. Single PR.

**Cost.** ~+$0.45/month. 75 hooks/day × ~10% Lever K rejection rate × 1 rewrite call × $0.0002 Haiku.

**Failure modes & mitigations.**
- Refinement collapse (model "refines" by changing nothing) — pass explicit `reason` string back into rewrite prompt.
- Over-refinement / quality drift — cap at 2 rounds.
- Self-preference bias (same model judging its own rewrites) — use Haiku for generation+rewrite but a SEPARATE judge (small classifier or different model family) for the final approval.

**References.** [SELF-REFINE (Madaan et al. 2023, arXiv 2303.17651)](https://arxiv.org/pdf/2303.17651), [Self-Preference Bias in LLM-as-a-Judge (arXiv 2410.21819)](https://arxiv.org/pdf/2410.21819).

---

#### 1.4 Click-rationale taxonomy extraction (Engine #11)

**Algorithm.** Two-pass classification. (1) Per-row classification on operator click: Haiku scores blueprint against 6 canonical categories with score per category. Highest score becomes `feedback_category`. Multi-label option for ties. (2) Weekly Opus refinement: cluster "other" / low-confidence rows; propose new categories; operator confirms via dashboard.

**Why first.**
- **The `feedback_category` column exists** (migration `x4s5t6u7v8w9_calibration_feedback_category.py`) but no auto-classifier exists today — operators must manually pick them, and per the migration's docstring "no endpoint reads them."
- **Cheap** (<$100/year total).
- **Unblocks 3 downstream engines** (LLM-as-judge rubric synthesis, per-category gate tuning, rejection-pattern audit).

**Integration.** New module `genlab_core/learning/rationale_classifier.py`. Called from `dashboard/server/core/calibration_helper.py:log_calibration_for_action` BEFORE the calibration write. Existing `rejection-breakdown-endpoint` test becomes consumer.

**Cost.** Per-rejection: ~$0.0001 (200-token Haiku). At ~3 rejects/day × 365 = ~$0.10/year. Weekly Opus: $1-2/week. **Total <$100/year.**

**Failure modes & mitigations.**
- Spurious categorisation — only auto-fill if confidence > 0.7; operator-override visible in dashboard.
- Category drift — weekly Opus clustering + operator confirmation flow.
- Self-reinforcement loop (operators rubber-stamp Claude's prediction) — **hide Claude's prediction from a randomly-sampled 10% of reviews (control group)**.

**References.** [LLM-Mod: Content Moderation by LLM (CHI 2024)](https://dl.acm.org/doi/10.1145/3613905.3650828), [Policy-as-Prompt: Rethinking Content Moderation (FAccT 2025)](https://dl.acm.org/doi/10.1145/3715275.3732054).

---

#### 1.5 Bayesian LR with Laplace approximation (Engine #12)

**Algorithm.** Bayesian logistic regression with Laplace approximation:
1. Prior `w ~ 𝒩(0, σ² I)` (weakly informative).
2. Per calibration row `(x_i, y_i)`: posterior `p(w | data) ∝ prior × Π σ(w·x_i)^{y_i} (1-σ(w·x_i))^{1-y_i}`.
3. Laplace approximation: posterior ≈ `𝒩(ŵ_MAP, H⁻¹)`. Closed-form, no MCMC.
4. At decision time (Thompson sampling): sample `w ~ posterior`, compute `p_approve = σ(w·x_new)`, threshold at 0.5.

**Why first.**
- **Works at n=1** with a proper prior. Practical convergence: ~50 rows per niche. All 5 niches usable today.
- **Replaces hand-tuned `rollout_pct`** — Bayesian posterior naturally widens for low-data niches and tightens with more data. Thompson sampling automatically scales aggressiveness with data confidence.
- **Concrete prediction:** anime niche (30 rows) → 95% CI on `p_approve` of width ±0.18 → decisions explore broadly. After 200 anime rows → CI tightens to ±0.06 → decisions converge. Automatic exploration-exploitation curve.

**Why not Gaussian Process?** GP preference learning (Chu & Ghahramani 2005) is the textbook approach but GPs scale O(n³); at 263 rows it's trivially fast but the GP machinery is overkill. Bayesian LR on the existing 6-D feature space is dead simple.

**Integration.** New module `genlab_core/learning/bayesian_gate.py`. Reduces `auto_approval_gate.evaluate` to a thin wrapper. Posterior persists in `bayesian_gate_state.json` (~5KB per niche); refits nightly from calibration table.

**Cost.** $0/month. Fit takes ~100ms on 263 rows via sklearn. Inference: sub-ms (matrix multiply).

**Failure modes & mitigations.**
- Laplace approximation may be poorly Gaussian for n<30 — use conservative `mean - 1·std` rule for small-n niches.
- Cold-start exploitation trap (Thompson can lock onto wrong arm) — explicit **ε-greedy floor** (10% random for first 100 decisions per niche).
- Feature drift — if gate adds a new feature, posterior must be reset (no online updating). Versioned artifact.

**References.** [Chu & Ghahramani GP Preference Learning (ICML 2005)](https://www.gatsby.ucl.ac.uk/~chuwei/paper/icmlPL.pdf), [PG-TS Logistic Bandits (Dumitrascu et al. NeurIPS 2018)](http://papers.neurips.cc/paper/7713-pg-ts-improved-thompson-sampling-for-logistic-contextual-bandits.pdf).

---

### Tier 2 — Ship 4-8 weeks after engagement-rate fix lands (5 engines)

**These engines require fresh clean engagement data.** PR #588 fixed the bug today; need 2-3 weeks of post-fix data accumulation before these become trustworthy.

#### 2.1 Multi-arm bandit over prompt templates (Engine #19)

**Algorithm.** Each prompt template variant is a bandit arm; LinUCB learns which template produces highest-engagement hooks per niche. Extension of existing `style:*` arms in `pick_hook_style` (`llm_hook_generator.py:77-145`). New arm dimension: **prompt template** orthogonal to style.

**Why Tier 2.** **Reuses existing Thompson/LinUCB infrastructure.** Adding 4-6 hand-designed template variants seeded in config + bandit-over-templates wired in. **Zero new ML.** Just data once engagement signal is trustworthy.

**Expected gain.** +3-6 pp on engagement_rate of generated hooks, niche-specific (gaming's best template differs from anime's).

**Cost.** $0/month. Template selection is local; only the rendered prompt changes.

**Prerequisite.** Engagement_rate fix landed AND 4-6 weeks of fresh data accumulated. 4-6 hand-designed template variants seeded in `genlab-core/config/hook_templates/`.

**References.** [A Component-Based Survey of LLMs + Multi-Armed Bandits (arXiv 2601.12945)](https://arxiv.org/pdf/2601.12945), [Feel-Good Thompson Sampling for Contextual Bandits (arXiv 2507.15290)](https://arxiv.org/html/2507.15290).

---

#### 2.2 Hook-winner retrieval RAG (Engine #14)

**Algorithm.** 2026 production-RAG best practice: **hybrid sparse+dense + cross-encoder rerank**. BM25 catches entity/keyword overlap (player names, franchise titles); dense embeddings (OpenAI text-embedding-3-small, 1536d, $0.02/M tokens) catch semantic intent; cross-encoder (sentence-transformers `cross-encoder/ms-marco-MiniLM-L-6-v2`, local CPU) reranks top-50 → top-5.

**Why Tier 2.** Replaces today's frozen 5-element `top_hooks` static list with a self-updating index — the static list IS the documented cause of template-overfit. **Estimated 20-35% absolute reduction in generic-output rejection rate**, lifting operator revision rate from ~1/day to ~0.3/day.

**Integration.** Replace `top_hooks` block at `llm_hook_generator.py:454-460`. New module `genlab_core/learning/hook_winner_retriever.py` (mirrors `learning/rejection_rag.py` shape: 5-min TTL cache, fail-OPEN).

**Cost.** Index build: ~$0.01. Per-query embed: ~$0.00003. At 75 hooks/day: **~$0.07/month**.

**Prerequisite.** Engagement_rate fix landed AND `blueprints` table has `reward_48h` denormalized column OR a JOIN view to `analytics`.

**Mitigations against failure modes.**
- **Stale-winner overfit:** half-life decay `exp(-Δdays/30)` before ranking.
- **Cross-niche leak:** pre-filter strictly by `niche_id`.
- **Genre-clustering collapse:** post-retrieval **MMR (Maximal Marginal Relevance)** or DPP-based reranking on retrieved set.

**References.** [AppScale Hybrid RAG 2026](https://appscale.blog/en/blog/hybrid-search-and-reranking-production-rag-bm25-dense-cross-encoder-2026), [Milvus Few-Shot RAG](https://milvus.io/ai-quick-reference/how-can-fewshot-examples-be-utilized-in-a-rag-prompt-to-demonstrate-how-the-model-should-use-retrieved-information-for-instance-providing-an-example-question-the-context-and-the-answer-as-a-guide).

---

#### 2.3 DSPy MIPROv2 prompt optimization (Engine #16)

**Algorithm.** Wrap the existing hook generator as a `dspy.Module` with `dspy.Signature` `(story_title, story_summary) -> hook`. Define `dspy.Metric` that calls `HookClassifier.score_hook(predicted_hook)` + banned-phrase penalty + length sanity. Run `dspy.MIPROv2(metric=..., auto="medium").compile(program, trainset=trainset)`.

**Why Tier 2.** **Highest expected quality lift** of any generative engine (10-40% over hand-written prompts per case studies). Sample-efficient (works at 40 examples; GenLab has 40+/niche today). **Requires HookClassifier to be reliable** — depends on engagement_rate fix.

**Integration.** New `genlab_core/learning/prompt_optimizer.py` (offline batch job). Config: `genlab-core/config/hook_prompts/{niche_id}.json`. Edit `llm_hook_generator.py:409-452` to load prompt from JSON if present.

**Cost.** ~$25/month (compile run for hook generation: ~$2-5 in Haiku calls per niche, 5 niches × monthly).

**Prerequisite.** Same engagement_rate fix; DSPy 2.5+ added to deps.

**Mitigations.**
- **Metric Goodhart:** composite metric (classifier × banned-phrase × length × diversity) + held-out validation set.
- **Prompt bloat:** cap token budget in compile config.
- **Per-niche overfit:** factor out a **shared constitutional core** + per-niche tail.

**References.** [DSPy MIPROv2 docs](https://github.com/stanfordnlp/dspy/blob/b40f359ec567a04a7f8d1d5d1a744ca9c32d5339/docs/docs/deep-dive/optimizers/miprov2.md), [CallSphere DSPy 2026 case studies](https://callsphere.ai/blog/vw8g-dspy-prompt-optimization-mipro-2026).

---

#### 2.4 LLM-as-judge with rubric extracted from calibration (Engine #8)

**Algorithm.** Hybrid two-stage. (1) **Offline rubric synthesis** (weekly Opus): join last 30 days of calibration rows where `gate_approved=True ∧ operator_action='rejected'` (false positives) with `feedback_category`. Feed FPs to Claude Opus: "What 5-8 rubric criteria, if added, would have correctly rejected these?". Persist to `gate_rubric_<niche>.md`. (2) **Online judging** (per borderline call): replace static `_LLM_JUDGE_SYSTEM_PROMPT` with synthesized rubric.

**Why Tier 2.** Foundation already shipped (Lever C borderline judge). Extension is **rubric-extraction from feedback_category** (which needs Engine 1.4 to populate first). GEPA-style prompt optimization improves judge-human agreement from 84.1% → 91.4%.

**Cost.** Per-decision: ~$0.0002 Haiku (already paid). Weekly Opus rubric synthesis: ~$0.50/niche × 5 = $2.50/week ≈ **$130/year**.

**Prerequisite.** Engine 1.4 (rationale taxonomy) populating `feedback_category` consistently.

**Mitigations.**
- **Position bias** (judge prefers first option): always pass single blueprints (not pairs); randomize rubric criterion ordering.
- **Verbosity bias** (judge over-approves long hooks): length-normalized scoring criterion.
- **Rubric drift:** κ-based alarm — if judge-human κ drops below 0.5 for 7 days, force re-synthesis off-cadence.
- **Calibration paradox:** cross-family validation — run Claude AND GPT-4 on a 20-blueprint subset weekly; if they disagree >20%, rubric is broken.

**References.** [Rubric-Based Evaluations (Adnan Masood Medium 2026)](https://medium.com/@adnanmasood/rubric-based-evals-llm-as-a-judge-methodologies-and-empirical-validation-in-domain-context-71936b989e80), [Reliability without Validity — LLM-as-Judge (arXiv 2606.19544)](https://arxiv.org/abs/2606.19544).

---

#### 2.5 Source quality / LTV model (Engine #5)

**Algorithm.** XGBoost regressor + Cox proportional hazards (lifelines). Per-source features: recency (days since last blueprint), frequency (blueprints from this source in last 30/90 days), monetary equivalent (cumulative engagement_rate), subscriber-growth velocity, comment velocity, niche-relevance score, content recency, negative-keyword hit rate.

**Why Tier 2 and not Tier 1?** **Could be the single highest-ROI engine in the entire list** — would eliminate the 68 dead sources from the candidate pool (per audit memory) AND free YT quota for productive sources. Estimated **+15% net engagement per unit cost**. But fundamentally depends on engagement_rate being clean — training on `engagement_rate=0` just learns "all sources are dead."

**Integration.** New module `genlab_core/learning/source_quality_model.py`. Called weekly from `score_and_filter.py` to re-rank sources. Exposes `/api/v1/sources/quality` endpoint for dashboard. **Auto-prune action gated behind `GENLAB_SOURCE_AUTOPRUNE=1`** (opt-in) until calibration verified.

**Cost.** Training: fits on CPU. YT subscriber-growth fetch: 1 quota unit per source per week × 150 sources × 4 weeks = 600 units/month (trivial vs 10K/day quota).

**Prerequisite.** Engagement_rate fix landed + 6 months of `blueprints.source_channel_id` history. PR #586 just shipped this column — start collecting now, train at month 3, deploy at month 6.

**Mitigations.**
- **Selection bias** (model only sees chosen sources): log a "candidate impressions" table.
- **Censoring** (source produces zero for 30 days then explodes): Cox model handles if censoring flag set correctly.
- **Niche-specific bias:** per-niche models OR niche_id as feature.

**References.** [Customer Lifetime Value with Gradient Boosting](https://www.jisem-journal.com/download/06_Arjun_Feb_2019_JISEM.pdf), [Predicting Customer Churn: XGBoost with Temporal Data (arXiv 1802.03396)](https://arxiv.org/pdf/1802.03396).

---

### Tier 3 — Ship in 3-12 months (10 engines)

These require either substantial data accumulation OR scale that GenLab doesn't have yet OR significant new infrastructure. Ranked by expected leverage.

| Rank | Engine | When to consider | Why deferred |
|---|---|---|---|
| 3.1 | **Hierarchical contextual bandit** (#21) | After 3 months of clean engagement data | Strategy archetype dimension needs labelled corpus + Engine 1.1 (templates) provides the first level |
| 3.2 | **Pre-publish engagement forecaster** (#1) | After 6 months clean engagement data | Multimodal regression head needs ~500+ examples/niche; today's volume is ~30/month/niche |
| 3.3 | **Posting-time bandit v2** (#24) | After Hierarchical bandit ships + 6 months data | Cube has 12,600 cells; today's data is way too sparse |
| 3.4 | **Operator-shadowing XGBoost rerank** (#13) | After 2 months of clean calibration data | Pairwise ranking needs ~150-300 pairs/niche; ~10/day accumulation rate means ~2-3 months |
| 3.5 | **IPS / Doubly Robust OPE** (#10) | After stochastic gate ships | Today's deterministic gate breaks IPS; must add stochasticity FIRST |
| 3.6 | **Trend lifecycle predictor (Hawkes)** (#4) | After 6 months of cascade timeseries data | HIP achieves 28.6% error reduction but needs ≥1000 cascades; backfill 6 months of velocity deltas |
| 3.7 | **Sponsorship trajectory predictor** (#6) | After 6 months of `MonetisationProgress` weekly snapshots | Bayesian linear regression on cumulative watch hours; needs ≥6 months data |
| 3.8 | **Retention curve modeler** (#3) | After per-second watch-time data available | Requires platform APIs to expose per-second retention curves; YouTube Analytics has it, Meta does not |
| 3.9 | **Virality / breakout predictor** (#2) | After Engine 3.2 ships | Heavy-tail prediction is orthogonal to mean-engagement prediction; only valuable once mean-engagement is solved |
| 3.10 | **Stochastic gate** (prerequisite for #10) | Q1 2027 | 1-week build but high architectural risk — gates all OPE downstream |

### Tier 4 — DON'T BUILD (5 engines)

Looks attractive but wrong for GenLab at current scale.

| Engine | Why not |
|---|---|
| **DPO / IPO / KTO fine-tune on hooks** (#7) | 263 rows is **4× below** AWS's stated 1000-pair minimum. "Random Is Hard to Beat" (arXiv 2604.02766) shows active selection in online DPO with modern LLMs barely beats random at small data. Strong pre-trained priors dominate. Revisit at 1000+ rows/niche (~12 months at current 17/day). KTO would be the eventual choice (matches unpaired binary data shape), not DPO. |
| **LoRA / QLoRA fine-tune of 8B generator** (#15) | Haiku 4.5 is already excellent at 60-char headline writing. QLoRA'd 8B unlikely to beat it; realistic upside is style consistency. **Cost: ~$100/month** (training + inference) vs **$10/month current Haiku spend**. Cost-negative until 50+ niches. |
| **Synthetic preference data generation** (#18) | Only valuable if DPO ships. "More is Less" paper (arXiv 2504.02193) shows synthetic preference data exhibits linear separability that DPO exploits as a shortcut (e.g., model learns "longer = better" from systematic length differences in synthetic pairs). High risk of contaminating real preference data. |
| **Cross-niche prototypical transfer** (#22) | Only useful at niche #6. Defer until SaaS expansion. Per-niche calibration loss + negative-transfer risk outweighs cold-start benefit at 5 niches. |
| **Audience segmentation per niche** (#23) | Premature. ~30 published clips/week per niche × 5 clusters × 168 hours = 600 buckets with <1 clip/bucket/month. Cluster posteriors stay flat. **Curse of dimensionality wins over per-segment learning at this scale.** |
| **Gaussian Process preference learning** | Premature. Bayesian LR with Laplace (Engine 1.5) gives 95% of the value with 10% of the complexity. GP machinery (kernel selection, Laplace for non-Gaussian likelihood) is overkill at 263 rows. |
| **RLHF with PPO** | Requires online interaction + on-policy data. GenLab is naturally offline (operator clicks accumulate over days). DPO/KTO/IPO superseded RLHF for offline preference data. |
| **GAIL / adversarial imitation** | Requires policy rollouts; GenLab can't safely roll out new policies without OPE evaluation first. Use BC + DAgger-style (Engine 3.4) instead. |

---

## Part 4 — The compounding strategy

The build order matters because engines compound on each other. The recommended sequence exploits compounding without requiring all-at-once delivery.

### Phase 1 (Weeks 1-3): Pure operator-click engines

Ship: Engine 1.1 (Diversity), 1.2 (Conformal), 1.3 (Critique-Rewriter), 1.4 (Click-rationale), 1.5 (Bayesian LR).

**Compounding effects:**
- Engine 1.4 populates `feedback_category` → enables Engine 2.4 (LLM-as-judge rubric) in Phase 2.
- Engine 1.2 (Conformal) provides the certainty layer that Engine 3.4 (Operator-rerank XGBoost) composes on.
- Engine 1.5 (Bayesian LR) replaces the hand-tuned `rollout_pct` → AUTO #2 auto-ramps based on data confidence instead of operator PRs.

**Combined effect after Phase 1:**
- Diversity filter eliminates the template-overfit failure pattern at the source.
- Conformal selective prediction reduces operator clicks 30-50%.
- Critique-rewriter eliminates the worst single-candidate hallucinations.
- Click-rationale unlocks per-category analysis.
- Bayesian LR replaces manual ramps with automated ones.

**Operator review burden:** ~17/day → ~10/day (estimated 40% reduction).

### Phase 2 (Weeks 4-12): Engagement-data-dependent engines

After PR #588 + #600 + #601 + #603 lands (today's session) and 4 weeks of fresh engagement data accumulates:

Ship: Engine 2.1 (Template bandit), 2.2 (Hook-RAG), 2.3 (DSPy), 2.4 (LLM-judge rubric), 2.5 (Source quality model).

**Compounding effects:**
- Engine 2.1 reuses LinUCB infrastructure; first thing to compound on the clean engagement signal.
- Engine 2.2 replaces frozen `top_hooks` static list → root-cause fix for template overfit.
- Engine 2.3 optimises the prompt itself → biggest single quality lift.
- Engine 2.4 composes on Engine 1.4's `feedback_category` data.
- Engine 2.5 unlocks source-pool hygiene (eliminates 68 dead sources from candidate pool).

**Combined effect after Phase 2:**
- Hook generation quality lifts +10-20%.
- Source pool shrinks from current size to "healthy" mix.
- Per-niche prompt optimization sharpens to the niche's actual voice.
- Engagement signal quality stabilises.

**Operator review burden:** ~10/day → ~5/day (combined Phase 1 + Phase 2 estimated 70% reduction).

### Phase 3 (Months 3-9): Strategic & meta engines

After Phase 2 stabilises and ~3 months of clean engagement data has compounded:

Ship: Engine 3.1 (Hierarchical bandit), 3.2 (Pre-publish forecaster), 3.3 (Posting-time v2), 3.4 (Operator-rerank), 3.5 (IPS/DR via stochastic gate), 3.6 (Trend lifecycle), 3.7 (Sponsorship trajectory).

**Compounding effects:**
- Engine 3.1 adds strategy archetype dimension that Engine 3.3 (posting-time) and Engine 3.7 (sponsorship) both consume.
- Engine 3.5 (IPS/DR) becomes critical when AUTO #2 ramps beyond Tier 2's 0.1 rollout — safely evaluate policy changes BEFORE deploying.
- Engine 3.6 (Trend) routes render priority → Engine 2.5's source quality scores inform which trends matter.

**Combined effect after Phase 3:**
- System reasons at the strategy level, not just the arm level.
- Posting time picks per (niche × strategy × audience-segment) instead of per-hour-average.
- Trends caught at hour 6 of 48 instead of day 3 of 5 (HIP enables ~28.6% error reduction).
- Sponsorship trajectory routes operator's effort to the highest-leverage channels.

**Operator review burden:** ~5/day → ~2/day (combined estimate 88% reduction from today).

### Phase 4 (Year 2): SaaS expansion enablers

Ship: Engine 22 (Prototypical transfer) — only when adding niche #6.

**Combined effect:** Each new niche cold-starts in days, not weeks, by inheriting prior knowledge.

---

## Part 5 — The strongest synergies

Some engine pairs are dramatically more powerful together than separately. Highlighting the three highest-leverage synergies:

### Synergy 1: Source quality (#5) + Trend lifecycle (#4)

Together: "this source is high-quality AND this trend is at hour 6 of 48 expected peak" → render IMMEDIATELY. Each engine alone gives ~15% lift; together estimated **30-50% on FAST_LANE clips**.

### Synergy 2: Hierarchical bandit (#21) + Prototypical transfer (#22)

When niche #6 launches, Engine 22's prototypes pre-fill Engine 21's tree leaves with rewards inherited from sibling niches. This is the **SaaS multi-tenancy enabler** — every new tenant gets prior knowledge for free.

### Synergy 3: Conformal selective prediction (#9) + Bayesian LR (#12) + Operator-rerank (#13)

Three-layer certainty stack:
- **Conformal** provides distribution-free marginal coverage (regardless of model correctness)
- **Bayesian LR** provides per-decision posterior uncertainty
- **Operator-rerank** provides session-aware preference modelling

Together: gate that auto-decides when ANY of the three signals is confident, and routes to operator only when ALL three are uncertain. Compounds to **~80% operator-click reduction** without sacrificing safety.

---

## Part 6 — The honest failure-mode picture

The six failure modes that recur across every learning engine, from the literature:

### 1. The corrupted-foundation cascade

If your reward signal is broken, every downstream learner trains on noise. GenLab lived this with the engagement_rate=0 bug for 17 days. The drift detection PR (#605 in the autonomy research) would catch the next instance in 24h instead of 17 days.

**Application:** Engine 1.1-1.5 (Tier 1) all learn from operator clicks (clean signal) — immune to this. Engine 2.x onwards must wait for engagement signal to stabilise.

### 2. The self-praise loop

Critique with the same model that generated produces "looks great to me!" Reflexion's degeneration-of-thought.

**Application:** Engine 1.3 (Critique-Rewriter) must use **either a different model OR a rubric the generator didn't see**. Lever K already does this correctly by using a critique prompt that explicitly cites principles the generator wasn't given.

### 3. Mode collapse from self-training

STaR / ReST / Constitutional AI all narrow output diversity when iterated.

**Application:** Engine 20 (Diversity Penalty) specifically defends against this. Engine 15 (LoRA fine-tune) is in Tier 4 partly because of this risk.

### 4. Reward hacking on proxy metrics

Bandits given click-through reward optimize for clickbait. Synthetic preference data exhibits linear separability that DPO exploits as shortcut.

**Application:** Engine 18 (Synthetic preference data) is in Tier 4 specifically for this. Engine 16 (DSPy MIPROv2) needs **composite metric** (classifier × banned-phrase × length × diversity) to prevent metric Goodhart.

### 5. Memory contamination

Hallucinated memories get written back to the store and retrieved later as "known facts."

**Application:** Engine 14 (Hook-RAG) needs **provenance** (source_blueprint_id, written_at) so the system can trace which past hook drove which decision. Half-life decay protects against stale-winner overfit.

### 6. Skill / preference drift invisibility

A skill or preference learned six months ago is still firing today, but assumptions (platform algorithm, audience, niche framing) have all changed.

**Application:** Every engine needs **drift detection**. Engine 5 (Source quality) has decay_hazard built-in via Cox model. Engine 24 (Posting-time v2) uses discounted Thompson sampling. Engine 14 (Hook-RAG) uses half-life decay. **Without drift detection, every learning loop has a built-in expiration date.**

---

## Part 7 — Concrete next-PR queue

Translating Tier 1 + Tier 2 into specific PRs. Combined with the autonomy research's Tier 1 (PRs #604/#605/#606), this is the full near-term agent-improvement queue:

| PR | Title | Tier | Effort | Operator Action |
|---|---|---|---|---|
| #604 | feat(deploy): activate auto-deploy workflow | Autonomy T1 | XS | Add 2 GH secrets |
| #605 | feat(observability): daily engagement-rate drift detection | Autonomy T1 | S | None |
| #606 | feat(autonomy): AUTO #2 enable for gaming niche | Autonomy T1 | XS | Review YAML |
| #607 | feat(generation): hook diversity penalty / DPP filter | Learning T1.1 | S | None |
| #608 | feat(scheduling): conformal selective prediction router | Learning T1.2 | M | None |
| #609 | feat(generation): critique-rewriter loop | Learning T1.3 | S | None |
| #610 | feat(learning): click-rationale taxonomy via LLM | Learning T1.4 | S | None |
| #611 | feat(scheduling): Bayesian LR gate with Thompson sampling | Learning T1.5 | M | None |

**8 PRs total. All Claude-shippable. Estimated 12-18 hours of Claude work + 30 min operator action (just the 2 GH secrets).**

After all 8 land:
- Operator review burden drops ~40% (Phase 1 effects from learning engines)
- Deploy bottleneck closed permanently
- Foundation drift caught within 24h instead of weeks
- AUTO #2 expands from 1 niche to 2 niches with calibration-correct evidence
- Hook generation no longer suffers template overfit
- Conformal router reduces operator clicks 30-50%
- Bayesian Thompson sampling replaces hand-tuned rollout
- Click rationale unlocks per-category analytics

Then wait 4 weeks for engagement data to clean up; ship Phase 2 (T2.1-T2.5) for the next ~10pp gains.

---

## Part 8 — Strategic framing for the operator

### Why this list matters more than the autonomy roadmap

The earlier autonomy research (`AGENT-AUTONOMY-RESEARCH.md`) identified the right activation order. This research identifies **what to build INSIDE that order to get compounding intelligence improvements**.

Autonomy without learning = a smart agent that ships the same content style forever.
Learning without autonomy = a learning system that needs operator approval for every learned action.
**Together = a system that gets sharper per niche per week without operator effort.**

That's the moat. Hootsuite/Buffer/Velocity will catch up on autonomous publishing. None of them are building the calibration-then-compound-learning architecture.

### Why operator-click-driven learning is the cheat code

Every "smart" content automation tool is trying to learn from engagement signal — which is delayed, noisy, platform-dependent, and dominated by ranker changes you can't observe. GenLab has a SECOND signal channel that no competitor has: **operator clicks**. 263 rows accumulated in 13 days. Clean. Cheap. Always available.

The Tier 1 engines exploit this asymmetric advantage. Conformal + Bayesian LR + Click-rationale + Critique-rewriter + Diversity penalty all ship using operator clicks (or no learning at all) — meaning **GenLab's smartest engines can land while the engagement signal is still being repaired**. By the time engagement data is trustworthy again, Phase 1 will be live and Phase 2 will compound on it.

### Why scale is GenLab's friend, not foe

At ~17 publishes/day, GenLab is too small for fine-tuning, RLHF, large-scale DPO, transformer hook generators, or any of the techniques that dominate the 2026 SOTA literature. **This is GenLab's edge.** Every Tier 1 + Tier 2 engine in this research is calibrated for small-data regimes — Bayesian methods, conformal prediction, RAG over retrieval, prompt optimization, bandits with shrinkage. These ship faster, debug faster, and don't require GPU infrastructure. They're invisible to competitors building 70B models who can't economically deploy them at 5-channel scale.

Once GenLab has 50 channels and 100k operator clicks, the Tier 4 engines become viable. Until then, **small-data sophistication > large-data brute force**.

### The thirteen-PR queue if everything ships in 2 months

| Phase | PRs | Expected combined effect |
|---|---|---|
| Autonomy Tier 1 (PRs #604/#605/#606) | 3 | Auto-deploy + drift detection + gaming AUTO #2 |
| Learning Tier 1 (PRs #607-#611) | 5 | Operator review −40%, template overfit eliminated, automated AUTO #2 ramping |
| Learning Tier 2 (5 more PRs) | 5 | Hook generation +10-20% quality, source pool healthy, prompt optimised per niche |

Total: 13 PRs over 2 months. Estimated combined operator-burden reduction: ~70%. Estimated combined hook-engagement lift: ~15-25%.

The compound effect after 6 months: GenLab's agent ships content of materially better quality than competitor SaaS tools while requiring ~30 minutes of operator attention per week instead of per day.

---

## Sources & references

**Predictive engines:**
- Pre-publish engagement: regression heads on multimodal embeddings (cited inline)
- Hawkes Intensity Process: [Rizoiu et al. WWW'17 (arXiv 1602.06033)](https://arxiv.org/abs/1602.06033), [DeepHawkes CIKM'17](https://github.com/CaoQi92/DeepHawkes)
- Source quality / LTV: [Customer LTV with Gradient Boosting](https://www.jisem-journal.com/download/06_Arjun_Feb_2019_JISEM.pdf)
- Sponsorship trajectory: Hierarchical Contextual Uplift Bandits (Dream11, arXiv 2601.14333)

**Preference learning engines:**
- DPO: [Rafailov 2023, arXiv 2305.18290](https://arxiv.org/abs/2305.18290)
- IPO: [Azar 2024, arXiv 2310.12036](https://arxiv.org/abs/2310.12036)
- KTO: [Ethayarajh 2024, arXiv 2402.01306](https://huggingface.co/papers/2402.01306)
- Random Is Hard to Beat: [arXiv 2604.02766](https://arxiv.org/abs/2604.02766)
- Conformal prediction: [Angelopoulos & Bates Gentle Intro](https://people.eecs.berkeley.edu/~angelopoulos/publications/downloads/gentle_intro_conformal_dfuq.pdf), [Reliable Small Datasets arXiv 2512.04566](https://arxiv.org/abs/2512.04566)
- IPS / DR OPE: [Dudik Langford Li 2011](https://arxiv.org/abs/1103.4601), [Swaminathan & Joachims SNIPS 2015](https://papers.nips.cc/paper/2015/hash/39027dfad5138c9ca0c474d71db915c3-Abstract.html)
- Bayesian preference learning: [Chu & Ghahramani GP Preference Learning ICML 2005](https://www.gatsby.ucl.ac.uk/~chuwei/paper/icmlPL.pdf), [PG-TS Logistic Bandits NeurIPS 2018](http://papers.neurips.cc/paper/7713-pg-ts-improved-thompson-sampling-for-logistic-contextual-bandits.pdf)
- DAgger / behavior cloning: [Ross Gordon Bagnell AISTATS 2011](https://arxiv.org/abs/1011.0686)

**Generative engines:**
- DSPy MIPROv2: [Stanford NLP docs](https://github.com/stanfordnlp/dspy/blob/b40f359ec567a04a7f8d1d5d1a744ca9c32d5339/docs/docs/deep-dive/optimizers/miprov2.md), [CallSphere 2026 case studies](https://callsphere.ai/blog/vw8g-dspy-prompt-optimization-mipro-2026)
- LoRA/QLoRA: [Hu 2021 LoRA](https://arxiv.org/abs/2106.09685), [Dettmers 2023 QLoRA](https://arxiv.org/abs/2305.14314)
- Hybrid RAG: [AppScale 2026](https://appscale.blog/en/blog/hybrid-search-and-reranking-production-rag-bm25-dense-cross-encoder-2026)
- Self-Refine: [Madaan et al. 2023 arXiv 2303.17651](https://arxiv.org/pdf/2303.17651)
- DPP for diversity: [arXiv 2509.04784 2025](https://arxiv.org/html/2509.04784v1)
- More is Less (synthetic preference pitfalls): [arXiv 2504.02193](https://arxiv.org/pdf/2504.02193)

**Strategic / meta engines:**
- Hierarchical Thompson Sampling: [Aouali et al. AISTATS'23](https://proceedings.mlr.press/v206/aouali23a/aouali23a.pdf), [Slivkins MAB book arXiv 1904.07272](https://arxiv.org/abs/1904.07272)
- Prototypical Networks: [Snell et al. 2017](https://dl.acm.org/doi/10.5555/3294996.3295163)
- LiMAML cross-task: [LinkedIn KDD'24, arXiv 2403.00803](https://arxiv.org/abs/2403.00803)
- BERTopic: [Maarten Grootendorst docs](https://maartengr.github.io/BERTopic/index.html)
- Discounted Thompson Sampling: [arXiv 2305.10718](https://arxiv.org/abs/2305.10718)
- Sleeping/Recovering Bandit (Duolingo): [Yancey & Settles KDD'20](https://research.duolingo.com/papers/yancey.kdd20.pdf)

---

*This document is meant to be revised. Next revision: after Tier 1 ships (within ~3 weeks), update Part 3 with measured outcomes (actual operator-click reduction from conformal router, actual diversity penalty rejection rate, etc.), promote any Tier 2 items whose engagement data prerequisites cleared, and reconsider the Tier 4 list given new evidence.*
