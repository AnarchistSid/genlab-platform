# Gen Lab — "Genius" Program Roadmap

**Author**: Session 2026-08-13→14, in response to operator directive
"the system is to be made into a genius"

**Scope**: bring Gen Lab from ~60% of the north-star vision (see CLAUDE.md
"agent we're building") to ~90-95% — the point where the operator's role
shifts from routine review to strategic direction + novel-situation
handling.

**Timeframe**: ~13 weeks (~40-50 focused ~4h sessions) for Phases 0-5.
Phase 6 (SaaS) adds 3 more weeks. Phase 7 (research frontier) is
open-ended.

**Guiding principle**: order by DEPENDENCY, not by ambition. The reward-
signal fix in Phase 0 compounds through everything downstream — do it
first even if the ambition is calling somewhere else.

---

## Baseline (2026-08-14, morning after this session)

**7 north-star capabilities, honest current state:**

| # | Capability | Current | Target after Phase 5 |
|---|---|---|---|
| 1 | Multi-channel content generation | 85% | 95% |
| 2 | Trend & content intelligence | 70% | 90% |
| 3 | Continuous learning | 65% | 92% |
| 4 | Multi-channel growth | 40% | 80% |
| 5 | Professional-grade judgment | 55% | 88% |
| 6 | Monetization intelligence | 50% | 85% |
| 7 | Engagement automation | 60% | 82% |

**Weighted overall**: ~60% → ~88% projected after Phase 5.

**What "genius" means concretely for Gen Lab**:
1. Follower growth is the primary optimization target, not per-post
   engagement proxy
2. All routine decisions (arm selection, blueprint approval, gate
   tuning, cost management, credential rotation, platform outage
   detection) run autonomously
3. Bad decisions get auto-reverted within 48h (outcome verifier)
4. Portfolio-level intelligence shifts effort toward high-ROI niches
   automatically
5. Content quality signal is multi-modal, not binary SUCCESS
6. Competitor ecosystem is monitored + learned from
7. Operator's role: strategic direction, novel situations, unblocking
   the system when it escalates

---

## PHASE 0 — Foundation: fix the reward signal (~1 week, 5 sessions)

**Why first**: bandit + strategist + calibration all optimize a Goodhart-
broken signal (binary SUCCESS reward). Everything downstream compounds
better after this. The strategist itself has yelled about this in 36
proposals over 5 weeks (all correctly identified, all rejected as stale
during session 2026-08-13 because operator was told "reward loop is
populated" — but the loop is populated with the WRONG signal).

### Task 0.A — Percentile-based reward (2 sessions)

Current: `reward = 1.0 if published else 0.0` in `reward_shaper.py`.

Target: `reward = percentile_of(post.engagement, niche_baseline_at_age)` —
a post that got 50th-percentile engagement for a same-age post in the
same niche gets reward=0.5. Best posts get reward→1.0; worst get →0.0.
Continuous signal that separates "published" from "grew the channel".

Files to touch:
- `genlab-core/src/genlab_core/learning/reward_shaper.py` — new
  `compute_percentile_reward()` function
- `genlab-core/src/genlab_core/learning/metric_collector.py` — feeds
  reward computation with age-matched engagement
- New table: `niche_engagement_baseline` — rolling percentiles per
  (niche, platform, post_age_hours_bucket)
- New alembic migration + daily refresh cron
- Pin tests: 30+ (edge cases: empty baseline, first post ever, age
  older than 30d, platform with no reward-eligible data)

Success criteria:
- Bandit posterior updates reflect NEW reward within 7 days of ship
- Strategist stops emitting "reward signal broken" proposals for 2+
  consecutive weekly runs

Kill criteria:
- If new reward signal is still Spearman-0 with follower delta after
  2 weeks of data, the mapping (percentile → reward) is wrong. Try
  log-scale or median-relative instead.

### Task 0.B — Daily follower snapshot (1 session)

Currently: `follower_count = unknown` in `state_collector.collect()` for
all 5 niches. Strategist has proposed this 3× — genuinely load-bearing.

Files:
- New script `scripts/snapshot_follower_metrics.py`
- Hits each platform's account metrics API (existing platform clients
  have the auth)
- Writes to new `channel_state_snapshots` table
- Daily systemd timer at 04:00 UTC (before strategist runs Sunday)

Success criteria:
- 7 consecutive days of populated follower_count for all 5 niches
- Strategist's `state` object shows real numbers instead of `unknown`

