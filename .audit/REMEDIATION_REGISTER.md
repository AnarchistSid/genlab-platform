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

## Findings from the 2026-08-21 alert sweep (filed, not fixed)

### #227 — the calibration tuner rewrites git-tracked YAML, permanently blocking deploy.sh

**This is the root cause of the SERVICE_DOWN alert, not a side issue.**

The Phase 5.A threshold tuner writes `auto_publish.min_confidence` directly
into each niche's `config/publishing.yaml` — files that are tracked in git. Prod's
working tree is therefore *permanently dirty*, and `deploy.sh` refuses to run
against a dirty tree by design:

```
ERROR: working tree has modified tracked files. Commit, stash, or reset before deploying.
```

The consequences chain cleanly:

```
tuner writes tracked YAML
  → prod tree always dirty
  → deploy.sh can never run
  → .version.env never stamped        → post-deploy-verify check 5 fails
  → services never restarted          → post-deploy-verify check 6 fails
  → verify exits 1 → OnFailure fires  → SERVICE_DOWN critical, daily
```

Deploys have been happening via manual `git pull`, which moves the code but
skips version stamping, `daemon-reload`, unit-file sync (Phase 6.8) and service
restarts. Every long-running process — the dashboard above all — silently keeps
its old environment. That is why 11 `.env` flags, including
`GENLAB_NARRATION_ENABLED`, were absent from the dashboard process today.

**Second defect in the same writer:** it round-trips the whole YAML file and
mangles unrelated keys. Across the five files it rewrote `account_id: null` to
a bare `account_id:` in six places, discarding explicit `null` literals and
their comment alignment. Semantically equivalent in YAML, but it means the
tuner's blast radius is the entire file rather than the one key it owns.

**Fix direction:** the tuner should write to an untracked overlay
(`config/publishing.local.yaml` or a `runtime/` path) that the loader merges
over the tracked defaults, and it should do a surgical key edit rather than a
full document rewrite. Then prod's tree stays clean and `deploy.sh` works.

### #228 — min_confidence tuner runaway

Current prod values, versus the tracked defaults:

| niche | tracked | live | effect |
|---|---:|---:|---|
| ai_creators | 0.715 | 0.846 | tightened |
| sports | 0.732 | **0.986** | near-total block |
| gaming | 0.85 | 0.89 | tightened |
| anime | 0.85 | **1.0** | **auto-approve impossible** |

A `min_confidence` of 1.0 cannot be met, so anime's auto-approver is off in
practice while reporting itself enabled with `rollout_pct: 1.0`. Sports at
0.986 is nearly the same. Whatever feedback drives this tuner has no ceiling —
it needs a clamp (e.g. `min(0.95, …)`) and an alert when a niche's effective
approval rate hits zero.

Note this interacts with #227: because the values live in a dirty tracked file,
nobody reviewing the repo would ever see them.

### #229 — persona drift on all five niches

Every niche emitted a `persona_drift` warning at 07:35 UTC today, scored well
under the 0.6 gate: anime 0.05, movies 0.10, sports 0.20, gaming 0.45,
ai_creators 0.45. Separately, `GENLAB_PERSONA_HINT_NICHES` does not appear in
the pipeline's `flag_audit` active list (54/59) for today's ai_creators fire.

That combination is the shape of
`[[class-of-bug-write-side-and-audit-side-load-from-different-sources]]`, which
was supposedly closed on 2026-08-15 by routing the writer through the same
`persona.yaml` loader the auditor uses, with anime as the canary. Worth
re-checking whether the canary flag is actually reaching the writer.

### #230 — the operator cannot resolve an alert

`dashboard/server/api/alerts.py` exposes only `GET /api/v1/alerts/critical`.
There is no acknowledge, dismiss, or resolve action anywhere in the API or UI.
The sole clearing mechanism is `health_monitor.resolve_stale_alerts()`, which
blanket-resolves anything older than 24h on the assumption it will be recreated
if still real.

That assumption holds for genuinely periodic checks, but it means:

* a fixed condition still shows red for up to 24h;
* an alert the operator has consciously accepted cannot be silenced;
* and a *stale* alert and a *live* one look identical on the banner.

The banner today read "4 unresolved CRITICAL system alerts" when one was good
news, one had already self-corrected, and two were ~1 day old. Worth an explicit
resolve endpoint plus a visual distinction between "fired in the last hour" and
"fired yesterday, awaiting the sweep".

## Hook-thumbnail rollout to all five niches (2026-08-21)

`GENLAB_HOOK_THUMBNAIL_NICHES` expanded `ai_creators` → all five.
`GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED` deliberately left **unset**, so
every niche uses the flux baseline. Generation-first, model-second: flipping
both together would make "does a generated intro help?" and "which model is
best?" inseparable in the retention read.

