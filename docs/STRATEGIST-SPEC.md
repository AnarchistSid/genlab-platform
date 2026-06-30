# Strategist Meta-Layer — Architecture Spec

**Status**: spec, pre-implementation
**Author**: 2026-06-30 evening planning session
**Implementation target**: 2-3 week PR series, ships before 2026-07-21
**Cost target**: $50-200/month Anthropic API budget

---

## 1. What the Strategist is

The **Strategist** is an LLM-based weekly meta-cognition layer that sits ABOVE
the existing bandit/gate/classifier stack. It's the missing "manager" role that:

1. Reads the full state of each channel (metrics, posteriors, calibration, learnings)
2. Detects the current strategic phase per niche
3. Reviews last week's outcomes and proposes concrete adaptations
4. Generates causal hypotheses for observed patterns
5. Surfaces architectural changes to the operator with explicit reasoning

It is NOT:
- A replacement for the bandits (they remain the tactical execution layer)
- An auto-executor (every Strategist proposal requires operator approval)
- A real-time decision system (runs weekly, not per-blueprint)

---

## 2. Why this layer is the architectural keystone

The 5 gaps in the current system (causal reasoning, strategic shift,
generalization, novelty, self-modification) all share one root cause: the
optimizer stack has no "supervisor" that thinks ABOUT the optimization.

The Strategist provides this supervisor. It's the bone that connects:
- **Phase detection** → adjusts `PhaseConfig` → reward weights + gates change
- **Causal hypotheses** → seeds `learning_findings` → writer prompts get smarter
- **Novelty proposals** → adds new bandit arms → escapes local optima
- **Cross-niche patterns** → seeds `universal_playbook` → new niches inherit
- **Self-review** → proposes hyperparameter changes → operator approves

Without the Strategist, each of these requires a separate engineering effort
with separate data models. WITH the Strategist, they're all outputs of one
weekly cycle with one shared data structure.

---

## 3. Data model

### Table: `strategist_reports` (new)

```sql
CREATE TABLE strategist_reports (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  niche_id        TEXT NOT NULL,
  run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  week_of         DATE NOT NULL,  -- Monday of the analyzed week

  -- Inputs (state snapshot at run time)
  inputs_json     JSONB NOT NULL,
  -- {follower_count, engagement_rate_7d, top_arms, validation_interpretation,
  --   calibration_agreement, recent_publishes_summary, cost_per_blueprint, ...}

  -- LLM-generated outputs
  detected_phase  TEXT NOT NULL,
    -- BOOTSTRAP | GROWTH | OPTIMIZE | MONETIZE | DEFEND
  phase_evidence  TEXT NOT NULL,
    -- "follower_count=247, crossed 100 threshold 12d ago, engagement_rate
    --  declining last 3 weeks → recommend GROWTH"

  proposals       JSONB NOT NULL,
    -- [{type: "phase_shift" | "arm_add" | "gate_threshold" | "reward_weight"
    --       | "novelty_rate" | "playbook_update" | "manual_action",
    --   target: "ai_creators.phase",
    --   current: "BOOTSTRAP",
    --   proposed: "GROWTH",
    --   reasoning: "...",
    --   expected_impact: "...",
    --   risk: "low|medium|high",
    --   urgency: "ship_now|this_week|next_sprint"},
    --  ...]

  causal_hypotheses JSONB NOT NULL,
    -- [{pattern: "Posts with style:revelation get 3.4× reward",
    --   hypothesis: "Gaming audience responds to leak/spoiler content
    --                because community values pre-release info",
    --   confidence: "high|medium|low",
    --   evidence: ["arm_id=gaming:style:revelation n=167 reward=0.297",
    --              "vs gaming:style:patch_news n=89 reward=0.087"],
    --   testable_prediction: "If hypothesis holds, gaming:style:rumour
    --                         should also outperform; bandit will discover"},
    --  ...]

  weekly_summary  TEXT NOT NULL,
    -- Human-readable Slack-friendly summary

  -- Operator decisions (post-review)
  reviewed_at     TIMESTAMPTZ,
  reviewed_by     TEXT,
  proposals_accepted JSONB,
  proposals_rejected JSONB,
  operator_notes  TEXT,

  CONSTRAINT week_unique_per_niche UNIQUE (niche_id, week_of)
);

CREATE INDEX idx_strategist_reports_run_at ON strategist_reports(run_at DESC);
CREATE INDEX idx_strategist_reports_unreviewed
  ON strategist_reports(niche_id, run_at DESC)
  WHERE reviewed_at IS NULL;
```