### Task 0.C — Reward signal audit dashboard card (1 session)

Currently: no visibility into what the bandit is actually learning.
Operator can't tell if reward signal is healthy without running SQL.

Files:
- New endpoint `/api/v1/learning/reward-audit`
- New card `RewardSignalAuditCard` on Mission Control
- Shows: reward distribution per (niche, platform), Spearman vs
  follower delta, staleness flag

Success criteria:
- Card renders for all 5 niches
- Spearman > 0.15 for at least ai_creators (best-signal niche)

### Task 0.D — Backfill 30 days of historical rewards under new signal (1 session)

Bandit posteriors trained on old-signal need re-derivation. Otherwise
first 60d of new-signal decisions fight legacy priors.

Files:
- New script `scripts/backfill_percentile_rewards.py`
- Walks `pending_feedback` last 30d, recomputes reward under new formula
- Bandit arm re-fit from backfilled reward

Success criteria:
- All 5 niches have new-signal reward for last 30d of published posts
- Bandit posterior means shift from old to new signal within 24h

---

## PHASE 1 — Close the strategist loop (~1 week, 5 sessions)

**Why next**: strategist proposes weekly. If the loop doesn't close (bad
decisions revert, decisions get graded), the system accumulates
degradation. Phase 0 gave it a better signal; Phase 1 gives it feedback.

### Task 1.A — Outcome verifier runner + rollback (1 session)

Scaffold shipped in `375494bc`. Wire the runner:
- Alembic migration for `strategist_outcome_verification` table
- Postgres impl of `MetricSnapshotProvider` (reads
  `pending_feedback.reward_48h` grouped by niche+platform)
- Postgres impl of `VerificationRecordStore`
- Wire `Verifier.register()` in `strategist_actions.apply_pending_actions`
- New `scripts/run_outcome_verifier.py` + `genlab-outcome-verifier.timer`
  every 6h
- Auto-rollback: SQL that reverses `arm_add` (mark paused) and
  `reward_weight` (reset to baseline) when `Verdict.REGRESSED`

Success criteria:
- 3+ verified proposals per week with baseline+t+48h values
- At least 1 auto-rollback fires within 2 weeks

### Task 1.B — LLM reviewer runner wire (1 session)

Scaffold shipped in `375494bc`. Flag set on prod (`GENLAB_LLM_REVIEWER_
ENABLED=1`). Wire the consumer:
- Modify `auto_accept_strategist_proposals.py` main loop: after all
  heuristic classifiers abstain, call `Reviewer.review(proposal, niche_id,
  state_snapshot)`
- Cost budget: skip if daily Anthropic spend > $0.50
- `action_taken_source='llm_reviewer_v1'` tag
- Live-test on prod: 3-5 currently-abstained proposals, verify Haiku
  returns valid JSON

Success criteria:
- Auto-action rate on abstained proposals ≥40%
- Zero JSON parse failures (parse_verdict gracefully abstains on any
  malformed output, so this is a strong claim about Haiku's output
  discipline)

### Task 1.C — Meta-learning: score which classifier decisions helped (1 session)

Currently: no data on whether accepted proposals actually improved
metrics. Rate-limit tuning is intuition-based.

