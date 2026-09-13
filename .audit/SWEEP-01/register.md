# SWEEP-01 register — 2026-09-12, one session, time-boxed. Instances only; nothing fixed.
# Evidence: M=Measured (probe run, output read) | I=Inferred | D=Documented-only
# Every probe below ran a control first. Instrument failures caught mid-sweep are logged at the end.

## Class 1 — Config with no consumer

| # | instance | E | artifact | severity |
|---|---|---|---|---|
| 1.1 | `daily_post_limit` — declared in BlackboxBrief, ClutchWire, CriticalRush, _template. **Zero code references repo-wide** (incl. .ts/.tsx/dashboard). | M | 4× niche.yaml | HIGH — name implies it governs rule #10's 1-reel/day cap; it does not. An operator editing it would believe the cap changed. Real cap is elsewhere (DailyCapEnforcer / platform_caps.yaml). |
| 1.2 | `use_live_publishing` — 4 channels, zero references repo-wide. | M | 4× niche.yaml | HIGH — a flag that reads as a publish kill-switch and is inert. Setting it false would not stop publishing. |
| 1.3 | `min_score_threshold` — 4 channels, zero references repo-wide. | M | 4× niche.yaml | MED — implies a scoring floor that is not applied. |
| 1.4 | `use_crewai_flows` — gaming only, zero references repo-wide. | M | gaming/niche.yaml | LOW — dead framework toggle. |
| 1.5 | `emoji_style` | M | 4× niche.yaml | LOW |
| 1.6 | `niche_category` | M | 4× niche.yaml | LOW |
| 1.7 | `pexels_query_rule` | M | _template, FrameDrift | LOW |

**INVERSE FINDING (the class run backwards).** `short_video_maker_url` was the
*seed example* for this class — "config key, implementation deleted". It is
**wired end-to-end**:
  `gaming/niche.yaml:18  use_short_video_maker: false`
  `render_gaming_video.py:360  if flags.get("use_short_video_maker", False):`
  `render_gaming_video.py:362  self._try_short_video_maker(...)`
  `render_gaming_video.py:820  svm_url = settings.short_video_maker_url`
Only the `ShortVideoMakerClient` *class* was deleted; an inline reimplementation
survives in gaming's renderer. See Class 3 — this makes a CLAUDE.md claim false.

## Class 2 — Registered but never executed

**Largely UNMEASURABLE from this machine.** Execution evidence lives in the VPS
journal and prod DB; neither is reachable from a local read. Stating what would prove it:
  - per stage: `journalctl -u genlab-pipeline-<niche> --since '30 days ago' | grep '<StageName>'`
  - durable alternative: `metrics.jsonl` per run under `.tmp/runs/` (PipelineMetrics
    records per-stage timing) — survives journal rotation, and is the correct
    instrument because it is written by the runner, not by the stage.
  - zero rows for a stage that appears in a niche's stage list = instance.

| # | instance | E | note |
|---|---|---|---|
| 2.1 | All 28 gaming stages + backbone template stages | UNMEASURABLE | needs VPS journal or `.tmp/runs/*/metrics.jsonl`; not run this session |
| 2.2 | `_try_short_video_maker` — reachable only when `use_short_video_maker: true`, which is false on the only niche declaring it. Never executed in prod by construction. | M (source) | fails safe (returns None), but swallows at `logger.debug` — rule #19 shape |

## Class 3 — Documentation contradicting measurement  *(operator-designated priority)*

