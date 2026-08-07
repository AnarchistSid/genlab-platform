# QB-FIX-08 B2 — `_is_terminally_archived()` Fragility Note

**Date:** 2026-08-07 15:50 IST
**Purpose:** record only. Not fixing.

## Observation

QB-FIX-07 A1's `_is_terminally_archived()` guard is a string match:

```python
_TERMINAL_ARCHIVE_TAG_PREFIX = "auto_archived_qb_fix"

def _is_terminally_archived(row: dict) -> bool:
    fields = row.get("fields", row)
    if fields.get("status") != "ARCHIVED":
        return False
    src = fields.get("action_taken_source") or ""
    return src.startswith(_TERMINAL_ARCHIVE_TAG_PREFIX)
```

**Failure mode:** any code path that rewrites `action_taken_source` on an ARCHIVED row silently drops the protection. The revive path checks source_tag first; if the tag was overwritten to `auto_approver_v1` or similar, the guard returns False and the row is revivable again.

**Not a live defect today.** A1's third-path audit confirmed no other read code treats ARCHIVED as non-terminal, and no known write path deliberately overwrites `action_taken_source` on ARCHIVED rows. The one observed resurrection (8ce02a08) went via the revive path itself — which A1 now blocks — not via source-tag overwrite.

**But it's fragile.** A future maintainer editing an unrelated code path (e.g., dashboard "revert archive" button, batch cleanup script, migration backfill) could reset `action_taken_source` without knowing about the terminal-archive semantics.

## Hardening candidate

A boolean column or explicit enum field would not have this failure mode. Options:

- **`archived_terminal BOOLEAN NOT NULL DEFAULT FALSE`** — explicit sticky flag. Set true when the sweep archives. Migration required.
- **`archive_reason TEXT`** (separate from `action_taken_source`) — dedicated column for "why archived." Migration required.
- **Reserve `action_taken_source` values in an enum/constraint** — Postgres CHECK constraint that only certain values may set the row terminal. Overkill for this defect.

## Recommendation

Do not migrate for this defect alone. When the next `blueprints` migration lands (schema addition, index change, etc.), bundle an `archived_terminal BOOLEAN` column and update `_is_terminally_archived()` to prefer the column over the string match. Backfill existing `auto_archived_qb_fix_*` tagged rows with `archived_terminal=true`.

Until then: any code path that touches `action_taken_source` on an ARCHIVED row should be reviewed for whether it preserves the sweep tag.

## Class-of-bug link

Related to ME-11 (aggregates hide composition defects) at a different layer: here the aggregate is "protection state," and the composition (status + source_tag) doesn't have a first-class representation. Refactoring toward first-class state is the general fix.

## No commit necessary

Record in this file. No code change.