Files:
- New table `classifier_decision_outcomes` — links every auto-accept
  to its 30d outcome (metric moved / didn't / regressed)
- New endpoint `/api/v1/learning/classifier-quality`
- New Mission Control card showing per-classifier per-type accuracy

Success criteria:
- Per-classifier accuracy rate visible on Mission Control
- Rate limit per type auto-suggests bump/tighten based on accuracy

### Task 1.D — Expand AUTO #2 to 4 remaining niches (2 sessions)

Currently: only `ai_creators` enrolled (calibration 83.8%, needs 90%).
Gaming enrollment was reverted 2026-07-17 per rule #22. Sports/movies/
anime never enrolled.

Two sub-tasks:
1. Get ai_creators to 90%: tighten `min_confidence` OR wait for 6 more
   operator agreements (~1 week of dashboard use)
2. Enroll gaming/sports/anime/movies once each hits 90% calibration.
   Rule #22: check confusion matrix breakdown NOT just agreement %.

Success criteria:
- All 5 niches at 90%+ calibration with `min_confidence` policy set
- Operator dashboard-review volume drops from ~150/wk to <30/wk

---

## PHASE 2 — Portfolio & prediction (~2 weeks, 8 sessions)

**Why next**: Phase 0+1 optimize WITHIN niches. Phase 2 optimizes
ACROSS niches + predicts failures.

### Task 2.A — Cross-niche transfer priors flip (0.5 session)

Infrastructure shipped 2026-07-23 in commit `f624521d`. Flag-off
because operator wanted to eyeball the numbers first.

Files:
- Flip `GENLAB_CROSS_NICHE_TRANSFER_ENABLED=1` on prod
- Verify anime FB-dominance signal appears in sports/movies transferred
  priors after 1 weekly run
- Rollback if any niche's arm-selection regresses

Success criteria:
- Sports+movies FB reward increases by 5%+ within 4 weeks

### Task 2.B — Portfolio LinUCB above per-niche bandits (2 sessions)

Currently: each niche's bandit is independent. No layer decides "shift
30% of movies budget to anime because anime is 3× the ROI this month".

Files:
- New `PortfolioLinUCB` class in `learning/`
- Feature vector: (niche follower growth rate, niche cost, niche
  conversion rate, niche engagement percentile)
- Arms: per-niche budget allocations (5-arm bandit)
- Consumer: `pipeline_runner` reads current arm to decide fetch depth,
  LLM budget, publish frequency per niche

Success criteria:
- Weekly budget shifts of 10-30% between niches based on ROI signal
- Aggregate follower growth improves 15%+ within 8 weeks

### Task 2.C — SLO time-series forecasting (1 session)

Currently: SLO alerts fire on threshold cross. All tonight's fixes
(cookies, source diversity, strategist 4k) were reactive.

Files:
- EWMA smoothing on `pipeline_metrics.duration_ms`, `zero_blueprints`
  rate, `download_failure_rate`
- Alert 24h ahead of projected threshold breach
- New card `SLOForecastCard` on Mission Control

Success criteria:
- 60%+ of alerts fire in "forecast" state (before actual breach)

### Task 2.D — Autonomous cost throttling (1 session)

Anthropic credit exhaustion caused 3-week silent strategist gap (07-13
→ 08-09). No layer auto-throttled.

Files:
- New `CostBudgetGate` in `cost/` — reads daily Anthropic + OpenAI spend
- Throttle rules: if spend > $5/day, reduce LLM-call frequency in
  strategist + writer by 50%; if > $10/day, pause LLM-optional callers
- Emergency shutoff at $20/day

Success criteria:
- Daily spend variance < 30% week-over-week
- Zero silent-outage days from credit exhaustion

### Task 2.E — Autonomous credential rotation (2 sessions) — Phase 6 blocker

Rule #33 SaaS blocker. Currently manual + risky.

Files:
- New `scripts/rotate_credentials.py` — walks credential inventory,
  rotates one at a time with zero-downtime rollout
- New `credential_rotation_state` table for audit
- Rotation runbook at `.audit/RUNBOOK_credential_rotation.md` (exists,
  needs update)

Success criteria:
- All rotatable credentials on 90-day rotation schedule
- Zero unplanned downtime from rotation

### Task 2.F — Change-point detection on platform reward (1 session)

Currently: platform algorithm changes = silent degradation until human
notices.

Files:
- Bayesian change-point detection on per-(niche, platform, hour_bucket)
  reward distribution
- Alert when posterior probability of change > 90%
- Auto-shift bandit exploration budget toward changed platform to
  re-learn

Success criteria:
- 3+ change-points detected + adapted per year
- Recovery time from platform change < 7 days (currently: unknown,
  never measured)

### Task 2.G — Meta-strategist (0.5 session)

Reviews strategist's own quality. Would have caught 4k-token bug in
1 week not 5.

Files:
- New `scripts/run_meta_strategist.py` — weekly, reviews strategist
  proposals from previous 4 weeks against their outcome_verifier
  results
- Grades per-proposal-type accuracy
- Emits recommendations: "trust type X more, tighten type Y"

Success criteria:
- Strategist per-type accuracy visible on Mission Control
- Rate limits auto-tune based on meta-strategist verdicts

---

## PHASE 3 — Ecosystem awareness (~3 weeks, 10 sessions)

**Why next**: Phases 0-2 optimize based on OUR data. Phase 3 brings in
competitor signal + trend signal + monetization automation.

### Task 3.A — Competitor monitoring (3 sessions)

Currently: we know what OUR bandit likes. We don't know what
MKBHD/PewDiePie/Marques Brownlee are doing that works 5×.

Files:
- Config: `configs/competitor_watch.yaml` — top 10 creators per niche
  with YouTube channel IDs
- New `scripts/fetch_competitor_content.py` — pulls their last 20
  videos via YouTube Data API, extracts hook + view count + engagement
- New table `competitor_content_deltas` — our metrics vs theirs
  matched on niche + posting time
- Strategist gets a new `competitor_context` field in its state
- New Mission Control card

Success criteria:
- Strategist emits 3+ competitor-informed proposals per week
- Hook style diversity increases 30%+ over 4 weeks

### Task 3.B — Trend anticipation flag flip + validation (0.5 session)

Infrastructure shipped 2026-07-01→02 sprint. Flag OFF because operator
wanted to see accuracy first.

Files:
- Flip `GENLAB_TREND_ANTICIPATION_ENABLED=1`
- Validate against `TrendAnticipationAccuracyCard` after 2 weeks
- Rollback if accuracy < 60%

Success criteria:
- Blueprint accept-rate improves 10%+ within 4 weeks
- No regression in per-post engagement

### Task 3.C — Sponsorship auto-outreach activation (2 sessions)

Infrastructure shipped PRs #481-#490 (2026-06-23). Currently passive:
Mission Control cards + printable media kits + copy-to-clipboard
buttons. No auto-send.

Files:
- Wire outreach to actual sending (SMTP via SendGrid/Postmark, or
  Outlook API since M365 is integrated)
- Weekly outreach cadence per niche once tier ≥ Bronze
- Track outreach → response → deal in `sponsorship_pipeline` table
- New Mission Control card: outreach pipeline funnel

Success criteria:
- 10+ outreach messages sent per week across niches
- Response rate > 5%
- 1+ sponsorship deal closed within 12 weeks

### Task 3.D — A/B experimentation framework (3 sessions)

Currently: strategist proposes experiments, apply worker runs them, no
A/B design, no power analysis, no stop-on-significance.

Files:
- New `experiment_runner.py` module
- Sample size calculator (Bayesian with prior from bandit state)
- Auto-stop at significance OR max duration
- Auto-write results back to strategist state

Success criteria:
- 5+ properly-powered experiments per month
- Zero "ran for 2 weeks, still no signal" wasted experiments

### Task 3.E — Cross-platform amplification wire (1.5 sessions)

Currently: publish independently to each of 4 platforms. No
cross-post-references (e.g., IG story linking to YT video).

Files:
- Cross-post scheduling: IG story 2h before YT video drop
- Threads reply thread with YT link 30 min after YT publish
- FB comment pinning our own YT link on high-reach posts

Success criteria:
- Cross-platform traffic increases 20%+ within 8 weeks

---

## PHASE 4 — Content quality intelligence (~4 weeks, 13 sessions)

**Why next**: Phases 0-3 optimize DELIVERY. Phase 4 optimizes what we
actually make.

### Task 4.A — Multi-modal quality signal (4 sessions)

Currently: hook classifier text-only. VMAF gate fails-open. No joint
signal.

Files:
- Extract visual features from rendered videos (color palette, motion
  energy, cut frequency, brand-consistency)
- Extract audio features (energy variance, dialogue density,
  music-to-voice ratio)
- Joint quality model: hook_score × visual_score × audio_score →
  combined 0-1 quality metric
- Feed into bandit as reward multiplier

Success criteria:
- Per-post reward variance decreases 20%+ (better signal-to-noise)
- Bandit converges faster on winning combinations

### Task 4.B — Aesthetic quality model (3 sessions)

Fine-tune a small vision model (e.g., CLIP variant) on our top-100
performers vs bottom-100. Score every new render before publish.

Files:
- New service: image quality scorer
- Training data: reward > p80 = positive, reward < p20 = negative
- Retrain monthly

Success criteria:
- Model AUC > 0.65 on held-out set (better than chance for a small
  dataset)
- Pre-publish quality score correlates 0.3+ with post-publish reward

### Task 4.C — Content ideation prompt informed by top styles + trends (2 sessions)

Currently: writer sees only story summary. Doesn't know "top hook style
this week is X".

Files:
- Compute top-3 hook styles per niche per week from bandit state
- Feed into writer's system prompt as "style guidance"
- Ship behind flag first: A/B against control

Success criteria:
- Guided writes get 15%+ higher reward than control after 4 weeks

### Task 4.D — Persona voice drift detector (1 session)

Each niche has `persona.yaml`. Nothing checks content matches persona
over time.

Files:
- LLM call every 20th publish: "does this hook match {persona}?"
- Alert on drift score < 0.6
- Track drift over time

Success criteria:
- Drift detection catches at least 1 real drift per quarter

### Task 4.E — Automated content ideation (3 sessions)

Bigger scope: system proposes new content ideas beyond bandit arm
exploration. LLM-driven, seeded on trends + top-competitor + persona.

Files:
- New `scripts/run_content_ideator.py` — weekly
- Emits: 10-20 idea candidates per niche
- Ideas go into a "content ideas pool" table
- Writer picks from pool when normal source (trending videos) is
  low-signal

Success criteria:
- 20%+ of published content originates from ideation pool by month 3
- Ideation-pool content reward matches or beats trending-video reward

---

## PHASE 5 — Human-in-loop reduction (~2 weeks, 6 sessions)

**Why last (of engineering phases)**: after Phases 0-4, the system is
capable enough that removing human review is safe. Doing this earlier
risks silent degradation.

### Task 5.A — Auto-tune calibration thresholds (1 session)

When AUTO #2 confusion matrix skews (e.g., 6 FP against 0 FN in
ai_creators), auto-suggest `min_confidence` bump.