| # | claim in CLAUDE.md | verdict | E |
|---|---|---|---|
| 3.1 | "`ShortVideoMakerClient` has been removed — **there is no consumer and no second render engine**" | **FALSE** | M — consumer chain above. A flag-gated second render path exists in gaming. |
| 3.2 | "Only `single_clip` implemented today; other variants ship per session" | **FALSE** | M — `split_screen` and `storytime` both have compositor methods (`frame_compositor.py:462`, `:605`) and live dispatch in `base_visual_render.py`, flag-gated. 3 of 6 implemented, not 1. |
| 3.3 | "ai_creators is the CANARY with `whisper_sync.enabled = true`; gaming/sports/movies/anime remain disabled" | **TRUE for live niches** | M — BB=True, ClutchWire/FrameDrift/SpliceReel/gaming=False |
| 3.3b | (corollary, undocumented) `CriticalRush/niches/_template/config/visuals.yaml` ships `whisper_sync.enabled=**True**` | **CONTRADICTS POLICY** | M — any niche scaffolded from the template inherits the canary ON, against the documented "do not extend without a retention read". Hits the SaaS "new brand = new YAML" path. |
| 3.4 | "`_LEGACY_HARDCODED_SOURCES` is now `frozenset()`" | **TRUE** | M — `filter_gaming_stories.py:65` |
| 3.5 | "`platform_encode_specs.yaml` INACTIVE; only `platform_durations.max_seconds` read" | **TRUE** | M — sole consumer `publishing/transcode.py:67-73`, duration trims only |
| 3.6 | ~~"24 stages" is FALSE~~ — **THIS FINDING WAS WRONG. RETRACTED 2026-09-12.** | M | I counted `class:` lines in each niche's own YAML and reported that as the stage count, *after* having myself discovered that the backbone template supplies the rest. The declared count is the niche's inject contribution, not its pipeline. **Measured truth:** backbone `pipeline_template.yaml` = 17 concrete stages + 11 inject points; ai_creators/sports/movies declare 7 → **24 at runtime**; anime declares 8 → **25**; gaming is bespoke with no template → **28**. Independently confirmed by a real journal line in `.audit/NARR-01-plan.md:1195`: `[Pipeline] 24 stages: … 'BBVisualRenderStrategy' …` from an ai_creators fire. So "24 stages/niche" was CORRECT for three of five niches; the correction was the error. No CLAUDE.md or ARCH-02 edit was made on this item. |

## Class 4 — Tests pinning broken behaviour

**Zero confirmed instances this pass — and I distrust that result.** Three candidates
examined, all correctly ruled out:
  - `test_drawtext_escape_hardening.py`, `test_caption_animator.py:115` — assert the
    **post-fix** `'\''` idiom. Correct.
  - `test_fallback_trigger_contract.py:40-42` — assert 529/500/503 return `False`
    (no failover). Correct post-fix.
  - `test_engagement_store.py:160` — backslash-escaped quote. **Ruled out**: the
    formula is `AND({f}='..')` + `max_records`, i.e. Airtable syntax, where backslash
    escaping is correct (unlike OData, which doubles).
**Method limitation:** I searched for literals from *known* recent fixes. The class is
defined by tests pinning behaviour that a fix has *since* changed — which requires
diffing the last 30 days of fixes against the suite, not grepping known strings.
Not completed within the time-box. This class should be re-run properly.

## Class 5 — Gates that assert nothing

| # | instance | E | the input that yields false success |
|---|---|---|---|
| 5.1 | `scripts/verify_meta_usage_morning.sh:66-77` — `publisher_ran=$(systemctl show ... --property=ExecMainStatus \| grep -c "ExecMainStatus=0")` | M (source) + M (instrument confirmed earlier this session: `systemctl show` returns success-shaped output for `LoadState=not-found`) | **Unit missing or renamed** → no `ExecMainStatus=0` line → `publisher_ran=0` → falls to `else` → prints **"OK: publisher exited 0, 0 [meta_usage] lines captured"**. A publisher that does not exist reports OK. |
| 5.1b | same script, same line — the success message prints `$publisher_ran`, a **grep count**, formatted as `"publisher exited $publisher_ran"` | M | reads as an exit status; `0` reads as "exited cleanly" while actually meaning "found zero healthy-status lines" — the number means the inverse of how it reads. |
| 5.2 | `scripts/verify_whisper_canary_morning.sh:66` — `captioned_count=$(find "$latest_run/visuals" -name "*_captioned.mp4" \| wc -l)` | I | if `$latest_run` is empty/unset the `find` errors to /dev/null and yields 0; needs reading to confirm whether 0 is treated as failure or as "no work". Not completed. |
| 5.3 | remaining ~12 `scripts/*check*`, `*verify*` + `post_deploy_verify.sh` | NOT RUN | time-box. Each needs the "what input yields success without work" question answered individually. |

## Class 6 — Correct in isolation, wrong in composition

