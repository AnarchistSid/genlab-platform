# The Infrastructure-Half-Wired Anti-Pattern

A reference document — not a one-off session writeup. **Read this before
any deep-dive audit of Gen Lab.** It defines a recurring failure mode,
catalogs the nine instances we've found and fixed (mostly during the
2026-06-14 session), describes the consistent fix-shape, and gives an
8-question checklist for finding more.

---

## The pattern, in one paragraph

A *component* is built carefully — usually with tests, documentation,
and a complete code path. But the **integration that makes the
component actually run** is missing, broken, or wired in a way that's
indistinguishable from "off." The component looks correct in isolation
(its tests pass, its code reads well) but produces zero or buggy output
in production. The bug is never in the component; the bug is at the
seam between the component and the system that should be feeding it
inputs or consuming its outputs.

This is distinct from "the code is buggy" and from "the feature wasn't
built." The code is fine, the feature is built — but the *wiring* is
half-done. Hence: **infrastructure-half-wired**.

The pattern compounds over time because each layer's half-wiring hides
behind the layers below it. *Eventually you have a system where every
individual component works, the integration is comprehensively wired
on paper, and the actual output is zero.* See: the Gen Lab autonomy
state on 2026-06-12 — fully designed, mostly built, ~$2/day of
LLM/API spend, 0 posts in the most-recent 3-week window.

---

## Nine instances we've found and fixed in this codebase

Each row follows the same shape: a component built well, an
integration left half-wired, the operational symptom that surfaced
the gap, the fix.

| # | Half-wired component | Symptom | Fix (PR) |
|---|----------------------|---------|----------|
| 1 | **BacklogClient Postgres path** — full code path existed but never invoked on the Tier 2 store branch | Stories created in Postgres never made it to blueprints | PR #175 — `_construct_tier2_stores` wiring |
| 2 | **calibration_logger.log()** — wrapped in fail-open try/except so a missing-table case silently swallowed every operator click | `AutoApprovalCalibrationCard` showed 0/30 forever despite operator activity | PR #185 (deploy probe) → operational migration apply |
| 3 | **prod deploy mechanism** — CI applied migrations to ephemeral pgvector DBs but no workflow / cron / runbook deployed to prod | Prod 30+ commits behind main since 2026-06-11 with no alert | PR #186 — `deploy.sh` + DEPLOYMENT.md |
| 4 | **daily_intel.sh UV resolution** — hardcoded `${HOME}/.local/bin/uv` worked under one systemd context, broke under another | Pipeline crashed at line 37 on first scheduled fire after operator changed `User=` to `genlab` | PR #187 — `command -v uv` resolution chain |
| 5 | **PushToBacklog _captioned.mp4 path** — pipeline stored captioned-variant path in `extra.visual_paths` with no guard against using it when whisper_sync was later disabled | Dashboard served stale captioned files for 7 active blueprints (the "giant ELDENRING" screenshot) | PR #189 — defensive `_strip_captioned_when_whisper_disabled` guard |
| 6 | **archive_orphan_drafts** — had Branch 1 (no-video, 7d) + Branch 2 (failed-video, 14d) but no branch for "render-never-completed with video_id" (the WARP-outage failure mode) | 94 youtube_trending DRAFTED accumulated 2026-06-01 → 2026-06-09 with no auto-cleanup | PR #190 — Branch 2a (render-never-completed, 7d) |
| 7 | **_next_available_slot** — collision check counted every existing record's `scheduled_for` as occupied including the record being re-scheduled itself | All afternoon-pipeline approvals silently shifted +1 day (movies, sports, ai_creators, anime — gaming immune by accidental timing) | PR #191 — `exclude_record_id` parameter |
| 8 | **`shared_sources.yaml`** — 711 source entries kept being fetched at full cadence regardless of whether downstream pipelines could ever use them | 119 sources at 0% claim rate over 14 days; ~574 wasted fetches/day | PR #193 — `FeedHealthTracker.refresh_zero_claim_disables` |
| 9 | **bootstrap_product_embeddings.py** — 256 LOC of careful design that crashed on line 222 with `TypeError: 'bool' object is not callable` | The PA-API embedding bootstrap was unrunnable from day-one; `product_embeddings` table 0 rows for weeks | PR #194 — call `available` as `@property` (no parens) + fix misleading error message |

---

## The fix-shape (consistent across all nine)

Every one of those PRs follows the same three-part pattern:

```
1. Probe that fails LOUDLY when the integration is broken
2. Documentation that says how the integration is meant to work
3. The integration itself, with conservative thresholds + fail-open behavior
```