Files:
- New `scripts/auto_tune_calibration.py` — weekly
- Reads `auto_approval_calibration`, suggests `min_confidence` delta
- Auto-apply if suggestion within [-0.05, +0.05] range

Success criteria:
- Calibration reaches 95%+ within 4 weeks of activation for all
  enrolled niches

### Task 5.B — Meta-strategist accepting/rejecting proposals autonomously (2 sessions)

Currently: session 2026-08-13 manual review of 166 proposals took
several hours. Meta-strategist should do this weekly without operator.

Files:
- Combine LLM reviewer (Phase 1B) + outcome verifier (Phase 1A) + meta-
  strategist (Phase 2G) into an autonomous accept/reject engine
- Escalate only when: confidence < 0.5 OR outcome verifier hasn't
  matured OR novel proposal type

Success criteria:
- Operator's strategist-review volume drops 90%+
- Accept/reject quality matches operator's 85%+ agreement rate

### Task 5.C — Autonomous flag-flip escalation (2 sessions)

Currently: operator manually flips flags (
`GENLAB_MAX_AUTO_ACCEPTS_PER_WEEK`, `GENLAB_CROSS_NICHE_TRANSFER_ENABLED`,
etc.). System should propose + apply flips based on classifier maturity.

Files:
- New `scripts/autonomous_flag_manager.py` — daily
- Reads classifier accuracy, outcome verifier results, alerts
- Proposes flip in operator's daily briefing
- Auto-flips when confidence > 0.9 AND no operator override in 24h

