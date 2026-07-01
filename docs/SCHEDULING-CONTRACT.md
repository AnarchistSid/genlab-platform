# Scheduling contract (auto-scheduler + publisher + retirement)

**Written**: 2026-07-01 after the disk-cleanup-cascade incident.
**Audience**: anyone doing manual DB surgery on `blueprints.scheduled_for` /
`blueprints.status` / `blueprints.action_taken`, and anyone extending the
publisher's slot-assignment logic.

---

## The three-field contract

Every blueprint's publish eligibility is determined by three fields:

| Field | Values | Contract |
|---|---|---|
| `status` | DRAFTED / VISUAL_READY / PUBLISHING / PUBLISHED / ARCHIVED | Publisher only picks VISUAL_READY. Auto-scheduler only assigns slots to VISUAL_READY. |
| `action_taken` | NULL / `'approved'` / `'rejected'` / `'cancelled'` | Publisher AND auto-scheduler only consider `approved`. NULL means "pending operator review". |
| `scheduled_for` | timestamptz / NULL | Publisher picks blueprints where `scheduled_for <= NOW()`. Auto-scheduler ASSIGNS this field when it finds an approved blueprint without a slot. |

**The publisher's pickup rule** (short version):
```
status = 'VISUAL_READY' AND action_taken = 'approved' AND scheduled_for <= NOW()
```

**The auto-scheduler's assign rule** (short version):
```
FOR each blueprint WHERE
  status = 'VISUAL_READY' AND action_taken = 'approved' AND scheduled_for IS NULL:
    scheduled_for = pick_next_available_slot(niche_id)
```

---

## The retirement rule (learned the hard way on 2026-07-01)

**To durably remove a blueprint from the publish queue, you MUST clear BOTH
`scheduled_for` AND either `status` OR `action_taken`.**

This is the trap the 2026-07-01 Round-1 recovery hit:

```sql
-- WRONG — the auto-scheduler will re-grab this blueprint within an hour
UPDATE blueprints SET scheduled_for = NULL WHERE id = ...;
-- Then auto-scheduler sees: status=VISUAL_READY + action_taken=approved
-- + scheduled_for IS NULL → assigns a new slot immediately.
```

The reason: `scheduled_for=NULL` is exactly the shape the auto-scheduler
looks for. Clearing only that field is equivalent to saying "please
schedule this fresh." Round-1 unscheduled 5 broken blueprints; the
auto-scheduler put 3 of them right back on tomorrow's slots within ~3
hours because their `status='VISUAL_READY'` + `action_taken='approved'`
made them re-eligible.

**Round-2 corrected** with the durable retirement pattern:

```sql
-- CORRECT — auto-scheduler cannot re-eligible this row
UPDATE blueprints
SET scheduled_for = NULL,
    status = 'ARCHIVED',
    action_taken = 'cancelled'
WHERE id = ...;
```

Setting `status = 'ARCHIVED'` alone is sufficient (publisher + auto-scheduler
both filter on `status = 'VISUAL_READY'`). Setting `action_taken = 'cancelled'`
alone is also sufficient. Doing both is belt-and-suspenders + gives future
readers explicit intent.

---

## What SHOULD `scheduled_for=NULL` mean, semantically

The operator intent when clearing `scheduled_for` is usually one of:

| Operator intent | Correct field set |
|---|---|
| "Skip this slot but let the blueprint auto-schedule to next open one" | `scheduled_for = NULL` (keep status + action_taken) — auto-scheduler picks it up |
| "Remove this blueprint from consideration forever" | `scheduled_for = NULL, status = 'ARCHIVED', action_taken = 'cancelled'` |
| "Send back to operator review" | `scheduled_for = NULL, action_taken = NULL` |
| "Delay by exactly N hours" | `scheduled_for = NOW() + INTERVAL 'N hours'` (never NULL) |

Round-1's error was intending the SECOND row but writing the FIRST row's SQL.

---

## The auto-scheduler's fire cadence