### Table: `learning_findings` (new — surface for Strategist outputs)

```sql
CREATE TABLE learning_findings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  niche_id        TEXT NOT NULL,
  finding_text    TEXT NOT NULL,
  evidence_count  INTEGER NOT NULL,
  source          TEXT NOT NULL,  -- 'strategist' | 'manual' | 'analyst'
  source_report_id UUID REFERENCES strategist_reports(id),
  active          BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  superseded_by   UUID REFERENCES learning_findings(id)
);

CREATE INDEX idx_findings_active ON learning_findings(niche_id, active);
```

Writer prompts include top 5 active findings per niche as context.

### Table: `universal_playbook` (new — cross-niche patterns)

```sql
CREATE TABLE universal_playbook (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pattern_text    TEXT NOT NULL,
    -- "Posts published 06:30 UTC outperform 18:00 UTC by 2.1× across all niches"
  evidence_niches TEXT[] NOT NULL,  -- which niches observed this
  confidence      TEXT NOT NULL,  -- high | medium | low
  active          BOOLEAN NOT NULL DEFAULT true,
  source_report_id UUID REFERENCES strategist_reports(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 4. Prompt structure

### System prompt (versioned)

```
You are the Strategist for Gen Lab, a video-first content automation system
that runs 5 social media channels (ai_creators, gaming, sports, movies, anime)
across 6 platforms (Instagram, YouTube, Facebook, X/Twitter, Threads, TikTok).

Your role is the MANAGER above a tactical optimization stack:
- Bandits (LinUCB + Thompson) pick content arms per blueprint
- Gates (auto-approval with 5 checks + LLM judge for borderlines) filter publishes
- Classifiers (XGBoost hook quality, conformal router) provide confidence signals

Your job each week:
1. Detect the strategic phase per niche (BOOTSTRAP → GROWTH → OPTIMIZE → MONETIZE → DEFEND)
2. Review last week's publishes + engagement + bandit shifts
3. Generate causal hypotheses for observed patterns
4. Propose concrete adaptations with reasoning + risk assessment
5. Surface cross-niche patterns as universal playbook entries

You are NOT the operator. Every proposal requires operator approval. You write
proposals; the operator decides. Be specific, calibrated, and admit uncertainty.

Strategic phases:
- BOOTSTRAP (0-100 followers): novelty + reach > engagement. Ship aggressively.
- GROWTH (100-1K): shares + new_followers > engagement_rate. Optimize for spread.
- OPTIMIZE (1K-10K): engagement_rate + watch_time. Refine within learned patterns.
- MONETIZE (10K+): affiliate_clicks + conversion. Reward weights shift to revenue.
- DEFEND (plateaued): diversify formats + platforms to escape local optimum.

Output your analysis as structured JSON conforming to the schema you'll be given.
Avoid generalities. Cite specific arm_ids, blueprint_ids, reward values, sample sizes.
If you don't have enough evidence for a hypothesis, say "insufficient_evidence"
explicitly rather than guessing.
```

### User prompt template per weekly run

```
NICHE: {niche_id}
WEEK OF: {week_monday}
TIMESTAMP: {run_at}

CHANNEL STATE
-------------
Follower count: {follower_count} ({delta_vs_4w_ago})
Engagement rate 7d: {engagement_rate_7d}% ({delta_vs_7d_prior})
Watch time avg 7d: {watch_time_avg_7d}s
Total publishes last 7d: {n_publishes_7d}
Top performing post: {top_blueprint_id} ({top_metrics})
Bottom performing post: {bot_blueprint_id} ({bot_metrics})

