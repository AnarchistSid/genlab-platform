# Gen Lab — Strategic Roadmap (post 2026-06-30 audit cycle)

**Written**: 2026-06-30 (after 33-commit session + 9 memory entries + 4 deep audits)
**Next review**: 2026-07-07 (after 1 week of post-deploy data)

---

## Part 1: Where We Honestly Are

### What's working
- **Pipeline produces content reliably** across 5 niches (15-28 posts/niche/month)
- **All 6 fetcher stages are firing** after today's `sources_config` fix
- **CI works at $0/month** via self-hosted runner on prod VPS
- **Schedule visible + full** on dashboard (35/35 week coverage)
- **Bandit foundation deployed** — backfill ran on 59 historical posts; validation harness measuring daily
- **Per-platform reward learning shipped** — bandit can now learn the 38× Facebook-vs-YouTube asymmetry
- **Operator UX improvements live** — batch-approve discoverable, source-performance card on dashboard
- **DR procedure documented + tested** — 65 deleted blueprints recovered today proves backup works

### What's NOT working
- **Channels are too small** to generate engagement variance — max 5-9 likes/post, max 295-849 reach
- **Reply system dormant** — 0 replies posted in 30 days (no new commenters to reply to)
- **Bandit reward variance near zero** — validation harness says "low signal" because rewards aren't differentiating
- **Gaming auto-approval gate broken** — 22.4% agreement, predicts OPPOSITE of operator 78% of time
- **4 intelligence engines shipped but ENV_FLAG=OFF** (conformal router, Bayesian gate, hook diversity, hook classifier training)

### Honest diagnosis
**The system is structurally capable of intelligence but lacks INPUTS to be intelligent with.** The agent has the brain — it doesn't have the audience that produces the data it needs to learn from. **Channel growth is the primary bottleneck, not engineering.**

---

## Part 2: The Critical Path

```
Channel Growth (audience size)
        │
        ↓
Engagement variance increases
        │
        ↓
Bandit reward signal becomes informative
        │
        ↓
Validation harness shows Spearman > 0.3
        │
        ↓
AUTO #2 can safely ramp to 50%+
        │
        ↓
Operator workload reduces meaningfully
        │
        ↓
Operator time freed for strategy / growth
        │
        ↓
(Compounds: better content + faster decisions)
```

**Single decision that unblocks the most: channel growth strategy.**

Today's options offered (operator chose 1b "reply-only safe" which is dormant due to no input):
- A. Paid audience seeding ($250-500 one-time) — fastest path
- 1a. Human-in-loop comments — safe, scales operator 10×
- 1c. Pure organic — slowest, lowest risk
- Decision still pending

---

## Part 3: Work Classification (4 Tiers)

### Tier 1 — OPERATOR DECISIONS (no engineering needed, only your call)

| # | Item | Cost | Time to decide |
|---|---|---|---|
| **D1** | Channel growth strategy | $0-500 | 5 min |
| **D2** | F1 VPS upgrade 4GB → 8GB | $10/mo | 5 min |
| **D3** | Gaming gate: enable `GENLAB_LLM_JUDGE_ENABLED=1` | ~$0.10/day | 1 min |
| **D4** | Activate AUTO #2 auto-ramp timer | $0 | 1 min |
| **D5** | Replace placeholder affiliate URLs with real PartnerStack codes | $0 | 30 min |
| **D6** | Sync runtime `affiliate_catalog.yaml` from `.example.yaml` | $0 | 2 min |
| **D7** | Activate 4 dormant intelligence engines (sequenced) | $0 | per week |
| **D8** | Sponsored content strategy (after channels grow) | $0 | defer |

### Tier 2 — QUICK ENGINEERING (1-day or less PRs, ready to ship)