Success criteria:
- Operator manual flag flips drop 80%+
- Zero unintended flag flips (that operator disagrees with)

### Task 5.D — Operator daily briefing bot (1 session)

Currently: operator's role is "check dashboards + review queues +
approve blueprints". Should be "read 5-line summary + intervene if
needed".

Files:
- New `scripts/generate_operator_briefing.py` — daily 06:00 UTC
- LLM writes: what changed yesterday, what worked, what didn't, what
  needs your judgment today
- Delivered via email + dashboard card

Success criteria:
- Operator spends <30 min/day on Gen Lab (down from ~2h estimated)

---

## PHASE 6 — SaaS/multi-tenant readiness (~3 weeks, 10 sessions)

**Only if** you want to open Gen Lab to external customers. Skip if
staying single-tenant.

### Task 6.A — Fix BYPASSRLS role attribute (rule #33, 1 session)

SaaS blocker. Runbook at `.audit/RUNBOOK_credential_rotation.md`.
Step 5b zero-rows gate closes it.

### Task 6.B — 34 psycopg bypass sites migration (2 sessions)

Documented in `[[deeper-cuts-audit-2026-07-16]]`. Each site skips
`pg_connect` and doesn't set niche context → RLS moot even if BYPASSRLS
is fixed.

### Task 6.C — Per-tenant credential vault + rotation (3 sessions)

Currently: single `.env` for all niches. Multi-tenant needs per-tenant
scoped credentials.