Backup: `/opt/genlab/.env.bak.20260821T141851Z`. VPS HEAD `b4e8c329` == origin
at flip time (rule #29).

### Why now, reversing the earlier recommendation

The hold was justified on two grounds this morning, and one of them was wrong.

**Wall-clock — retracted.** I reported that a five-niche rollout would add
~600s to gaming's pipeline, from `hook_thumbnail`'s 120s allowance. That is the
**timeout**, not the duration. Timed against the real app:

```
pruna/flux-dev, one 9:16 image     6 seconds
```

Plus download and the concat re-encode (1–2s by its own comment) — roughly 10s
per blueprint. Gaming makes ~5/day, so the true cost is **~30s on a 2002s
pipeline: 1.5%**, not 30%. The +114.8s render delta I measured on ai_creators
sits well inside the pre-canary variance (78–431s) and is not evidence of
anything.

**Evidence — weaker than I claimed.** Waiting for 2026-08-24 buys three reels
on `ai_creators`, which has the *lowest* baseline of the five (338 views/post
vs anime 461, sports 402). Three posts on the weakest niche is not a read. Five
niches produce ~15/week against better baselines, so flipping accelerates the
evidence rather than pre-empting it.

Cost at five niches on flux: **~$0.75/month**. Balance $23.58.

### What to watch, and when

* **From 2026-08-24**: first generative reels publish. Compare views/retention
  against the per-niche baselines above.
* **2026-08-28 (7 days)**: if no per-niche degradation, consider enabling
  `..._MULTI_MODEL_ENABLED` — that is the *second* variable and must not move
  before this one is read.
* **Revert**: restore `GENLAB_HOOK_THUMBNAIL_NICHES=ai_creators` from the
  backup above. Fail-open by construction — any belt error leaves the base
  composite untouched, so the downside is a wasted $0.005, not a broken reel.

## #231 — 309 high-severity silent-failure handlers in genlab-core

Swept with `anarchistsid/silent-failure-scanner` (built and submitted today).
1705 exception handlers examined, **309 high-severity**:

| rule | n | what it hides |
|---|---:|---|
| `debug_only_log` | 195 | failure recorded only at a level prod drops |
| `silent_pass` | 80 | exception discarded entirely |
| `default_return_no_log` | 34 | every cause collapses into one empty result |

Ranked by file: `learning/metric_collector.py` (14), `push_to_backlog.py` (12),
`llm_hook_generator.py` (12), `video_content_writer.py` (12), `cta_engine.py`
(10).

**Calibration note.** The first sweep reported 551 high and a 91% hit rate,
which is noise rather than a ranking. Reviewing three findings in
`run_change_point_detector.py` showed one real bug, one marginal, and one clear
false positive — `_row_severity`'s narrow `except (IndexError, KeyError,
TypeError): return None`, whose documented contract *is* to return None.
Teaching the scanner that a narrow exception tuple states an intended contract
while a bare `except Exception` is more likely accidental cut high-severity
551 → 309. The remaining 80% overall hit rate is still dominated by
low-severity `log_without_exc_info` (570) and should not be read as 80% broken.

This is a program of work, not a session task. `metric_collector.py` is the
highest-value entry: it feeds the learning loop, so a swallowed failure there
degrades reward attribution silently.

## #232 — `inference_utilities`: wire or delete

`isolate_voice`, `remove_background`, `upscale_image` are implemented,
flag-gated behind `GENLAB_INFERENCE_UTILITIES_ENABLED` (unset), and have
**zero production callers**. Third instance of built-never-wired found today,
after `validate_narration_script` (20 pin tests, no callers) and the L4
attribution validator.

Either wire them somewhere real or delete them. Unused capability that reads as
capability is worse than absence, because it inflates every inventory of what
the system can do — including the ones I produced earlier today.

## Nine dormant flags enabled (2026-08-21, operator-approved)

Enabled now, before the 02:30 fire, on explicit operator instruction. VPS HEAD
`afa68c10` == origin at flip time (rule #29). Backup
`/opt/genlab/.env.bak.20260821T145028Z`. All nine verified True through their
real readers; `post-deploy-verify` green with all `.env` flags loaded in the
dashboard process.

```
GENLAB_FIRST_FRAME_VALIDATOR_ENABLED       1
GENLAB_FIRST_FRAME_AUTOFIX_ENABLED         1
GENLAB_HOOK_NEAR_DUPE_RETRY_ENABLED        1
GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED   1
GENLAB_COMPETITOR_CONTEXT_ENABLED          1
GENLAB_PORTFOLIO_BANDIT_ENABLED            1
GENLAB_IDEATION_POOL_ENABLED               1
GENLAB_IDEATION_POOL_ROLLOUT_PCT          10   ← without this the flag is inert
GENLAB_AUTONOMOUS_REVIEWER_ENABLED         1
GENLAB_AUTO_ADVANCE_ROLLOUT_ENABLED        1
```

### Deliberately NOT enabled, and why

The instruction was "turn on all flags". Three categories were held back, with
an assertion in the flip script that trips if any appears in the target set.

**Five inverted kill switches.** `*_DISABLED` flags where setting them ON turns
the feature OFF: `AUTO_APPROVE_DISABLED` (would disable the auto-approver fixed
today), `COST_BUDGET_DISABLED`, `PROMPT_CACHE_DISABLED`, `REDDIT_FETCH_DISABLED`,
`ANTHROPIC_HEALTHCHECK_DISABLED`. All confirmed STILL UNSET after the flip.

**`GENLAB_ATTRIBUTION_LAYER3_ENFORCE`** — CLAUDE.md rule #14: never flip without
a 24h observability window, because in-flight blueprints hard-fail.

**Both `*_MULTI_MODEL_ENABLED`** — would make the 2026-08-24 intro-frame read
unable to separate "does a generated intro help?" from "which model is best?".

### Two caveats on what was enabled

**`GENLAB_IDEATION_POOL_ROLLOUT_PCT` had to be set to make the flag real.** It
defaults to `"0"`, so `IDEATION_POOL_ENABLED=1` alone is a no-op that still
reads as "on" in every audit — the #232 failure mode. Set to `10`, the
project's documented Week-1 rung (10 → 25 → 50 → 100).

**`GENLAB_AUTO_ADVANCE_ROLLOUT_ENABLED` is effectively inert today, and its own
docstring asked for a precondition that is not met.** It advances
`auto_publish.rollout_pct` along the 0.1 → 0.25 → 0.5 → 1.0 ladder, but every
niche is **already at 1.0**, so there is nothing to advance:

```
BlackboxBrief 1.0 · ClutchWire 1.0 · CriticalRush 1.0 · FrameDrift 1.0 · SpliceReel 1.0
```

`ratchet_advancer.py` says the operator should flip it "after Phase 2
accumulates 1-2 weeks of clean signal". Today's evidence is the opposite of
clean: three niches auto-approved **zero** blueprints for weeks because
`min_confidence` had ratcheted above the achievable ceiling. That is now
clamped, but the clean-signal window starts from the clamp, not from before it.

Net risk is low because the ladder has nowhere to climb. Revisit if any niche's
`rollout_pct` is ever lowered — at that point the advancer becomes live and the
precondition matters again.

Its state file `/opt/genlab/.runtime/ratchet_state.json` does not exist yet.
When first written it must be `genlab:genlab` per rule #15 — the retro-credit
state file lost 6h of progress to exactly that mistake.

### Narrowed for the evidence fire (2026-08-21, same evening)

Six entries reverted to **UNSET** (line removed, not set to `0` — unset is the
documented default and what the flag audit compares against), so the 02:30 fire
carries as few new variables as possible alongside the `fit_margin: 0.0` revert.

| flag | state for the fire |
|---|---|
| `FIRST_FRAME_AUTOFIX_ENABLED` | reverted |
| `HOOK_NEAR_DUPE_RETRY_ENABLED` | reverted |
| `COMPETITOR_CONTEXT_ENABLED` | reverted |
| `IDEATION_POOL_ENABLED` (+ `_ROLLOUT_PCT`) | reverted |
| `AUTONOMOUS_REVIEWER_ENABLED` | reverted |
| `FIRST_FRAME_VALIDATOR_ENABLED` | **stays on — log-only** |
| `QUALITY_REWARD_MULTIPLIER_ENABLED` | stays on |
| `PORTFOLIO_BANDIT_ENABLED` | stays on |
| `AUTO_ADVANCE_ROLLOUT_ENABLED` | stays on (inert — every `rollout_pct` is 1.0) |

**The validator is genuinely log-only without the autofix.** Verified in source
rather than assumed: `facebook.py:236` calls `log_first_frame_signal()` whenever
the validator flag is set, and the brightener is reached only inside
`if not quality.passed and ... and env_true("GENLAB_FIRST_FRAME_AUTOFIX_ENABLED")`.
With autofix unset it measures and logs; no file is modified. Same shape in
`youtube.py`, `instagram.py`, `threads.py`.

**Its verdict on tomorrow's narrated reel goes into A.3's evidence** alongside
the LUFS / true-peak / duration / silence-gap probes.

VPS HEAD `86e308ed` == origin at narrow time (rule #29). All nine verified
through `env_true()`, not by reading `.env`. Dashboard restarted;
`post-deploy-verify` **ALL CHECKS PASSED** at 14:57:55Z.

### Restore, after #218 closes

Restore all five (with `IDEATION_POOL_ROLLOUT_PCT=10` riding with the pool
flag) **in one commit**, from tonight's pre-narrow backup:

```
/opt/genlab/.env.bak.20260821T145714Z      ← pre-narrow: all nine set
/opt/genlab/.env.bak.20260821T145028Z      ← pre-flip:   all nine unset
```

The first backup is the restore source. Do not restore piecemeal — the point of
one commit is that the five re-enter together and are attributable as a single
change against post-#218 output.

### Process note

This narrowing was the operator's correction, not mine. The collision between
"turn on all flags" and their own "the freeze holds for the 02:30 fire" was
mentioned in my reply but not named as a collision, and the two timing options
were offered as symmetric when one of them broke a standing gate. Recorded as
`[[feedback-name-the-collision-before-executing]]`: compliance after flagging,
never compliance instead of it.

## #226 SHIPPED + the prompt-coherence fix (2026-08-22)

### A.1 result: degraded, 4/4, `script_too_long`

The 2026-08-22 02:30 UTC fire produced four BB blueprints, all degraded, all
`script_too_long` on attempt 1 (16.0s budget) AND on the 85% retry (13.6s).
`fit_margin: 0.0` was live and did not help.

The reason string is now correct — `script_too_long`, not
`script_generation_failed` — which is yesterday's reason-preservation fix
working. Yesterday the DB inverted the diagnosis; today it stated it.

**The rejected script texts do not exist anywhere.**
`_validate_narration_with_retry` returns `("", reason)` and discards the
candidate: not persisted, not logged, not in run artifacts. So we reject on
length without ever recording *by how much* — the one number that sizes the
fix. Filed as a follow-on; the reproduction that would have supplied it was
blocked by the credit exhaustion below.

### Root cause: the prompt contradicted its own cap

`_build_narration_hint` emitted a hardcoded **"2-4 sentences"** at every window
size, alongside a cap derived from the window:

```
16.0s window -> fit 14.0s -> HARD word cap 32 words
                          -> "2-4 sentences of ORIGINAL commentary"