| # | pair | E | jointly-wrong state |
|---|---|---|---|
| 6.1 | `gate_tuner` daily calibration **×** Strategist `gate_threshold_override` (`auto_approval_gate.py:158-181`, `strategy_phase.py:92,242`) | M | The Strategist override **unconditionally overwrites** the tuner's value, and has **no expiry** — the only TTL in `strategy_phase.py` is a 300s in-process *cache*, not a validity window. Once an operator accepts one gate_threshold proposal, gate_tuner's daily output is discarded **forever** for that niche while continuing to compute, log and appear healthy. Same shape as the confirmed ratchet-vs-rail defect. **HIGH** — governs what auto-publishes on all 5 niches. Whether any niche currently holds an override: UNMEASURABLE locally (prod DB). |
| 6.2 | `hook_thumbnail` intro **×** `chart_broll` (`base_visual_render.py:499`, "mutually exclusive") | M(source) | known: chart_broll muted by design when an intro is prepended. Already recorded in memory as "muted by mutex, not broken" — but it is a missing abstraction, not a design. |
| 6.3 | `storytime` compositor **×** narration path (`transformation_orchestrator.py:131,548,567`) | M(source) | hand-written mutex; `narration_degraded_reason="storytime_mutex"`. The ARCH-02 exhibit. |
| 6.4 | `series_part` **×** `watch_till_end` (`watch_till_end_selector.py:141`, "series_part wins over") | M(source) | two structural variants competing for one story; precedence hand-coded. |
| 6.5 | env kill-switch **×** per-niche YAML (`auto_approver.py:357`, "wins over any per-niche YAML setting") | M(source) | env wins globally; a per-niche enable is silently inert while the env var is set. |
| 6.6 | explicit kwarg **×** env override — `hook_diversity_cache.py:270` (env wins) vs `youtube_quota.py:145` (**kwarg wins**) | M(source) | **the two modules resolve the same conflict in opposite directions.** A caller reasoning from one will be wrong about the other. |
| 6.7 | `gate_tuner` override lookup failure → `logger.debug` (`auto_approval_gate.py:154`) while the sibling Strategist lookup failure → `logger.warning(exc_info=True)` (`:178`, elevated 2026-07-21 per rule #19) | M | the rule-#19 elevation was applied to one of two adjacent handlers in the same function. The unelevated one silently reverts thresholds to default. |

## RETRACTION (recorded 2026-09-12, same session)
Finding **3.6 was false** — see the row above. It was produced by the exact error the
sweep exists to catch: a structural count taken from the wrong artifact, reported as
measurement. It was caught only because §R required running the falsifying probe
*before* editing CLAUDE.md. Had §R been executed as written, a correct statement in
CLAUDE.md would have been replaced with an incorrect one.
**Rule reinforced:** a correction is a claim and carries the original's burden of proof.

## Instrument failures caught during this sweep (T-20 working as intended)
1. Class 1 control: the known-dead key returned `readers=1`, not 0 — the count included
   its own **definition site**. Refined to exclude definition files. Had I not run the
   control, every key with a `settings.py` field would have looked live.
2. Class 3 whisper probe: `grep --include=*.yaml ... | grep -i enabled` returned **zero
   across all niches**, because `whisper_sync` and `enabled` sit on different YAML lines.
   A uniform zero, correctly distrusted; re-run with a YAML parser gave the real answer.
3. Class 1 scaled scan initially searched only 7 directories; the four HIGH findings were
   re-verified repo-wide (incl. dashboard .ts/.tsx) before being recorded.
4. **The stage-count probe itself was the broken instrument** (finding 3.6, retracted).
   `grep -c "class:" <niche>/config/niche.yaml` answers "how many stages does this file
   declare", which is NOT "how many stages run". The correct instrument is the expanded
   list the runner logs at fire time (`[Pipeline] N stages:`) or template + declared.

## What this sweep could not find (stated plainly, per the brief)
Anything requiring the system to **run**. No stage-execution evidence (Class 2) was
obtainable locally. Class 5 entries are defects **in the logic as written** — proving
they fire needs a negative control against a live systemd host. Class 6 pairs are
reported as pairs, not verdicts: none has been shown to have *actually* mis-fired in
prod, only to be capable of it. 6.1's live impact needs one prod query.

## Deferral ledger (not examined this session)
- Class 2 in full (needs VPS journal / metrics.jsonl sweep)
- Class 4 proper method (diff 30 days of fixes against the suite)
- Class 5: ~12 remaining verifier scripts + post_deploy_verify.sh
- The 59-flag enumeration (Class 1 second half) — only niche.yaml keys were scanned;
  env-var flags (`GENLAB_*`) were not.


# =====================================================================
# §R §2 RESULT — 2026-09-12 ~20:45Z. Read-only. VPS reachable; real code path used.
# =====================================================================

## 6.1 CORRECTED, then CONFIRMED LIVE on 3 of 5 niches.

**First, the register's own overstatement.** I wrote the override "discards the tuner
FOREVER". Wrong. `strategy_phase.py:158-169` selects the *latest reviewed report with
accepted proposals*, `ORDER BY run_at DESC LIMIT 1`. A newer reviewed report supersedes
it. The real defect is narrower and sharper: **there is no staleness bound on that
query**, so if reports stop arriving, an arbitrarily old one governs indefinitely.

**That is exactly what has happened.** Measured via `get_phase_config()` (the same
function the gate calls), compared against `get_overrides_for_niche()` (the tuner):

| niche | tuner today | governing override | governing report | effect |
|---|---|---|---|---|
| ai_creators | 0.50 | none | 2026-08-13 | tuner active |
| anime | 0.50 | none | 2026-08-13 | tuner active |
| gaming | 0.16 | **0.22** | 2026-08-13 | stricter than calibration |
| movies | 0.48 | **0.38** | 2026-08-13 | more permissive |
| sports | 0.50 | **0.25** | 2026-08-13 | **half as strict as calibration** |

Every governing report is **30 days old**. sports and movies have been auto-approving
against a materially more permissive composite gate than daily calibration computes.

## NEW — root cause chain (found while answering §2; NOT in the original register)

| # | finding | E |
|---|---|---|
| R.1 | `genlab-strategist.service` **is failing**: `Result=exit-code`, `ExecMainStatus=1`, last fire 2026-09-06 07:30 IST. Timer is healthy (`ActiveState=active`, `Persistent=yes`, next Sun 2026-09-13 07:30 IST) — the *timer* works, the *job* fails. | M |
| R.2 | No strategist report has been written since **2026-08-16** (6 reports/niche total). Newest *governing* (reviewed + accepted) is 2026-08-13. So ~4 weeks with no new report and ~30 days with no new governing value. | M |
| R.3 | Failure reason **UNMEASURABLE**. `genlab-strategist.service` has `StandardOutput=journal`, `StandardError=journal` and **no file artifact**; `journalctl -u genlab-strategist.service` returns "No entries" (41.9M total journal, rotated). A **weekly** job whose only diagnostic sink rotates faster than its own cadence is un-diagnosable by construction. What would prove it: re-run `scripts/run_strategist.py` manually capturing stdout/stderr to a file (a WRITE action — not taken, §2 is read-only). | M (config) / UNMEASURABLE (cause) |

**The composition, stated plainly.** Four individually-correct mechanisms:
timer retries weekly · query takes the latest reviewed report · tuner recalibrates daily ·
gate prefers the deliberate operator value. Jointly: a failing job freezes a 30-day-old
threshold that silently outranks daily calibration on three niches, and the evidence of
why the job failed is deleted before the weekly cadence brings anyone back to look.

**Fix scope unchanged from the brief, now with a prerequisite:** the strategist failure
(R.1/R.3) must be diagnosed first — an expiry on the override would, on its own, hand
all five niches back to the tuner without anyone understanding why the strategist stopped.
Recommended order: (a) capture the strategist failure to a file, (b) fix it,
(c) add the override expiry + age-at-read logging.

# =====================================================================
# §R2 §A + §B — 2026-09-12 ~21:10Z. Read-only + one dry-run (no persist).
# =====================================================================

## §A — The two gates are DIFFERENT quantities, SEQUENCED, and COUPLED.

| | `gate_threshold_override` | `min_confidence` (publishing.yaml) |
|---|---|---|
| consumer | `auto_approval_gate.evaluate()` → `min_composite_score` | `auto_approver` step 4 |
| compares | `extra["composite_score"]` vs threshold (`:277`) | `decision.confidence` vs 0.65 |
| binds | **FIRST** — inside evaluate(), as one of 5 checks | SECOND — on evaluate()'s output |

**They are not the same value.** Yesterday's 0.65 work is not overwritten.

**But they are coupled, two ways:**
1. `approved = len(failed) == 0` (`:459`). A failed composite check sets approved=False
   outright — so the composite threshold decides approval before confidence is consulted.
2. The composite check's confidence contribution is computed *relative to the threshold*:
   `span = max(0.001, 1.0 - thr); conf = 0.7 + 0.3*min(1, (composite-thr)/span)`.
   A LOWER threshold yields a HIGHER confidence for the identical blueprint. So the stale
   override also inflates the number `min_confidence` then tests. (Diluted in the final
   score, which is the mean across all 5 checks — do not over-read this second channel.)

**Blast radius, measured on DISTINCT blueprints, last 30 days:**

| niche | live override | tuner today | distinct approved in band | effect |
|---|---|---|---|---|
| sports | 0.25 | 0.50 | **16 of 16 (100%)** | every sports auto-approval in 30d clears ONLY because of the stale threshold |
| movies | 0.38 | 0.48 | 0 of 15 | override has no practical effect |
| gaming | 0.22 | 0.16 | 0 of 40 | override is STRICTER than calibration — conservative, no harm |

**So: sports is the only niche materially affected, and it is affected totally.**
Approval readings taken on sports need re-interpreting; gaming and movies do not.

### Counting error caught mid-analysis (recorded, per T-20)
I first reported "1192 of 1192 sports blueprints". Wrong by ~75x. `gate_examinations`
logs **every evaluation**, and the auto-approver re-examines the same blueprint every
30 min, 06:00–22:00 — one week shows 618 rows across **3** distinct blueprints. The
contradiction with an earlier 14-day count (n=45) is what surfaced it. Any rate computed
off this table MUST use `COUNT(DISTINCT blueprint_id)`.

## §B — precondition ANSWERED, and it fails in composition.

`scripts/run_strategist.py` does **not** write `reviewed_at` or `proposals_accepted`.
Reports land PENDING and cannot become governing on their own. Precondition satisfied
*in isolation*.

**But:** `scripts/auto_accept_strategist_proposals.py:194,214` stamps
`reviewed_at = NOW(), reviewed_by='auto'` on unreviewed reports, and runs on
`genlab-strategist-apply.timer` — **daily 08:30 IST, next ~6h out**. A real re-run
therefore becomes governing within hours, without an operator ever reviewing it.
The precondition as posed ("does the re-run mark its own output reviewed?") answers
*no*; the safe answer is *no, but a different timer will*. Another Class 6 instance.

### Dry-run result (safe path taken)
`run_strategist.py --dry-run` → **exit 0, 5/5 niches, 0 failures**, captured to
`/opt/genlab/.runtime/strategist_diag/dryrun_20260912.log` (a file, not the journal).
State collection is HEALTHY. `--dry-run` skips both the LLM call and the persist, so
the failure (`ExecMainStatus=1`) is isolated to **the LLM call or the persist step**.
Note all 5 niches report `proposals=0` even in dry-run — the collector finds nothing
to propose, which is itself worth a look once the runner is fixed.

**NOT taken:** a real re-run. It would be auto-accepted at 08:30 IST and become
governing, changing what auto-publishes — which §B forbids for a diagnostic.
Options for the operator: (a) mask `genlab-strategist-apply.timer` for the window,
(b) real run then delete the report before 08:30 IST, (c) reproduce the LLM call
in isolation without the persist.

# =====================================================================
# §R3 — 2026-09-13 ~04:10Z. Masked re-run ABORTED (see below); root cause found.
# =====================================================================

## §A — the re-run was unnecessary. The strategist FIXED ITSELF, and the real cause is elsewhere.

State changed between §R2 and §R3. Measured at 04:04Z on 2026-09-13:
  * `genlab-strategist.service`  fired 07:30 IST today -> **Result=success, ExecMainStatus=0**,
    exited 07:38:13 (≈8 min, consistent with real LLM calls).
  * `genlab-strategist-apply.service` fired 08:30 IST -> **Result=success, ExecMainStatus=0**.
The `ExecMainStatus=1` of 2026-09-06 did not recur. **There is no longer a failure to
reproduce**, so the real re-run was not performed — it would have exercised a healthy path.

`systemctl mask` FAILED: "File /etc/systemd/system/genlab-strategist-apply.timer already
exists" — mask creates a /dev/null symlink and refuses when a real unit file occupies the
path. **`stop` achieved the goal** (ActiveState=inactive, NEXT="-"). Timer restored and
verified: NEXT = Mon 2026-09-14 08:30 IST.
*Generalises: `mask` is not available for units with a real file at that path; `stop` +
`disable` is the working idiom, and the "is it masked?" check must read ActiveState, not
assume the mask succeeded.*

## ROOT CAUSE — why fresh reports never supersede (this is the real defect)

Both jobs succeeded hours ago, yet the governing override is STILL the 2026-08-13 report
(gaming 0.22 / sports 0.25 / movies 0.38 — unchanged). Measured reason:

**Every strategist_report since 2026-08-16 has `reviewed_at = NULL`.**
The 2026-08-13 batch all carry the identical timestamp `2026-08-15 06:04:43` — the
signature of the one-time backfill, not of ongoing behaviour.

`auto_accept_strategist_proposals.py::_mark_completed_reports` stamps `reviewed_at` **only
when every proposal is triaged** (`COUNT(DISTINCT idx) over accepted ∪ rejected >=
length(proposals)`). Its own docstring: *"Reports with even one un-triaged proposal
(usually operator_gate punts) stay NULL — operator still owes those."*

Measured triage state of every report since 2026-08-16: **all have un-triaged proposals.**
Today's: ai_creators 4 props/0 triaged, sports 7/4, movies 8/3.

**So `auto_accept` is not buggy. This is a Class 6 composition defect:**
  * `auto_accept` correctly leaves operator-gated proposals for a human — correct.
  * `strategy_phase` correctly requires a *reviewed* report before honouring an override — correct.
  * the operator has not triaged since 2026-08-15 — not a defect.
  * **Jointly:** the override freezes at the last fully-triaged report and silently
    outranks daily calibration, indefinitely, with no surface showing the age.

The earlier framing ("the strategist is broken") was wrong. The strategist is fine.
The freeze is caused by a *human-triage dependency* that nothing surfaces or bounds.

**This changes the §C fix.** An expiry alone treats the symptom. The options are:
  (a) bound the override by age (§C as briefed) — still correct, still needed;
  (b) have `strategy_phase` prefer the newest report with an accepted gate_threshold
      regardless of full-triage state — changes what "reviewed" guarantees;
  (c) surface un-triaged proposal age on Mission Control so the human dependency is visible.
(a) + (c) together are the minimum. (b) should not be taken without deciding what
`reviewed_at` is supposed to mean.

## §D — `_template` audit: whisper_sync was ONE of ELEVEN drifted keys.

Fixed: `whisper_sync.enabled: true -> false` (with rationale in-file).

**The remaining ten — every one disagrees with ALL FOUR live niches. Reported, not fixed:**

| key | _template | live niches | consequence for a new niche |
|---|---|---|---|
| `ffmpeg.preset` | `slow` | all four `medium` | slower encode on a 4 GB VPS (CLAUDE.md documents `fast` — a third value again) |
| `ffmpeg.timeout_seconds` | `120` | all four `600` | **compounds with the above: slowest preset + 1/5th the timeout = near-certain render timeout** |
| `channel_handle` | `@framedrift` | per-channel | **a new niche publishes attributing FrameDrift's handle** |
| `feature_flags.platforms_enabled` | 6 entries | all four 5 | enables a 6th platform; rule #23 scopes to 4 |
| `pipeline.max_items_per_run` | `4` | all four `10` | |
| `pipeline.min_score_threshold` | `0.42` | `0.2`–`0.25` | |
| `video_sourcing.top_n_per_run` | `5` | all four `15` | |
| `video_sourcing.…min_composite_score` | `0.3` | `0.15`–`0.32` | |
| `video_sourcing.…velocity_threshold` | `600` | `80`–`400` | 1.5–7.5× stricter |
| `freshness.max_story_age_hours` | `72` | movies `1440`, anime `720` | |

Taken together a niche scaffolded today would render past its own timeout, publish under
another channel's handle, target an out-of-scope platform, and filter so aggressively it
would likely produce zero blueprints — while looking correctly configured.
**This is the SaaS "new brand = new YAML" path.** The operator's prediction that one drift
implies others was right by a factor of ten.

## §E — filed, and it is NOT the next failure along; it was a red herring
Dry-run showed `proposals=0` for all five niches. The real 07:30 run produced 4–8 proposals
each. `proposals=0` is an artifact of `--dry-run` skipping the LLM, not a health signal.
Nothing to investigate. (Recorded so the earlier note is not acted on.)
