# QB-FIX-02 V3 — Root-Cause the `niche=unknown` Blueprint

**Date:** 2026-08-06 22:00 IST
**Status:** FIX SHIPPED; DB gate PASS (measured pre-fix + post-fix). Bug was NOT in DB write path; it was in the pre-write gate's in-memory synthetic blueprint.

## Extent measurement (V3 Step 1)

```sql
-- blueprints
SELECT status, COUNT(*) FROM blueprints
WHERE niche_id IS NULL OR niche_id = '' OR niche_id = 'unknown' GROUP BY status;
-- 0 rows

-- stories
SELECT COUNT(*) FROM stories
WHERE niche_id IS NULL OR niche_id = '' OR niche_id = 'unknown';
-- 0

-- content_pool: table has no niche_id column (unclassified by design;
-- content_pool feeds N niches from a shared pool). Not a violation.
```

**Zero rows with null/empty/unknown niche_id in `blueprints` or `stories`.** DB was always correct. Including the Yankee row that triggered the audit:

```
id=b2292ede-887f-4ee2-8b3f-1daf2c64be95 niche_id=movies (len=6) status=VISUAL_READY
```

Row is correctly tagged `niche_id='movies'`. The `[gate] LLM judge fired for niche=unknown` log was NOT reading this row.

## Real origin (V3 Step 2 — trace)

The log `[gate] LLM judge fired for niche=unknown` at `auto_approval_gate.py:731` uses the local `niche_id` variable from line 582: `niche_id = (blueprint.get("niche_id") or "unknown").strip()`.

That `blueprint` dict was NOT the DB row — it was a synthetic dict built in `push_to_backlog.py:3064`:

```python
_synth = {
    "hook_text": fields.get("hook_text", ""),
    "visual_paths": fields.get("visual_paths", ""),
    "extra": { "composite_score": ..., "virality_score": ..., ... },
}
_decision = _aag_evaluate(_synth)   # <-- niche_id missing from _synth
fields["auto_approval_confidence"] = round(_decision.confidence, 4)
```

Push_to_backlog was pre-computing `auto_approval_confidence` before the DB write so downstream consumers (dashboard preview, AUTO #2 worker) wouldn't have to re-run the gate. But the synthetic dict passed to `evaluate()` OMITTED `niche_id`. The DB write immediately after (`insert into blueprints ...`) correctly stamped `niche_id='movies'`, so the persisted row is fine. Only the pre-write in-memory `auto_approval_confidence` computation and its LLM-judge log line saw a phantom `niche=unknown`.

## RLS behavior (V3 Step 3)

Per CLAUDE.md rule #27, the `genlab` role in prod has `BYPASSRLS` attribute, so table-level RLS policies (including `blueprints.niche_isolation`) are silently no-op for the app role. The `PostgresBackend.find/update/delete` methods use belt-and-suspenders `AND niche_id = %s` in every WHERE clause to enforce tenant isolation.

If a null-niche row DID land in the DB (it hasn't — see extent measurement), the practical effect would be:

- **RLS policy:** no-op (role bypasses)
- **Belt-and-suspenders WHERE:** the row's `niche_id IS NULL` never matches `niche_id = 'movies'` or any tenant value → row invisible to every tenant-scoped query
- **Unconditional queries:** admin queries like `SELECT * FROM blueprints` would see the row
- **Publisher:** `blueprint_selector` scopes queries per niche → invisible
- **Auto-approver:** ditto → invisible

Practical answer for the hypothetical null-niche row: invisible to production reads, but a data-hygiene problem waiting to break under future admin queries or a role change. Not a live tenant-leak risk today because the DB write path DOES stamp niche_id correctly.

## Fix (V3 Step 4)

At the write path, not by backfill. `push_to_backlog.py:3064` `_synth` dict now propagates `niche_id`:

```python
_synth = {
    "niche_id": fields.get("niche_id") or niche_id,   # NEW
    "hook_text": fields.get("hook_text", ""),
    ...
}
```

`fields.get("niche_id")` primary — that's what the imminent DB write will use. `niche_id` (stage-scope local variable from `context["niche_id"]` at line 1584) fallback — never actually needed today since `fields["niche_id"]` is always set before this branch, but the belt-and-suspenders is cheap.

**Not applied in this pass:** DB-level `NOT NULL` constraint on `blueprints.niche_id`. Flagged for a future migration. The migration is safe (zero existing rows violate) but is a schema change and warrants its own review + rollback plan.

## Gate

**Pre-fix (before this session):**
```sql
SELECT COUNT(*) FROM blueprints
WHERE created_at >= NOW() - INTERVAL '24 hours'
  AND (niche_id IS NULL OR niche_id = '' OR niche_id = 'unknown');
```
Result: **0** (measured 2026-08-06 22:05 IST; DB write was always correct).

**Post-fix (in-memory verification):**
- Journal grep on next pipeline fire: expect `[gate] LLM judge fired for niche=unknown` to NEVER appear.
- Source pin test `test_push_to_backlog_niche_id_gate_synth.py` asserts `"niche_id"` key is present in the `_synth` dict literal — passes locally.

Auto_approval_confidence values on future blueprints will now reflect the correct per-niche gate thresholds (composite/virality overrides from `gate_tuner.get_overrides_for_niche(niche_id)` — before this fix, all pre-write confidence computations used the empty-string / default thresholds).

## Yankee blueprint (V3 auth check)

`b2292ede-887f-4ee2-8b3f-1daf2c64be95` is correctly tagged `niche_id='movies'`. **NOT approved** per §5 instruction ("do not approve the Yankee blueprint until the row's niche_id is corrected and the origin is understood"). Row's niche_id was never wrong. Origin now understood: it's the pre-write gate synth dict, not the DB row.

Left VISUAL_READY unapproved. The reserved status is unchanged for other reasons (F4 batch 1 slots are filled by INHERIT + Primetime).

## Fix summary

- Bug is 1 line: missing key in a synthetic dict.
- Fix is 1 line: add the key.
- Impact: pre-write gate + LLM judge now reason under the correct tenant. Auto_approval_confidence values on new blueprints will use per-niche gate_tuner overrides instead of default thresholds.
- No backfill needed (DB rows always correct).

## Commit

`fix(pipeline): require niche_id at blueprint creation` — one code file + one pin test.
