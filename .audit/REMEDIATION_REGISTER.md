# GenLab Remediation Register — from State of the System 2026-08-19

**Gate: nothing in this register executes before NARR-09's evidence session
closes #218.** Two preconditions, in order: (1) operator listen verdict on
`PREVERIFY2_narrated.mp4` (#30), (2) NARR-09 A.1 → Phase C complete.

Sequence slots: **S1** OPS-01 · **S2** ALERT-01 · **S3** CONTENT-01 ·
**S4** 09-01 strategic review · **S5** LEARN-01 + #222 · **HYG** hygiene batch
(rides S1) · **OP** operator-only.

| # | Issue | Slot |
|---|---|---|
| 1 | Journal retention ~9.7h vs 30d; disabled rule #26; hid 3 failed units | S1 |
| 2 | 3 silent failed units + orphan transient | S1 |
| 3 | post-deploy-verify weekly, not deploy-triggered, and failing | S1 |
| 4 | Auto-approve thresholds diverge 3 ways; movies live at 0.99 | S1 |
| 5 | 6 prod-only systemd units incl. attribution-health-monitor | S1 |
| 6 | Alert panel insert-only; no resolve/ack/dedup | S2 |
| 7 | CUSUM miscalibration (122σ artifact; UP-shift as CRITICAL) | S2 |
| 8 | Reward signal wrong — **see correction below** | S3 |
| 9 | Branded cold open: Tier-1 HIGH, zero adoption | S3 |
| 10 | FIRST_FRAME_VALIDATOR built, off | S3 |
| 11 | HOOK_NEAR_DUPE_RETRY built, off | S3 |
| 12 | 255 transform arms unlearnable; freeze at benchmark defaults | S5 (table drafted S3) |
| 13 | Harness Phases 1–2 (scorecard + reference corpus percentiles) | S3 (parallel, read-only) |
| 14 | CTA engine optimizes follows, not sends | S3 |
| 15 | Publish window pinned 06:00 UTC → multi_publish bootstrap deadlock | S4 (execute) |
| 16 | Consolidation decision (anime deficit, SR/FD risk, gaming's 4 marks) | S4 |
| 17 | Gaming `blueprint_context` omits all 4 NARR keys | S4→S5 |
| 18 | whisper_sync canary dependency | S1 (diagnostic) |
| 19 | Propagator class + positional stage order + no final-asset gate | S5 (#222) |
| 20 | Writer observability gap (#223) | S5 |
| 21 | tests/deploy xdist shared-state flake | HYG |
| 22 | Hygiene batch (orphan, stale error file, legacy backup dir, 4 dead modules, OpenSandbox/, 769 branches, stale rule #27 docs) | HYG |
| 23 | Two Postgres instances + two backup dirs | HYG |
| 24 | Ideation pool: 36–54/niche pending, consumed=0 | S4 |
| 25 | Dead-source pruning; BB off-niche channels | S3 |
| 26 | 11 MCP connectors unauthenticated; Pinecone keyless | OP |
| 27 | OpenAI top-up + billing alerts both consoles | OP |
| 28 | BB Facebook 10,032 followers — provenance + monetization eligibility | OP (before 09-01) |
| 29 | Stale shells in UI panel | OP |
| 30 | **The listen verdict — gates everything** | OP (now) |

---

## Correction carried into #8

The register's fix shape — "re-plumb RewardShaper to dense signals" — does not
match what was measured on 2026-08-19. **The weights already specify the dense
signals.** What is missing is the data.

Across 90 posts per platform over 21 days, these appear in **zero** rows:

`completion_rate` · `vtr` · `avg_view_duration` · `avg_watch_time` ·
`minutes_viewed` · `dm_send_rate` · `follower_gained` · `subscriber_gained` ·
`skip_rate` · `discovery_share` · `reply_chain_rate`

Weight allocated to never-collected metrics:

| platform | absent weight | what survives |
|---|---|---|
| facebook | **0.60** | reach; shares nonzero 2/90 |
| instagram | **0.60** | saves 65/90, plays 65/90, shares 5/90 |
| youtube | **0.50** | plays 60/89; likes 2/89 |
| threads | 0.30 | `plays` alone — reach/shares/reposts all 0 across 88 |

`RewardShaper`'s redistribution then silently reallocates the missing weight
onto the survivors, converting a retention-weighted reward into a view-proxy
reward. That is the mechanism behind mean 0.074 / 44% zeros.

Sharpest instance: **`dm_send_rate` is weighted 0.25 on Instagram — joint
highest — and has never been collected once.**

**S3 #8 is therefore a fetcher/instrumentation build, not a re-weighting.**
Targets: IG Insights reels metrics, FB `minutes_viewed`/`completion_rate`,
YouTube Analytics `averageViewDuration` + `subscribersGained`. The weights are
already correct and begin working the moment data arrives.

Related suspicion for S3: redistribution is documented to WARN above 15%
dropped weight. It is running at 50–60% and no such warning was found — either
it never fires or the 9.7h journal window hid it. Same silent-degrade class as
the narration bug.

## Data already on hand for OPS-01

* **#4 movies-at-0.99**: movies holds 15 scheduled ahead through **2026-09-03**
  and produces ~3.3 blueprints/day against 1/day burn — consistent with
  backlog drain rather than rare high-confidence passes. Step 2.3 should
  confirm and compute exhaustion from that surplus, not from zero.
* **#16 anime**: covered until **2026-08-22**, zero unscheduled spare,
  producing 0.43/day against 1/day burn (−0.57/day). Dark from ~08-23 as the
  register anticipates.
* **#1 journal**: 42 MB of a 2 GB cap, 12 GB free, hourly vacuum freed 0 B,
  **one boot retained across 5 weeks uptime**, oldest entry 13:04 today. The
  aspirehub-volume hypothesis is untested — Step 1.2 is the right test.
* **#23**: `:5432` = docker `genlab-postgres` (GenLab's, 66 MB, per
  `DATABASE_URL`); `:5433` = host PostgreSQL 18. Bare `psql` hits 5433.
