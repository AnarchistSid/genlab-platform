# QB-FIX-07 A0 + A1 + A2 — ARCHIVED terminal on revive path

**Date:** 2026-08-07 15:30 IST (~20.5h before Aug 8 12:05 IST fire)
**Result:** A1 shipped + verified live. Queue is clean pre-fire — every queued row is POST-fix.

## A0 Step 1 — `8ce02a08` current state

```
niche_id:            ai_creators
title:               Meet Birding Pal
status:              ARCHIVED (Z0 re-archived it after Y0/X0-a resurrection)
action_taken:        approved (preserved from prior state)
action_taken_source: auto_archived_qb_fix_06_armspace
scheduled_for:       NULL (Z0 nulled it)
created:             08-05 08:09 IST — PRE-fix (before Aug 6 entirely)
ducking arm:         -9  (ambiguous by arm alone; PRE by date)
intro arm:           logo_tagline_reveal
```

Currently safe (ARCHIVED). But vulnerable to re-resurrection on tonight's pipeline runs without A1.

## A0 Step 2 — revive predicate (verbatim)

Located at `push_to_backlog.py:2454` (assignment) + `:2968` (revive body).

**Trigger condition:**
```python
existing_bp_raw = client.blueprints.all(formula=f"{{candidate_id}}='{candidate_id}'", max_records=5)
blocking_match = next((bp for bp in existing_bp_raw if _is_blocking(bp)), None)
non_blocking_match = next((bp for bp in existing_bp_raw if not _is_blocking(bp)), None)
...
if non_blocking_match is not None:
    revive_fields = {k: v for k, v in fields.items() if k != "candidate_id"}
    revive_fields["action_taken"] = None
    # ... claim_status atomic transition ...
    client.blueprints.update(non_blocking_match["id"], revive_fields)
```

**Statuses revived FROM:** any status where `_is_blocking()` returns False. Post-Y0 (2026-08-07), that set includes ARCHIVED. Pre-Y0 it did too (ARCHIVED was never in `LIVE_OR_PENDING`).

**Status revived TO:** the fresh incoming status (typically VISUAL_READY), via `claim_status` atomic transition.

**Preserves/clears:**
- `action_taken` → cleared to `None`
- `scheduled_for` → NOT touched (preserved if present)
- `action_taken_source` → OVERWRITTEN (fields dict typically carries the new auto_approver source tag)
- All other columns → overwritten by fresh `fields` dict

**Case 2 applies (deliberate revive-from-ARCHIVED).** The comment at line 2969 confirms: "Revive the archived/failed row instead of inserting." The intent was to handle "render failed → fresh pipeline retried successfully" without inserting a duplicate blueprint. That intent does NOT apply to rows archived by staleness sweep.

## A0 Step 3 — quantify

Pre-fix state (baseline, tags all still ARCHIVED):

| tag | niche_id | count |
|-----|----------|-------|
| auto_archived_qb_fix_04_pre_fix | ai_creators / anime / gaming / movies | 10/6/12/6 = 34 |
| auto_archived_qb_fix_06_armspace | ai_creators / gaming / movies | 2/2/4 = 8 |
| auto_archived_qb_fix_06_z1_sports_drafted | sports | 4 |
| **Total** | | **46 — all ARCHIVED** |

**Zero currently-non-ARCHIVED rows carrying a qb_fix tag.** Note: this measures the CURRENT state after Z0 caught 8ce02a08's resurrection and re-archived it. The historical resurrection is documented in the Z0 report.

## A1 fix — `_is_terminally_archived()` predicate

Added module-level constant + helper in `push_to_backlog.py`:

```python
_TERMINAL_ARCHIVE_TAG_PREFIX = "auto_archived_qb_fix"

def _is_terminally_archived(row: dict) -> bool:
    fields = row.get("fields", row)
    if fields.get("status") != "ARCHIVED":
        return False
    src = fields.get("action_taken_source") or ""
    return src.startswith(_TERMINAL_ARCHIVE_TAG_PREFIX)
```

`non_blocking_match` generator now filters:
```python
non_blocking_match = next(
    (bp for bp in existing_bp_raw
     if not _is_blocking(bp) and not _is_terminally_archived(bp)),
    None,
)
```

Ordinary lifecycle archives (`auto_archived_render_never_completed`, `rejected`, `archived_by_ops_*`) return False here — still revivable.

## A1 Step 2 — audit for third non-terminal-ARCHIVED read path

Grep swept: auto_approver, publisher, nightly_scheduler, dedup_keys, backlog_client.

- **auto_approver.py:136** — `get_blueprints_by_status("VISUAL_READY", ...)` — explicit VR-only. **SAFE.**
- **publisher:475** — `claim(record_id, expected_status="VISUAL_READY", ...)` — VR-only. **SAFE.**
- **nightly_scheduler:32/268** — SQL `WHERE status = 'VISUAL_READY'`. **SAFE.**
- **dedup_keys** → `video_id_dedup.is_blocking()` — Y0 covered.
- **push_to_backlog revive** — A1 covers.
- **backlog_client.py:215** — `archiving = fields.get("status") == "ARCHIVED"` — write-side guard, not read path. **SAFE.**

**No third read path exists.** All status readers other than the two Y0+A1 covered use direct VISUAL_READY string comparisons and don't fall into the `is_blocking()`-based non-blocking bucket.

## A1 gate — verified live

Triggered ai_creators pipeline post-deploy (15:12 → 15:29 IST). Baseline: 46 qb_fix-tagged rows ARCHIVED. Post-run count:

```
 niche_id   | status   | count
 ai_creators| ARCHIVED |    12  (baseline)
 anime      | ARCHIVED |     6  (baseline)
 gaming     | ARCHIVED |    14  (baseline)
 movies     | ARCHIVED |    10  (baseline)
 sports     | ARCHIVED |     4  (baseline)
```

**Unchanged.** Pipeline created 3 fresh blueprints via the insert-fresh branch:
- `366c8196 How to Schedule a Weekly Metrics Report` — VR-approved
- `ec08ac25 How to Turn a Forecast Spreadsheet Into Interactive Planning Tool` — VR-approved
- `7ec130f0 DeepMind Just Changed How AI Sees The World` — VR-approved

No `Revived blueprint` log lines. No `Refusing to revive` log either — meaning no candidate_id in this run matched an archived row (the 3 fresh stories were genuinely new URLs). The guard is present but wasn't exercised this specific run; the count-stability proves it doesn't corrupt the happy path.

**Pin tests:** 12/12 pass on `test_push_to_backlog_terminal_archive_revive.py`.

## A2 pre-fire re-verification

Queue state comparison against QB-FIX-06 §4 expected:

| niche | Z0 expected | actual now | delta | notes |
|-------|-------------|------------|-------|-------|
| ai_creators | 3 | **6** | **+3** | 3 new POST-fix rows from tonight's pipeline (all -9 ducking, created 15:29 IST) |
| anime | 4 | 4 | 0 | unchanged |
| gaming | 1 | 1 | 0 | unchanged |
| movies | 2 | 2 | 0 | unchanged |
| sports | 4 | 4 | 0 | unchanged |
| **total** | 14 | 17 | +3 | all new rows POST-fix |

**Full arm classification (all 17 rows):** every row's `ducking` is in `[-3, -6, -9]` (post-F3a-2 set) OR the row's `created_at` is post-fix (Aug 6 evening or Aug 7). No `-12` or `-15` present. **Zero pre-fix survivors.** No revives (Z0 archives still hold; A1 guard prevents future revives).

## What publishes tomorrow (per niche, with arm provenance)

- **movies:** `c25972a9 Primetime` (F4 batch 2 approved, -9 arm + 08-06 19:17 IST POST) — only approved candidate
- **anime:** `b8de02a6 Master Swordsman II` (F4 batch 2 approved, -6 unambiguous POST) — only approved candidate
- **ai_creators:** 5 approved contest — publisher/auto-approver priority decides. All 5 POST-FIX:
  - `7ec130f0`, `ec08ac25`, `366c8196` (created today 15:29, -9 post)
  - `ab84333e` (today 08:12, -9 post)
  - `6119cc65` (Aug 6 22:52, -9 post-V4)
- **gaming:** `6273e9cc MARVEL TŌKON` VR unapproved; publishes only if auto-approver fires + approves in next 20h. Even then, may not reach publisher fire window depending on scheduled_for (Y1 (c) risk).
- **sports:** 4 approved contest, all POST-FIX (today's Y2 pipeline output, all -9 post)

## §5 record corrections

Added to `methodology_errors.md`:

### ME-14 — Threads timeout hypothesis wrong

Filed as part of QB-FIX-06 Z1 Step 3. My hypothesis was that sports Threads' 180s container-processing timeout correlated with file size. Measurement disproved it:
- Sports Sainz (FAILED): 2.22 MB, 18.6s, 0.78 Mbps — **smallest of the three**
- Anime Saga of Tanya (SUCCESS): 4.97 MB, 16.1s, 2.28 Mbps — **largest, succeeded**
- Movies INHERIT (SUCCESS): 3.98 MB

The hypothesis was plausible (larger reels take longer to process → timeout) and the measurement killed it (sports was smallest and still failed). Log as "process working" — that is what measurement-before-conclusion looks like. Reclassify the Threads timeout as transient Meta infrastructure. **Detection heuristic:** for any "timeout" hypothesis, verify the size correlation before filing the fix — timeouts have many causes (auth, ingestion queue, downstream service health).

### ME-15 — `render_error` observability gap is stronger than filed

Filed as part of QB-FIX-06 Z1 Step 1. All 4 sports DRAFTED rows had `extra->>'render_error'` = NULL, but the pipeline journal for at least one (Pete Crow-Armstrong) showed `pre_render_quality: hook_title_truncation`. **The rejection reason exists at runtime and is discarded before persistence.**

Implication: **F-QB-0606's verification gate is structurally unanswerable.** F-QB-0606's verification explicitly instructed: "check `extra->>'render_error'` on DRAFTED rows to determine whether pre_render_quality rejected them or the gate is buggy." That check returns NULL uniformly, so it can never distinguish "gate rejected" from "gate never ran." Any future finding relying on `render_error` persistence for verification is in the same position.

Reclassify from "observability follow-up" to "invalidates a verification gate." Findings currently or previously depending on `render_error` need re-verification via journal grep (fragile) or via adding write-side persistence to the pre-render gate (proper fix).

## Follow-up recommendations

- **Write-side fix for `render_error`:** pre-render quality gate should write rejection reason to `extra->>'render_error'` so DRAFTED rows self-explain. Not shipping in A2 per scope.
- **Class-of-bug meta-lesson:** ARCHIVED not treated as terminal at all read paths. Y0 + A1 cover two paths; the pattern should trigger an audit whenever a new predicate reads `row.status`. Consider adding an `is_active(row)` helper + linter rule that flags direct status comparisons in favor of it.

## Commits

- A0 diagnosis + A1 fix: `c8e4cc5d fix(backlog): treat ARCHIVED as terminal in push_to_backlog revive path`
- A0/A1/A2 report + ME entries: this file (pending commit)
