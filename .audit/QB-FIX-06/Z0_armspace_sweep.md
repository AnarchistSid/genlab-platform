# QB-FIX-06 Z0 — Arm-Space Staleness Sweep

**Date:** 2026-08-07 14:55 IST (21h before tomorrow's 12:05 IST fire)
**Result:** 8 pre-fix rows archived. 10 non-sports queued rows remain, all post-fix by arm evidence or date corroboration. Y0's `is_blocking()` short-circuit verified holds under both `scheduled_for=NULL` and `scheduled_for=populated` shapes.

## Step 1 — full classification (26 queued rows)

**Post-F3a-2 arm set:** `[-3, -6, -9]`. **Pre-F3a-2 arm set:** `[-9, -12, -15]`. `-9` overlaps → date corroboration against F3a-2 landing time ~15:00 IST 2026-08-06.

| id | niche | title | status | created (IST) | appr | ducking | intro | classification |
|----|-------|-------|--------|---------------|------|---------|-------|----------------|
| ab84333e | ai_creators | ChatGPT Created Ticket | VR | 08-07 08:12 | ✓ | -9 | logo_tagline_reveal | **POST** (today) |
| 5bc17270 | ai_creators | programming in 2026 | VR | 08-06 22:52 | ✗ | -9 | logo_tagline_reveal | **POST** (post-V4 22:15) |
| 6119cc65 | ai_creators | Introducing Agent Plugins | VR | 08-06 22:52 | ✓ | -9 | logo_tagline_reveal | **POST** |
| **89a7d2fb** | **ai_creators** | **Billion Dollar AI Race** | VR | 08-06 08:04 | ✓ | -9 | pattern_break_intro | **PRE** (before F3a-2) |
| **8ce02a08** | **ai_creators** | **Meet Birding Pal** | VR | 08-05 08:09 | ✓ | -9 | logo_tagline_reveal | **PRE** (⚠️ RESURRECTED from X0-a archive) |
| 6549fe64 | anime | Chainsmoker Cat | VR | 08-07 11:42 | ✗ | -6 | pattern_break_intro | **POST** |
| 9ebb8d55 | anime | Exiled Heavy Knight | VR | 08-07 11:42 | ✗ | -9 | logo_tagline_reveal | **POST** |
| b8de02a6 | anime | Master Swordsman II | VR | 08-06 20:08 | ✓ | -6 | logo_zoom | **POST** (F4 batch 2) |
| e1ada462 | anime | Dating Sim | DRAFTED | 08-06 20:08 | ✗ | — | — | **POST** (by date) |
| 6273e9cc | gaming | MARVEL TŌKON | VR | 08-07 09:40 | ✗ | -9 | pattern_break_intro | **POST** |
| **3ad79cf7** | **gaming** | **League of Legends** | VR | 08-06 09:39 | ✓ | **-15** | pattern_break_intro | **PRE** (unambiguous) |
| **85866097** | **gaming** | **Escape from Tarkov** | DRAFTED | 08-06 09:39 | ✗ | — | — | **PRE** (by date, sibling to LoL) |
| b2292ede | movies | Yankee | VR | 08-06 19:17 | ✗ | -9 | logo_tagline_reveal | **POST** (after F3d-1 18:30) |
| c25972a9 | movies | Primetime | VR | 08-06 19:17 | ✓ | -9 | pattern_break_intro | **POST** (F4 batch 2) |
| **e9db5302** | **movies** | **Honest Trailers** | VR | 08-06 09:14 | ✗ | -9 | logo_tagline_reveal | **PRE** (by date) |
| **a99747f9** | **movies** | **INSIDIOUS** | VR | 08-06 09:14 | ✗ | **-15** | logo_zoom | **PRE** (unambiguous) |
| **9ebab11b** | **movies** | **RAMAYANA** | VR | 08-06 09:14 | ✗ | **-15** | logo_tagline_reveal | **PRE** (unambiguous) |
| **699a1a3b** | **movies** | **Violent Night 2** | VR | 08-06 09:14 | ✗ | **-15** | pattern_break_intro | **PRE** (unambiguous) |
| ff3142e0 | sports | Ronald Acuña | VR | 08-07 10:50 | ✓ | -9 | logo_zoom | POST |
| 2b1f501c | sports | Garry vs Prates | VR | 08-07 10:50 | ✓ | -9 | pattern_break_intro | POST |
| aed26edf | sports | Jan Blachowicz | VR | 08-07 10:50 | ✓ | -9 | pattern_break_intro | POST |
| 9d48f88a | sports | Jordantaylor | VR | 08-07 10:50 | ✓ | -9 | logo_zoom | POST |
| 198597a1 | sports | PETE CROW-ARMSTRONG | DRAFTED | 08-06 23:25 | ✗ | — | — | POST (by date; Y2 run output) |
| cae23d7f | sports | onboard fastest lap | DRAFTED | 08-04 10:31 | ✗ | — | — | PRE — **HELD** for Z1 |
| 9c9f0808 | sports | Death of a Gentleman | DRAFTED | 08-03 10:32 | ✗ | — | — | PRE — **HELD** for Z1 |
| 21febc65 | sports | Hamilton penguins | DRAFTED | 08-02 10:31 | ✗ | — | — | PRE — **HELD** for Z1 |

### Per-niche summary

| niche | queued | pre-fix | post-fix | ambiguous | approved pre-fix (danger) |
|-------|--------|---------|----------|-----------|--------------------------|
| ai_creators | 5 | 2 | 3 | 0 | **2** (`89a7d2fb`, `8ce02a08`) |
| anime | 4 | 0 | 4 | 0 | 0 |
| gaming | 3 | 2 | 1 | 0 | 1 (`3ad79cf7`) |
| movies | 6 | 4 | 2 | 0 | 0 |
| sports | 8 | 3 (held) | 5 | 0 | 0 |

## Step 2 — `6119cc65` Agent Plugins verification

- `created_at`: **2026-08-06 22:52 IST** — post-V4 (22:15 IST), post-everything
- `ducking`: `-9` (ambiguous by arm alone; decisive by timestamp)
- `intro`: `logo_tagline_reveal` picked by bandit; force_none overrides at compositor (per F3d-1 design — bandit-pick is recorded but not applied)
- **POST-FIX confirmed.** Publishes tomorrow if publisher picks it (competing with `ab84333e` ChatGPT Created Ticket which is also POST-FIX and approved — either is safe).

## Step 3 — archive execution

```sql
UPDATE blueprints
SET status = 'ARCHIVED',
    action_taken_source = 'auto_archived_qb_fix_06_armspace',
    scheduled_for = NULL       -- clear rule-2 phantom-block belt (Y0 makes redundant, but safe)
WHERE id IN (
  '89a7d2fb-9e92-4442-a077-090a38ed21e3',  -- ai_creators Billion Dollar AI Race (Aug 6 08:04, -9 pre)
  '8ce02a08-e333-4a27-b1e9-b4735da9cb4f',  -- ai_creators Meet Birding Pal (RESURRECTED from X0-a)
  '3ad79cf7-7b82-4f26-93f1-ad8efeff4fa0',  -- gaming League of Legends (Aug 6 09:39, -15 pre)
  '85866097-06f7-429f-8b42-bd7bcceb1968',  -- gaming Escape from Tarkov DRAFTED
  'a99747f9-4225-411b-a3fa-0ee6322e95fe',  -- movies INSIDIOUS -15
  '9ebab11b-b069-4916-ad5b-be4c7fce307d',  -- movies RAMAYANA -15
  '699a1a3b-bf80-4652-8280-320cf15dc742',  -- movies Violent Night 2 -15
  'e9db5302-4267-49a9-a25c-ffe231197e2c'   -- movies Honest Trailers Aug 6 09:14
);
-- UPDATE 8
```

Sports 3 DRAFTED (Aug 2-4) explicitly held for Z1.

## Step 4 — Y0 fix verification

Runtime probe on VPS post-archive:

```
is_blocking(archived+approved+scheduled=NULL)       = False
is_blocking(archived+approved+scheduled=populated)  = False
```

Both shapes return False. My archive nulls `scheduled_for` as belt-and-suspenders even though Y0's ARCHIVED short-circuit makes it redundant. Passthrough measurement will come naturally on the next pipeline run.

## Resurrection defect (new finding)

**`8ce02a08 Meet Birding Pal` was resurrected from X0-a's archive at 23:00 IST last night** — 25 min after my X0-a archive at 22:35 IST. The `action_taken_source` tag was overwritten from `auto_archived_qb_fix_04_pre_fix` → `auto_approver_v1`.

Timeline reconstruction:
- 22:35 IST — X0-a UPDATE archived 35 rows including 8ce02a08 with tag `auto_archived_qb_fix_04_pre_fix`
- 22:53 IST — ai_creators pipeline (X0-b) ran fresh
- 23:00 IST — 8ce02a08 unarchived; tag reset to `auto_approver_v1`

Hypothesis: `PushToBacklog._maybe_revive_archived_row` (or similar revive path in the writer/push chain) resurrected 8ce02a08 when the pipeline re-fetched a candidate that hashed to the same story_id/candidate_id. Y0's ARCHIVED short-circuit prevents the row from BLOCKING new fetches, but does NOT prevent the revive path from resetting an archived row's status back to VISUAL_READY.

**Broader concern:** every future archive competes with the revive path. If any archived candidate is re-fetched, it can resurrect. Q0's tag-in-source-column strategy (`auto_archived_qb_fix_04_pre_fix` / `auto_archived_qb_fix_06_armspace`) provides forensic breadcrumbs but no defense.

**Filed as follow-up (not fixing in Z0):** the revive path needs a guard against terminal-state rows. Either (a) revive should refuse ARCHIVED rows and instead insert a new blueprint (blueprint_id will differ), or (b) revive should preserve the archive tag and log a warning, or (c) revive should check whether the archive was recent (< N hours) and skip revival in that case.

## What publishes tomorrow (per niche)

- **ai_creators:** either `6119cc65 Agent Plugins` or `ab84333e ChatGPT Created Ticket` — both POST-FIX, either is safe
- **anime:** `b8de02a6 Master Swordsman II` (F4 batch 2 approved, POST-FIX)
- **gaming:** nothing approved (LoL archived; MARVEL TŌKON unapproved; Y1 (c) defect still blocks even if approved at 15:30 IST)
- **movies:** `c25972a9 Primetime | A24` (F4 batch 2 approved, POST-FIX)
- **sports:** likely one of 4 approved rows (all POST-FIX from today's Y2 pipeline output)

Movies + anime F4 batch 2 fires as scheduled. ai_creators fires clean. Sports fires clean. Gaming stalled (Y1 unresolved).

## Commit

`chore(backlog): archive pre-fix blueprints by arm-space provenance`