| # | Item | Effort | Impact |
|---|---|---|---|
| **E1** | Wire `record_anthropic_usage()` to 5 untracked LLM call sites | 2h | Cost dashboard becomes accurate |
| **E2** | Pass `arm_id` to writer prompt as constraint | 4-6h | Hook matches arm intent |
| **E3** | Implement engagement_window 7d recompute | 1d | Captures late-tail viral content |
| **E4** | Per-platform hashtag pools + CTA library | 6h | Platform-tailored engagement |
| **E5** | LinUCB platform feature dimension (13→14) | 1-2d | Bandit learns platform asymmetry in features (not just arms) |
| **E6** | Verify YT channel RSS reachability + add PixelDojo AI | 30min | Complete the AI-creator channel add from earlier today |
| **E7** | Reward weight redistribution logging + alert | 2h | Detect API-tier gaps |
| **E8** | Visual asset backup to S3 Glacier | 2-3h | DR coverage of MP4s |

### Tier 3 — MULTI-DAY PROJECTS (substantial work, plan before shipping)

| # | Item | Effort | Impact |
|---|---|---|---|
| **P1** | Per-platform decision-time logic in push_to_backlog | 2-3d | Bandit picks per-platform-best arms at creation time |
| **P2** | Whisper captions text_optimizer regression fix | unknown | Re-enables word-by-word audio sync (showcase content gain) |
| **P3** | A/B testing framework (multi-armed for hooks) | 1-2d | Real hook quality measurement |
| **P4** | Mobile-friendly dashboard | 1-2d | Operator can review on phone |
| **P5** | Content recycling system (90d+ high-engagement republish) | 1d | Free content from proven winners |
| **P6** | TikTok integration completion | 2-3d | 5th major platform |
| **P7** | Sponsorship readiness scoring + dashboard | 1-2d | Phase 2 SaaS prep |
| **P8** | Hook critic feedback loop (regenerate with reasons) | 1-2d | Hook quality improves automatically |

### Tier 4 — TIME-PASSIVE (just wait for data)

| # | Item | Timeline | Trigger |
|---|---|---|---|
| **T1** | Bandit posteriors accumulate variance | 4-8 weeks | Need engagement variance from larger audience |
| **T2** | Validation harness shows useful Spearman | 4-8 weeks | Builds on T1 |
| **T3** | AUTO #2 calibration data hits threshold per niche | 1-4 weeks per niche | Operator reviews accumulate |
| **T4** | Source performance dashboard data populates | 1-2 weeks | Each published post adds 1-5 platform-specific reward rows |
| **T5** | Content_type LinUCB feature learns variance | 2-4 weeks | Needs 50+ obs with new 13D vector |

---

## Part 4: Sequencing + Dependencies

### Week 1 (this coming week)

**Operator does (90 min total)**:
- D1: Pick channel growth strategy
- D2: Upgrade VPS to 8GB (~10 min)
- D3: Enable gaming LLM-as-judge env flag
- D4: Enable AUTO #2 auto-ramp timer
- D5: Replace 3 affiliate placeholder URLs

**Passive accumulation**:
- T4 starts (each new published post adds platform-specific reward rows)
- T3 starts to accumulate per niche

**Ship engineering** (single small PR):
- E6: Verify YT channels + add PixelDojo AI

### Week 2

**Operator does**:
- Review bandit_validation interpretation column daily — does it move from "low signal" to "developing"?
- Review gaming gate agreement post-LLM-as-judge — has it jumped from 22.4% to 80%+?

**Engineering** (1 small PR per day):
- E1: Cost tracking wires (Mon)
- E2: arm_id in writer prompt (Wed)
- E7: Reward weight logging (Fri)

### Week 3-4

**Operator does** (if validation looks good):
- Activate first dormant engine: hook classifier training wire
- After 1 week stability: conformal router for ai_creators

**Engineering**:
- E3: 7-day reward recompute
- E5: LinUCB platform feature (substantial, single focused day)
- E4: Per-platform content templates

### Month 2