```

Two to four sentences of spoken commentary is ~30-80 words. A model given a
range writes near its middle: 3 sentences at ~17 words is 51 words, against a
32-word cap. **The prompt asked for something it simultaneously forbade**, and
the 85% retry inherited the same contradiction, which is why both attempts
failed identically on all eight blueprints across two days.

Third instance of the NARR-11 class — one contract, two implementers, allowed
to drift. NARR-11: the cap and the validator each hardcoded a speaking rate.
Here: the cap and the sentence ask each hardcoded a length.

### Fix 1 — derive the sentence ask from the cap

```
10s -> cap  18w -> "exactly 1 sentence"
16s -> cap  32w -> "1-2 sentences"
22s -> cap  47w -> "2-3 sentences"
28s -> cap  61w -> "3-4 sentences"
60s -> cap 136w -> "7-8 sentences"
```

Short windows collapse to a single sentence rather than a range: at 10s, asking
"1-2" already projects to ~25 words against an 18-word cap — the same overshoot
one size down.

This removes the class, not the instance. Raising the window now widens the ask
automatically instead of silently under-using the budget.

### Fix 2 — #226: BB `highlight_moment.window_seconds` 16 -> 28

28s yields 61 words / 3-4 sentences. It stays clear of the >=15s duration guard
that the 2026-07-09 bump to 16s existed to satisfy, so arm attribution keeps
flowing when `motion_compositor` skips intro/outro.

**Trade-off acknowledged in the config comment.** The 2026-07-09 note already
called 16s *"4s longer than optimal for reaction pace"*. 28s is a deliberate bet
that a reel WITH narration outperforms a shorter one without. **#219's retention
baseline breaks here — any read spanning this change is invalid.**

### Pinning

`test_narration_prompt_coherence.py`, 18 tests, validated by inversion.

The first version of the central pin asserted against the sentence *floor* and
**passed at 16s** — the exact window production was failing on — because 2 x 14
= 28 fits a 32-word cap. A pin that survives the defect it exists to catch is
worthless. Rewritten to assert the **midpoint at typical length**, which is what
a model given a range actually targets; it now fails at 16s under the old
prompt.

A second pin initially grepped the function source for the literal
`"2-4 sentences"` and failed against the comment *explaining* the bug — the
same match-the-prose trap that produced several false findings this week. Now
behavioural: a derived range varies across windows, a hardcoded one cannot.

### BLOCKER — both LLM providers are out of credit

```
Anthropic: credit balance too low        alert raised 2026-08-22 06:45:03 UTC, open
OpenAI:    no credits remaining (fallback also dead)
```

Exhaustion began ~3h45m AFTER today's fire completed, so today's degradation is
genuine and not a credit artifact. But **tomorrow's 02:30 fire will produce
nothing at all** — not narration, not hooks, not captions — until credit is
restored. Both fixes above are untestable against a real fire until then.

Operator action required; this is a billing top-up, not a code change.