| Instance | The probe | The doc | The integration |
|----------|-----------|---------|-----------------|
| #2 (calibration) | PR #185 deploy script's pre-flight | (existing CLAUDE.md note about fail-open) | Operational `alembic upgrade head` |
| #3 (deploy) | PR #186 `deploy.sh` dry-run refuses on drift | PR #186 `DEPLOYMENT.md` | PR #186 `deploy.sh --apply` |
| #4 (UV) | (latent — caught by service-restart) | docstring rewrite | PR #187 fallback chain |
| #5 (captioned) | Frame-extraction proved the bug visually | docstring + memory note | PR #189 PushToBacklog guard |
| #6 (orphan drafts) | The DRAFTED-count query | docstring "Three flavours of orphan" | PR #190 Branch 2a SQL |
| #7 (scheduler) | The 2-movies-blueprint trace | docstring + memory note | PR #191 `exclude_record_id` |
| #8 (zero-claim sources) | The `content_pool` claim-rate query | docstring on the new methods | PR #193 `refresh_zero_claim_disables` |
| #9 (embeddings) | `m.available()` TypeError reproduction | error-message rewrite | PR #194 attribute access |

The recurrence tells us: **shipping the integration alone is not
enough**. The probe makes future regressions surface within one
operator-session instead of one outage-window. The doc makes the
integration findable by the next person who touches the area.

---

## The checklist for finding more

When auditing any subsystem in Gen Lab (or any video-content
platform with similar shape), walk through these eight questions.
Each is a "yes/no — and prove it" check. Any "I'm not sure" answer
is an unaudited candidate.

1. **Does this component produce output that another component
   consumes?** If yes, name both components.
2. **Is the consumer actually wired to read from the producer's
   actual output channel today?** Verify by tracing: producer's
   write call → consumer's read call → matching path/field.
3. **Does the consumer's failure-mode for "producer's output is
   missing" distinguish between "producer hasn't run" and
   "producer doesn't exist"?** If both fall through to the same
   silent fallback, the integration can be 100% broken without
   surfacing.
4. **Is there a probe that would fail loudly if the integration
   is broken?** A test, a health check, an alert, an SLO. *"The
   pipeline keeps running"* is not a probe.
5. **Does the integration assume a specific environment** (env
   var, systemd user, HOME directory, on-disk path)? **Has every
   assumption been tested under the actual runtime context?**
   This is where #4 and #7 lived: code that worked in tests
   broke under production systemd context.
6. **Does the output have a cleanup story?** Anything that
   accumulates DB rows or files needs a corresponding
   auto-eviction mechanism. PRs #6, #8, #189 all hit this.
7. **Does the integration have a fail-open path?** If yes,
   does the fail-open distinguish "transient" (DB briefly down)
   from "structural" (table missing, env var unset)? PR #2 was
   the canonical example: fail-open swallowed a missing table
   for ~24 hours.
8. **If the integration has been in place > 30 days, is there
   data showing it actually fires?** Query the DB. Read the
   metrics. If the answer is "I don't know," that's #8 again —
   the inflow may be running while the outflow does nothing.

A subsystem that passes all 8 checks is *probably* fully wired.
A subsystem that fails 2+ is *almost certainly* half-wired.

---

## Evidence the rule is right: data from 2026-06-14

The 2026-06-14 prod recovery created the `auto_approval_calibration`
table (instance #2 from the table above). As of 15:30 IST today
— five hours after the recovery — the table has **5 rows**. Each
row is an `(operator_action, gate_verdict)` pair. The
calibration_logger code that produces these rows has been on
prod since 2026-06-13 morning. **For nearly 24 hours it was
fail-open-silent on a missing table, producing zero rows.** After
the recovery, the same code is producing rows at ~1/hour.

The component (calibration_logger) was perfect. The integration
(table existence) was missing. The system looked correct in
isolation; the output was zero. Five rows in five hours is the
shape of "the integration is now intact."

This is the strongest evidence we'll get that the anti-pattern is
real and that the fix-shape works.

---

## How to use this document in future sessions

Future Claude session (or operator) auditing Gen Lab:

1. **Before investigating a "why is X not working" finding**: run
   the 8-question checklist against the surrounding subsystem.
   Frequently the bug is two layers up from the symptom.

2. **Before recommending a code refactor**: ask whether the
   architectural pain is *the design* or *the wiring*. The
   half-wired pattern looks like design pain ("this whole layer is
   broken") but the cure is often single-digit-LOC wiring.

3. **Before declaring "the feature is done"**: confirm there's a
   probe + doc + integration triple. Without all three, the
   feature has a non-trivial chance of being half-wired the
   moment its surrounding context changes.

4. **When prioritizing bug fixes**: half-wired bugs compound
   because they hide. A 10-LOC half-wiring fix often unlocks
   features worth weeks of work. The 2026-06-14 session converted
   one screenshot ("ELDENRING text is giant") into 11 PRs by
   following the half-wired thread up through the system.

---

## Related memory files

- `[[session_2026_06_14_deploy_pipeline_gap]]` — instance #3 in detail
- `[[session_2026_06_14_auto1_migration_drift]]` — instance #2 in detail
- `[[session_2026_06_14_captioned_mp4_cleanup]]` — composite of instances #5, #6, #7, #8, #9
- `[[feedback_infrastructure_half_wired_pattern]]` — pre-2026-06-14
  observations that named this pattern; this doc is the formalization
- `docs/runbooks/MERGE_2026-06-14.md` — the operational landing plan
  for the 10 PRs that fixed instances #4–#9

---

**Last updated:** 2026-06-14, after the session that shipped instances
#4 through #9. Will need a refresh when the next session finds more
(it will).