BANDIT POSTERIORS (top + bottom 5 per arm type)
-----------------------------------------------
{bandit_state_summary}
# e.g.:
# style:revelation     n=167 reward=0.297
# style:bold_claim     n=88  reward=0.335
# source:youtube_trending__facebook  n=12 reward=0.094
# ...

VALIDATION HARNESS
------------------
Spearman last 7d: {spearman_7d} ({interpretation})  # interpretation in {'low signal', 'developing', 'useful'}
Calibration agreement: {agreement_pct} ({n_calibration_rows} rows)

CONFORMAL STATE
---------------
Coverage achieved: {coverage_pct}
Abstain rate: {abstain_pct}

COST EFFICIENCY
---------------
Cost per blueprint: ${cost_per_bp} ({delta_4w})
Cost per published post: ${cost_per_published} ({delta_4w})

RECENT PUBLISHES SAMPLE (top 5, bottom 5, random 5)
---------------------------------------------------
{publishes_table}

CROSS-NICHE COMPARISON (briefly)
--------------------------------
{other_niches_summary}

ACTIVE LEARNINGS (existing findings)
------------------------------------
{active_findings_for_this_niche}

LAST WEEK'S PROPOSALS (and operator decisions)
----------------------------------------------
{last_week_outcomes}

---

Generate your weekly report as JSON conforming to this schema:
{schema_definition}
```

### Output JSON schema

```json
{
  "detected_phase": "GROWTH",
  "phase_evidence": "Follower count crossed 100 on 2026-06-22, currently 247. Engagement rate stable but new follower velocity declining. Recommend GROWTH phase with reward weight shift to shares + new_followers.",

  "weekly_summary": "ai_creators is in early GROWTH phase. Bandit converging on style:comparison (n=24, reward=0.154) but novelty rate at 0.05 may be too low — consider raising to 0.15 to escape plateau. Cross-niche pattern: posts at 06:30 UTC outperform 18:00 UTC by 2.1× consistently.",

  "proposals": [
    {
      "type": "phase_shift",
      "target": "ai_creators.phase",
      "current": "BOOTSTRAP",
      "proposed": "GROWTH",
      "reasoning": "Follower threshold crossed; phase config should shift reward weights.",
      "expected_impact": "Reward weights move to shares (0.4) + new_followers (0.3) vs current views-heavy mix; bandit will reweight arm preferences.",
      "risk": "low",
      "urgency": "ship_now"
    },
    {
      "type": "novelty_rate",
      "target": "ai_creators.novelty_rate",
      "current": 0.05,
      "proposed": 0.15,
      "reasoning": "Bandit converged on style:comparison; engagement plateaued. More exploration needed to find next-best arm.",
      "expected_impact": "1 in ~7 publishes will be novelty mode. Short-term expected reward dip ~10%; medium-term potential lift if new arm found.",
      "risk": "medium",
      "urgency": "this_week"
    },
    {
      "type": "arm_add",
      "target": "ai_creators.arms",
      "current": null,
      "proposed": {"arm_id": "style:behind_the_scenes", "prior_alpha": 1, "prior_beta": 2},
      "reasoning": "Anime niche style:cliffhanger underperformed but style:bold_claim won — suggests audience wants definitive statements. Could ai_creators test similar 'behind the scenes definitive insight' style?",
      "expected_impact": "New arm starts at Beta(1,2); will need 10+ plays to differentiate from priors.",
      "risk": "low",
      "urgency": "next_sprint"
    }
  ],

  "causal_hypotheses": [
    {
      "pattern": "Posts with style:comparison get 1.5× reward vs style:question",
      "hypothesis": "AI creator audience values direct value comparison (e.g., 'X vs Y for video editing') over open-ended questions because they're solving specific tool-choice problems",
      "confidence": "medium",
      "evidence": [
        "ai_creators:style:comparison n=24 reward=0.154",
        "ai_creators:style:question n=18 reward=0.103",
        "Top 3 ai_creators blueprints last week were all comparison-style"
      ],
      "testable_prediction": "If hypothesis holds, ai_creators:style:vs_comparison should also outperform when introduced. Tracking blueprint_ids: [...]"
    }
  ],

  "universal_playbook_proposals": [
    {
      "pattern_text": "Posts published 06:30 UTC outperform 18:00 UTC by 2.1× consistently across all niches",
      "evidence_niches": ["ai_creators", "gaming", "anime", "movies", "sports"],
      "confidence": "high"
    }
  ]
}
```

---

## 5. Integration points

### Where Strategist outputs feed back into the system

1. **`PhaseConfig` reads detected_phase**
   - File: `genlab_core/scheduling/strategy_phase.py` (new)
   - Reward shaper reads phase-specific weights from PhaseConfig
   - Auto-approval gate reads phase-specific thresholds

2. **Writer prompts read active learnings**
   - File: `genlab_core/writing/video_content_writer.py`
   - Adds context: "Recent learnings for {niche_id}: {top_5_active_findings}"
   - Affects hook generation by giving LLM specific patterns to lean on

3. **Bandit arm registry reads new_arm proposals**
   - File: `genlab_core/learning/arm_loader.py`
   - When operator approves arm_add proposal, new arm gets registered with proposed prior

4. **Universal playbook influences cross-niche transfer**
   - File: `genlab_core/learning/arm_loader.py`
   - When a new niche launches, playbook entries seed its priors

5. **Dashboard surfaces unreviewed proposals**
   - New React component: `StrategistReportCard.tsx`
   - Shows latest report per niche + accept/reject UI per proposal
   - Banner alert when unreviewed reports >7 days old

### What the Strategist does NOT touch (boundaries)

- It does NOT modify env flags
- It does NOT modify code
- It does NOT auto-execute proposals
- It does NOT modify operator-controlled config (publishing.yaml, sources.yaml)

Operator approval is required for every proposal. The Strategist writes; the
operator decides.

---

## 6. Failure modes + mitigations

| Failure mode | Mitigation |
|---|---|
| LLM hallucinates fake patterns | Require minimum evidence_count per finding; reject low-confidence hypotheses |
| LLM proposes harmful actions | Type whitelist (only 7 proposal types allowed); operator approval gate |
| Strategist contradicts itself week-over-week | Include last week's report in input context; require "supersedes" reference when reversing |
| Cost overrun (Anthropic API) | Hard budget cap per run; fail-soft to "skipped this week" log |
| Slow LLM response blocks pipeline | Background timer, never blocks live pipeline path |
| Operator ignores reports | Dashboard banner after 7d unreviewed; Slack alert after 14d |
| Strategist proposals correlate poorly with actual impact | Track proposal outcomes in `strategist_reports.reviewed_at + outcome_metrics`; LLM sees its own track record |

---

## 7. Cost analysis

**Per-run estimate** (Claude Sonnet 4.6, large context):
- Input: ~15K tokens (state snapshot, posteriors, recent publishes, history)
- Output: ~3K tokens (structured JSON report)
- Per niche per week: ~$0.30 input + $0.30 output = $0.60

**Total monthly cost**:
- 5 niches × 4 weeks × $0.60 = $12/month base
- Plus ad-hoc re-runs after operator feedback: ~$10/month
- Plus error retries: ~$5/month
- **Estimated total: $25-50/month**

Well within $50-200/month budget target.

---

## 8. Implementation plan (3 PRs)

### PR Strategist-1: Foundation (Week 1)
**Files**:
- `genlab-core/migrations/versions/{new}.py` — schema for 3 new tables
- `genlab_core/intelligence/__init__.py` — package init
- `genlab_core/intelligence/strategist.py` — core class with stubs
- `genlab_core/intelligence/prompts.py` — system + user prompt templates
- `genlab_core/intelligence/state_collector.py` — gathers inputs from DB
- `genlab_core/intelligence/proposal_schema.py` — Pydantic models for output
- `tests/intelligence/test_strategist.py` — initial test scaffold

**What it does**: Strategist runs end-to-end on synthetic data, writes report
to DB. Does NOT integrate with live pipelines yet.

### PR Strategist-2: Live wire + cron (Week 2)
**Files**:
- `genlab-core/scripts/run_strategist.py` — CLI entrypoint
- `deploy/systemd-phase2/genlab-strategist.{service,timer}` — Sunday 02:00 UTC
- `dashboard/server/api/strategist.py` — endpoint for reports + accept/reject
- `dashboard/frontend/src/components/StrategistReportCard.tsx` — UI
- `tests/intelligence/test_state_collector.py` — uses real DB schema
- `tests/intelligence/test_proposal_integration.py` — end-to-end with mocked LLM

**What it does**: Strategist runs weekly on real prod data, writes reports,
operator reviews in dashboard. Proposals do NOT auto-execute yet.

### PR Strategist-3: Integration + auto-actions (Week 3)
**Files**:
- `genlab_core/scheduling/strategy_phase.py` — PhaseConfig reads from detected_phase
- `genlab_core/learning/reward_shaper.py` — modified to read PhaseConfig weights
- `genlab_core/scheduling/auto_approval_gate.py` — modified to read PhaseConfig threshold
- `genlab_core/writing/video_content_writer.py` — appends active findings to prompts
- `genlab_core/learning/arm_loader.py` — handles approved arm_add proposals
- `tests/scheduling/test_phase_config_integration.py`
- `tests/writing/test_writer_findings_integration.py`

**What it does**: Approved proposals automatically translate to live system
changes within 10 minutes of operator approval. Full closed loop.

---

## 9. Open questions for operator

1. **LLM provider**: Claude Sonnet 4.6 (recommended) or GPT-5? Sonnet
   is more cost-effective + better at structured output.

2. **Run frequency**: Weekly (Sunday 02:00 UTC) recommended. Or biweekly to
   conserve cost while channels are small?

3. **Operator review SLA**: Banner alert at 7d unreviewed, Slack alert at 14d?
   Or stricter?

4. **Proposal auto-execution**: Should LOW-risk proposals auto-execute after
   72h if operator silent? Or always require explicit approval?

5. **Cost cap**: Hard cap at $100/month (fails soft to "skipped"), or no cap?

6. **Initial phase per niche**: Should the first run auto-detect, or should
   operator set initial phase manually?

7. **Niche scope**: All 5 niches in PR Strategist-1, or start with ai_creators
   only as pilot?

---

## 10. What this unlocks

After Strategist-3 ships:
- Phase-aware reward shaping is live → bandits learn what matters for current
  phase, not abstract "reward"
- Causal hypotheses feed writer → hooks lean on patterns the operator has
  approved as real
- Cross-niche playbook influences new arm priors → 6th channel inherits 4
  months of learning
- Operator workload shifts from "approve each publish" to "approve weekly
  strategy" → 10× leverage on operator time

This is the architectural keystone. Without it, the 5 gaps require 5+ separate
features with no unifying logic. With it, they're outputs of one weekly cycle.

---

## 11. What this does NOT unlock (still need separate work)

- **Causal graph learning** (Gap 1c — heavy ML, research project)
- **Hierarchical Bayesian bandit** (Gap 3a — substantial PR, post-Strategist)
- **Trend-driven novelty** (Gap 4c — needs separate scraping infra)
- **Audience modeling beyond engagement** (Gap 6 — new gap, separate work)
- **Inverse RL reward learning** (Gap 5d — heavy ML, post-Strategist)
- **Channel growth strategy** (operator decision, not engineering)

These remain in the Tier 2/3 roadmap. The Strategist makes them easier to
implement because it provides the framework for "we detected a gap, here's a
proposal, operator approves, code ships."

---

## 12. References

- `docs/ROADMAP-2026-07.md` — overall roadmap context
- Memory: `bandit-decision-architecture-2026-06-30`
- Memory: `dormant-intelligence-engines-2026-06-30` (now superseded)
- Memory: `prod-state-2026-06-30-evening` (current prod state)
- Memory: `agent-learning-state-2026-06-30` (what the agent has learned)
- Memory: `system-blind-spots-2026-06-30` (related blind spots)
