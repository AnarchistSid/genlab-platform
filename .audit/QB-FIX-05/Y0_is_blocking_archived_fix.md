# QB-FIX-05 Y0 — `is_blocking()` ARCHIVED Short-Circuit

**Date:** 2026-08-06 23:05 IST
**Status:** SHIPPED. Runtime predicate now returns False on ARCHIVED regardless of approved/scheduled_for state. No data backfill required.

## Pre-fix aggregate

```sql
SELECT status,
       action_taken IS NOT NULL AS approved,
       scheduled_for IS NOT NULL AS scheduled, COUNT(*)
FROM blueprints GROUP BY 1,2,3 ORDER BY 1,2,3;
```

```
 status       | approved | scheduled | count
--------------+----------+-----------+-------
 ARCHIVED     | f        | f         |   389
 ARCHIVED     | t        | f         |   731
 ARCHIVED     | t        | t         |   601
 DRAFTED      | f        | f         |     6
 PUBLISHED    | f        | t         |     1
 PUBLISHED    | t        | f         |    13
 PUBLISHED    | t        | t         |   476
 VISUAL_READY | f        | f         |     6
 VISUAL_READY | t        | t         |     9
```

Rule 2 fires when `action_taken == 'approved' AND scheduled_for IS NOT NULL`. Wider aggregate `approved (any) AND scheduled` counts 601 ARCHIVED rows; narrower query where `action_taken = 'approved'` specifically:

```
ai_creators: 38, movies: 20, sports: 18, gaming: 15, anime: 8 = 99 phantom blockers
```

The 99 are the rows historically constraining URL re-fetch across all 5 niches from ARCHIVED state.

## Fix

`genlab-core/src/genlab_core/pipeline/video_id_dedup.py:is_blocking()` — 3-line short-circuit added before existing checks:

```python
status = fields.get("status", "")
# QB-FIX-05 Y0: ARCHIVED is terminal — no publisher path will ever
# pick an archived row. Short-circuit before rule 2 to prevent
# phantom dedup pressure from stale approved-scheduled state.
if status == "ARCHIVED":
    return False
```

Reasoning: ARCHIVED is terminal — no code path publishes an archived row, so no dedup path should block a re-fetch on account of one. Rule 2 was added 2026-07-06 to catch mid-migration commitments (task #525) but doesn't need to consider terminal-state rows.

## Backfill decision

**None applied.** Per §2 Step 2 preference ("Prefer the code fix making the backfill unnecessary"): the predicate is called at pipeline runtime; the DB rows still carry `action_taken='approved' AND scheduled_for` populated (601 total), but the fixed predicate returns False on ARCHIVED before checking those fields. No SQL needed. Future pipeline runs naturally see previously-blocked URLs as fetchable.

X0-a's earlier cleanup (NULL scheduled_for on 17 rows) is now redundant but harmless.

## Pin test

`test_video_id_dedup.py::test_is_blocking_archived_short_circuits_over_rule_2`:

```python
row = {"fields": {"status": "ARCHIVED", "action_taken": "approved",
                   "scheduled_for": "2026-07-30T10:00:00Z"}}
assert is_blocking(row) is False
```

Both nested-`fields` shape and flat-row shape covered. Pre-Y0 parametrized test covered `ARCHIVED` without rule-2 inputs — the phantom-blocker case had no coverage. Test fills the exact hole.

All 28 `test_video_id_dedup.py` tests pass locally (27 pre-existing + 1 new).

## Gate

Pre-fix:
- DB: 99 rows in phantom-blocker shape (measured pre-fix, unchanged since — code-only fix)
- Runtime: `is_blocking({status: 'ARCHIVED', action_taken: 'approved', scheduled_for: '...'})` = **True** (BAD)

Post-fix (verified live on VPS after deploy):
- DB: 99 rows (unchanged — code-only fix, correct)
- Runtime: `is_blocking(...)` = **False** ← runtime probe on VPS confirmed PASS

## Passthrough gate — deferred to next pipeline run

Per §2 gate: "on a real pipeline run, confirm the dedup stage's candidate-rejection count drops."

**Deferred to Y2's sports run** — Y2 will trigger the sports pipeline, exercising the fixed predicate. Passthrough measurement captured there.

## Consequence reaches backwards

The F3d-1-gate session (2026-08-06) attributed movies' dedup saturation to 11 "active" blueprints. After ME-09 corrected the TTL reading, that count was believed accurate. **After Y0, the true active set is smaller than that count too** — archived rows counted toward it via rule 2 and never should have.

Per-niche dedup passthrough may improve on next pipeline runs without any config change. This could re-open sourcing on niches that were silently constrained.

## Class-of-bug

Same shape as ME-11: composite predicate with N OR-gates where one path (rule 2) bypasses the status check another path (rule 1) respects. Terminal-status short-circuit is the canonical fix — for any `is_X()` predicate on rows with a state machine, verify that terminal-state rows return the expected inactive answer regardless of what other fields say.

## Commit

`fix(dedup): short-circuit is_blocking on ARCHIVED status` — `4963169d`