- `niche_pause_sweeper.timer` runs at 02:00 UTC daily. It sweeps VISUAL_READY
  approved blueprints without slots and assigns them.
- The pipeline's `push_to_backlog` stage also auto-schedules approved
  blueprints as they're produced.
- Together, a `scheduled_for=NULL, status=VISUAL_READY, action_taken=approved`
  row will get a fresh slot within ~1 hour typical (immediate on next
  pipeline run, or worst-case at 02:00 UTC next day).

If you're doing bulk DB surgery and want changes to STICK, you have <1 hour
before the sweeper undoes them.

---

## Publisher pickup gotchas

- Blueprints with `scheduled_for <= NOW()` but `status != VISUAL_READY` are
  ignored. This is a common source of "why didn't my scheduled blueprint
  publish" — status might be DRAFTED (render failed) or already PUBLISHED
  (idempotent skip).
- Blueprints with `visual_paths` pointing to non-existent files fail with
  `MISSING_RENDER` (2026-07-01 fix, PR after `ea8d4fa4`). Publisher writes
  a SKIPPED row to publishing_analytics; retries don't fire because
  `should_retry(MISSING_RENDER)` is False.
- Publisher fires at 06:30 UTC (12:00 IST) primary + 10:30 UTC (16:00 IST)
  retry window daily. Slots at 06:00 UTC also picked up in the primary run.

---

## Operator-safe cheatsheet

```sql
-- Cancel a broken blueprint durably (this is the safe pattern)
UPDATE blueprints
SET scheduled_for = NULL,
    status = 'ARCHIVED',
    action_taken = 'cancelled',
    extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
      'archived_at', NOW()::text,
      'archived_reason', '<short reason>',
      'archived_by', '<operator|script name>'
    )
WHERE id = ANY(ARRAY['<uuid1>', '<uuid2>']::uuid[]);

-- Reassign a broken slot to a fresh blueprint (this is the pattern
-- Round-2 used tonight)
UPDATE blueprints
SET scheduled_for = '2026-07-01 06:30:00+00'::timestamptz,
    action_taken = 'approved',
    reviewed_at = NOW(),
    extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
      'manually_scheduled_at', NOW()::text,
      'manually_scheduled_by', '<operator|script name>',
      'manually_scheduled_reason', '<short reason>'
    )
WHERE id = '<fresh-bp-uuid>' AND status = 'VISUAL_READY';

-- Verify slots for a date range
SELECT niche_id, scheduled_for, status, action_taken,
       LEFT(hook, 60) as hook_preview
FROM blueprints
WHERE scheduled_for >= DATE '2026-07-01'
  AND scheduled_for < DATE '2026-07-08'
ORDER BY scheduled_for, niche_id;

-- Verify visual files ACTUALLY exist for scheduled blueprints
-- (would have caught the disk-cleanup cascade 24h earlier)
SELECT b.niche_id, b.scheduled_for, b.hook,
       vp.path,
       (vp.path IS NOT NULL AND
        pg_stat_file(vp.path, missing_ok := true) IS NOT NULL) AS file_exists
FROM blueprints b
LEFT JOIN LATERAL (
  SELECT jsonb_array_elements_text(b.extra->'visual_paths') AS path
) vp ON true
WHERE b.scheduled_for >= NOW()
  AND b.scheduled_for < NOW() + INTERVAL '7 days';
```

---

## Related

- `genlab_core.scheduling.auto_approver` — the module owning the assign rule
- `genlab_core.scheduling.niche_pause_sweeper` — the daily 02:00 UTC sweep
- `genlab_core.monitoring.health_monitor.check_missing_media` — already
  detects the missing-file class + auto-archives unscheduled broken bps
  (respects the "scheduled posts are sacred" rule per `cleanup_safety.md`)
- `docs/ROADMAP-2026-07.md` — broader system roadmap
- Memory entry `[[disk-cleanup-cascade-2026-07-01]]` — the incident this
  doc synthesizes

## Version

- v1 (2026-07-01) — initial write after the disk-cleanup-cascade incident
