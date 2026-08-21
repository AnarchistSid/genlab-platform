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

---

## Monetization additions (2026-08-20)

### Shipped today

| change | scope | tracked? |
|---|---|---|
| `affiliate_enabled: false → true` × 4 niches | gaming, sports, anime, ai_creators | **NO — prod-only, gitignored catalog** |
| `cta_injection_enabled: false → true` × 5 niches (`70af24f5`) | all | yes |
| `cta_injection_enabled → false` for ai_creators (`b5b47c5f`) | BB only, dated defer | yes |
| `evergreen_default` moved Claude Pro → Ring Light (ai_creators) | ai_creators | **NO — prod-only, gitignored catalog** |

The headline finding: `cta_injection_enabled` was disabled 2026-08-06
(`4dd2ebb6`) explicitly "pending disclosure-position rebuild". That rebuild
shipped 2026-08-12 (`e790334c`). **The flag was never flipped back**, and no
commit since ever set it true. 540 blueprints carried affiliate links
historically; the last was movies on 2026-08-05, the day before the disable.
Zero since, across every niche.

### #M1 — Selection-loss diagnostic (findings only)

Linked blueprints publish at roughly **¼** the rate of unlinked ones: movies
generated 16 linked blueprints in 30 days and published **2**, where a
link-blind selector would have published ~7–8. Find where the
selector/gatekeeper penalizes link-bearing inventory — auto-approval
confidence, priority score, schedule ordering, or the affiliate daily cap
interacting with slot assignment. **Findings only, no fix.** This multiplier
matters more than any further enablement: four more niches now feed a funnel
that loses ~75% of what enters it.

### #M2 — Cuelinks / EarnKaro SaaS inventory for ai_creators

Every AI-software product in the ai_creators catalog is unusable:

```
Claude Pro Subscription  enabled=True   networks=['direct']  0% + probes healthy=False
ChatGPT Plus             enabled=False  networks=NONE
Midjourney Subscription  enabled=False  networks=NONE
```

So AI-tool stories can never keyword-match a monetizable product and always
fall through to the evergreen — currently a ring light. OpenAI, Anthropic and
Midjourney have no consumer affiliate programmes, so no URL can fix these;
inventing one would be worse than the gap. `CUELINKS_EMAIL`,
`CUELINKS_PUBLISHER_ID`, `EARNKARO_EMAIL` are already in `.env`. Source real
SaaS offers through those networks so the highest-CPM niche sells software
rather than lighting.

### #M3 — Catalog versioning (executes in OPS-01)

`affiliate_catalog.yaml` and `affiliate_seasonal.yaml` are **gitignored**
(`.gitignore:141`). They hold the kill switches, 82 product URLs, commission
rates and the evergreen defaults, and exist on one box with no history — which
is why `git log -S"affiliate_enabled: false"` returns nothing and there is no
record of who disabled four niches or when. The affiliate tags are already env
vars, so nothing sensitive lives in the file. Split product data + kill
switches into git; keep secrets in `.env`.

**Named revert-on-rebuild risks until #M3 lands:**
1. `affiliate_enabled: true` × 4 niches
2. `evergreen_default` Ring Light promotion (ai_creators)

Both silently revert if the VPS is rebuilt from the repo. Backups:
`affiliate_catalog.yaml.bak.20260820T081823Z` (pre-enable) and
`.bak.20260820T094834Z` (pre-evergreen-move).

## AFF-01 — affiliate from slot machine to system (authorized 2026-08-20)

Four phases, sequentially gated. The gating is the point: each phase's
authorization is conditional on the *previous phase producing evidence*, not on
the previous phase merely completing.

| Phase | What | Gate to enter |
|---|---|---|
| **1 — Inventory + health** | Read-only. Catalogue what exists, probe every link, geo-check, map niche→product fit. Findings only, no writes. | **Authorized. Runs Friday 2026-08-21 after the four non-BB niches fire (03:30–06:00 UTC).** Read-only, so it runs parallel to the pipeline. |
| **2 — Attribution** | Prove a click can be joined back to the blueprint that produced it. | Phase 1 findings reviewed + #218 closed. |
| **3 — Selection** | Arm the product bandit on real signal. | **Explicitly gated on Phase 2's click-join proof.** |
| **4 — Scale** | Inventory expansion into what Phase 3 shows converts. | Phase 3 showing measured lift. |

### Attribution-before-arming — adopted 2026-08-20

Phase 3 does not begin until Phase 2 demonstrates a click joining back to its
originating blueprint. This is the same shape as the transform-arms lesson: 255
`transform__*` arms were created and updated for months against a reward signal
too sparse to distinguish them from noise, making them arithmetically
unlearnable (~18 years to converge at current volume). An arm that cannot
receive attributable reward is not a learning system — it is a random number
generator with a persistence layer.

The product bandit has 108 arms. If clicks cannot be joined to blueprints, those
108 arms are in exactly the position the 255 transform arms were in, and
arming them would manufacture the same false impression of a learning loop.
Hence: prove the join first, arm second.

### Standing constraints on all four phases

* **Never fabricate affiliate URLs or enroll in programmes.** Inventory
  additions use only networks with live credentials
  (`AMAZON_IN_AFFILIATE_TAG`, `AMAZON_US_AFFILIATE_TAG` confirmed set), and
  every added link is health-probed before enable.
* **Compliance is frozen as shipped.** `#ad` head-position enforcement
  (first 100 chars, `e790334c`) is untouched by any phase.
* ai_creators stays `cta_injection_enabled: false` until on/after
  **2026-08-29** — unchanged, since the fire never moved.

## #226 — BB highlight window raise 16s → ~28s (post-evidence content change)

**Filed 2026-08-21. Gated: does NOT ship before Saturday's evidence run.**

BB's `highlight_moment.window_seconds: 16` is the shortest reel we produce and
sits at the floor of the 15–60s platform range. It is also the constraint that
made narration marginal:

```
16.0s clip − 2.0s tail = 14.0s fit budget @141wpm → ~33 words
```

Thirty-three words is one sentence. The voice-over has no room to say anything
a viewer would notice, and any projection error at all pushes it over. Raising
the window to ~28s gives ~61 words — a natural two-sentence VO with headroom
above the mix-time guard's 0.5s tolerance.

**Why it is filed rather than shipped:** it changes reel duration, which is a
published-artifact surface, and the pre-evidence freeze covers render, caption
and metadata alike. Changing the clip length in the same window as the
narration evidence run would confound #219's retention read — a duration change
and an audio change landing together are not separable in the retention curve.

**Sequence:** Saturday's evidence run at the current 16s → operator listen
verdict → #218 closes → #219 baseline starts → *then* #226, as a deliberate
single-variable content change with its own retention read.

**Annotation for #219:** the baseline is measured at `window_seconds: 16`. When
#226 lands, the #219 series breaks and a new baseline segment starts. Any
retention comparison spanning the change is invalid.