**Engineering**:
- P1: Per-platform decision-time logic
- P2: Whisper captions deep fix (depends on text_optimizer investigation)
- E8: Visual asset backup

**Operator**:
- Activate Bayesian gate for ai_creators (after conformal stable)
- Activate hook diversity for anime
- Consider AUTO #2 ramp beyond 10% if data justifies

### Month 3+

**Engineering** (depending on growth):
- P3: A/B testing framework
- P5: Content recycling
- P4: Mobile dashboard
- P7: Sponsorship readiness (when channels approach monetization thresholds)
- P6: TikTok completion

---

## Part 5: Decision Matrix Per Tier

### How to triage new requests / ideas

```
Is the bottleneck audience size?
├── YES → Channel growth tactics (Tier 1, D1)
└── NO → Is the bottleneck operator time?
        ├── YES → Activate dormant engines (Tier 1, D7) or batch-approve (already shipped)
        └── NO → Is the bottleneck data correctness?
                ├── YES → Engineering Tier 2 (cost tracking, reward signal quality)
                └── NO → Is the bottleneck capability?
                        ├── YES → Engineering Tier 3 (multi-day projects)
                        └── NO → Wait for Tier 4 (passive accumulation)
```

### Single most important question to ask before ANY new work

**"Does this change require engagement variance to demonstrate value?"**

- YES → defer until channels grow OR ship and wait 4-8 weeks
- NO → ship now if it's pure operator-visible improvement (dashboard, ops, backup)

---

## Part 6: Success Metrics (How We Know It's Working)

### 30-day targets (post channel growth strategy activation)

| Metric | Today | 30 days |
|---|---|---|
| Max likes per post | 5-9 | 25-50 |
| Max reach per post | 295-849 | 1500-3000 |
| Avg engagement score | 1.25-2.63 | 3-5 |
| bandit_validation Spearman (ai_creators) | 0.000 | 0.20+ |
| bandit_validation interpretation | "low signal" | "developing" or "useful" |
| Auto-approval gate agreement (gaming) | 22.4% | 80%+ (with LLM-as-judge) |
| Operator clicks per day on review | ~5-10 | ~3-5 (batch-approve adoption) |
| Affiliate clicks per week | 0 | 5-20 (Runway/Sora/Midjourney) |

### 90-day targets

| Metric | 90 days |
|---|---|
| Channels at 1K+ followers | 2 of 5 |
| Niches with AUTO #2 at 50%+ | 2 of 5 |
| Niches with validation = "useful" | 3 of 5 |
| Bandit posteriors meaningfully different per platform | YES |
| Cost per blueprint accurate to ±10% | YES |
| Dormant engines activated | 3 of 4 |
| First sponsored content brand outreach | YES |

### 6-month targets

| Metric | 6 months |
|---|---|
| Channels monetized (YT 1K/4K) | 1+ |
| AUTO #2 at 80%+ on top niche | YES |
| Validation harness saying "useful" | 4-5 niches |
| First brand deal | $500-2000 MRR |
| Self-sustaining content + engagement loop | YES |

---

## Part 7: Open Questions for Operator (need answers before next sprint)

1. **Channel growth strategy** (D1): A (paid), 1a (human-in-loop), or organic-only? **The most important single decision.**

2. **Revenue target for 90 days**: $0 (just learning), $100-500 (proof of concept), or $1000+ (real income)?

3. **Time per day you can spend on Mission Control**: 5 min / 15 min / 30 min / variable?

4. **Comfort with engine activation**: Conservative (1 niche at a time, 1 week wait) or aggressive (all niches simultaneously)?

5. **Sponsored content readiness**: Want to start outreach now (even pre-1K followers) or wait for monetization thresholds?

6. **VPS budget**: $10/mo OK (CX21)? $4/mo for separate CI (CX11)? Cap somewhere?

7. **AI disclosure standard**: Soft "Made with X" (current default) or stricter "AI-generated content notice"?

---