### Task 6.D — Tenant-scoped cost accounting (2 sessions)

Bill per tenant. Rate-limit per tenant. Prevent one tenant's usage from
exhausting shared quotas.

### Task 6.E — Operator onboarding tools (2 sessions)

`create_niche` tool exists (2026-Sprint 63). Extend to `create_tenant`
+ docs + walkthrough.

---

## PHASE 7 — Research frontier (ongoing)

Not on a timeline. Read papers, experiment, ship what works.

- **Non-Goodhart reward signals** — literature on reward hacking in RL
- **Genuine creativity ranking** — beyond templated arms
- **Aesthetic quality assessment** — beyond VMAF
- **Cross-modal coherence** (hook + visual + audio)
- **Multi-agent coordination** between per-niche bandits
- **Adversarial robustness** — platform algorithm changes
- **Meta-learning over meta-strategist** — Bayesian priors on which
  strategy types work per niche

---

## Execution logistics

### Session cadence
- **Target**: 3-4 sessions per week × ~4h each = ~14h/week
- **Weekend blocks preferred** (fewer interruptions)
- **Rest between phases** — 1 day off after each phase to observe metrics

### Progress tracking
- Update this doc at end of each session: mark tasks done, log
  deviations from plan, capture surprises
- Weekly summary in memory: `session-YYYY-MM-DD-genius-program-week-N.md`

### Cost tracking
- Anthropic API budget: $50/week during active development
- Autonomous cost throttling (Task 2.D) is itself a Phase 2 deliverable
  to prevent runaway spend

### Rollback discipline
Every phase's tasks ship behind feature flags (existing pattern —
`GENLAB_X_ENABLED`) so any regression can be reverted with one
`.env` edit. Kill-criteria checks after each phase; if metrics move
wrong direction, roll back before adding more.

### When to pause the program

Pause the whole program if any of these:
- Weekly follower growth turns negative for 2+ consecutive weeks
- Anthropic spend > $200/week (excluding one-off backfills)
- Operator's time-on-Gen-Lab increases week-over-week (opposite of
  goal)
- Two consecutive phases fail to hit success criteria (redesign
  needed, not push forward)

### When to accelerate

Accelerate (double session frequency) if:
- A phase hits success criteria in half the estimated time
- Follower growth rate accelerates (+50%+ week-over-week for 3
  weeks)

---

## Success metrics for the program

**North-star metric** (single number to beat):
- **Aggregate follower growth across all 5 niches over 90 days**
- Baseline: today's growth rate (measured post Phase 0.B)
- Target: 3× baseline by end of Phase 5

**Secondary metrics** (leading indicators):
- Operator time-on-Gen-Lab per week: target < 5h by Phase 5 (from ~15h)
- Auto-decision rate: target > 90% by Phase 5 (from ~50% today)
- Content quality (percentile reward): target median > 0.5 by Phase 4
  (from current ~0.15 average across in-scope platforms)
- Sponsorship revenue: target $500/mo per niche by Phase 3 completion

---

## What's already shipped (baseline, pre-Phase-0)

Tonight's session (2026-08-13→14) landed:
- yt-dlp cookies-file support unlocking YT source diversity
- Strategist 4k→16k token cap fix (silent 3-week gap resolved,
  4 weeks backfilled)
- Source-diversity SLO with two-layer creator check
- Extended auto-accept classifier (hour: shape, consensus fallback,
  stale + scope rejects)
- Per-type rate limits with env override
- Outcome verifier + LLM reviewer scaffolds (Phase 1 tasks are wire-
  ins, not new modules)
- 9 new AI creator channels + AI news + YT search + Product Hunt +
  Reddit OAuth stub
- 91 strategist proposals accepted, 61 rejected, all 5 channels
  scheduled through 08-14

Two flags pre-flipped waiting on next session:
- `GENLAB_MAX_AUTO_ACCEPTS_PER_WEEK=8` (aggregate cap)
- `GENLAB_LLM_REVIEWER_ENABLED=1` (no-op until Phase 1.B ships)

---

## Living document

This roadmap is a plan, not a contract. Amend as reality teaches us
what actually works. Every session should end by asking:

1. Did the task hit its success criterion?
2. Did we learn something that changes the next task?
3. Should we deviate from the plan?

Answer honestly. Deviate when the evidence warrants it. Don't grind
through a plan when the world has shifted.
