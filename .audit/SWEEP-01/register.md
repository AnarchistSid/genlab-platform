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