## Part 8: What Success Looks Like at 6 Months

### The system operating "intelligently"
- ai_creators: 5K-10K followers per platform, AUTO #2 at 80%, validation says "useful", bandit picks demonstrably outperform random
- anime + movies: 2-5K followers, AUTO #2 at 50%, validation "developing"
- gaming + sports: 1-2K followers, AUTO #2 at 20%, validation "developing"

### Operator daily ritual
- 3-5 min on Mission Control: review batch-approve queue, glance at source-performance card, dismiss any critical alerts
- Weekly: review validation harness trends, decide on dormant engine activations
- Monthly: review sponsorship readiness, decide on outreach

### Revenue trajectory (optimistic)
- Month 1-2: $0 (channel growth phase)
- Month 3-4: $50-200 (affiliate clicks from showcase content)
- Month 5-6: $500-2000 (first sponsored content + meaningful affiliate revenue)

### Architecture state
- All 4 dormant engines activated + measurably contributing
- Per-platform bandit posteriors meaningfully different
- Cost tracking accurate
- Backups including visual assets
- Validation harness showing real Spearman correlation per niche

---

## Part 9: What I Would Personally Bet On

If I had to pick **3 things** the operator should DO this week that have highest expected value:

1. **D1 (channel growth strategy)** — pick A (paid seeding $250-500). Highest ROI single action this week. Without audience, nothing else matters.

2. **D2 (VPS upgrade)** — $10/mo solves chronic operational issues forever. CodeQL back on, CI doesn't compete with prod, swap pressure gone.

3. **D3 (gaming LLM-as-judge)** — $0.10/day cost, gaming gate jumps from 22.4% to 80%+ agreement within a week, unblocks AUTO #2 ramp possibility.

Everything else is downstream of these three.

---

## Part 10: Anti-patterns to Avoid

Things this audit cycle revealed to NOT do:

1. **Don't ship more code without measuring tonight's deploys first** — 33 commits is enough; let data accumulate
2. **Don't activate dormant engines all at once** — sequence them, 1 niche at a time, 1 week apart
3. **Don't blindly trust "PR Z claims to have shipped this"** — verify with data queries (today's per-platform finding proved arms never populated)
4. **Don't add cleanup automation that violates documented safety contracts** — yesterday's disk_cleanup_v1 deleted scheduled posts' media; always check existing protection
5. **Don't ship engineering work hoping it solves audience-growth problems** — those are strategy/marketing decisions, not engineering
6. **Don't auto-merge to main without CI when CI is available** — today's admin-push pattern was emergency mode; revert to PR + CI workflow once self-hosted runner stabilizes

---

## Part 11: Roadmap Maintenance

This document should be:
- **Reviewed weekly** during the active growth/activation phase
- **Updated at each completed Tier 2 / Tier 3 item** (mark done, move to "completed work")
- **Re-audited monthly** for new blind spots as the system evolves
- **Versioned per quarter** (this is ROADMAP-2026-07; ROADMAP-2026-10 will be next quarter's)

---

## Reference index — Memory entries

Key memory entries written 2026-06-29 → 2026-06-30 that this roadmap synthesizes:

1. `backup-recovery-procedure-2026-06-29.md`
2. `disk-cleanup-cron-mistake-2026-06-29.md`
3. `self-hosted-ci-runner-architecture-2026-06-29.md`
4. `sources-config-bug-2026-06-30.md`
5. `ai-creators-content-funnel-2026-06-30.md`
6. `declared-but-unpopulated-anti-pattern.md`
7. `agent-learning-state-2026-06-30.md`
8. `dormant-intelligence-engines-2026-06-30.md`
9. `gaming-auto-approval-gate-broken.md`
10. `bandit-decision-architecture-2026-06-30.md`
11. `system-blind-spots-2026-06-30.md`

Future sessions: read MEMORY.md top section first, then this roadmap, then dive into specific memory entries as needed.
