# Phase 2 — Config, schema, and defaults

**Audit run:** A (2026-07-29)
**Session:** 3 (Pre-Session-3 corrections + Phase 2)
**Status:** COMPLETE — 8 findings carried (A-0023…A-0030), plus 3 Phase 1 findings corrected
**Related artifacts:** `.audit/PHASE_1_drift_canonical.md`, `.audit/PHASE_2_uncommitted_prod_state.diff`, `.audit/DEFERRALS.md`, `.audit/snapshots/verify_policy_block_l1.sh` (redacted, gitignored)

**Prior-artifact posture (§0.8):** Two Phase-1 findings materially corrected this session:
- **A-0014** superseded by **A-0025**: I had wrong script (`backup_db.sh` writes to `.tmp/backups/` and is correct; the timer runs `pg_backup.sh` writing to `.backups/` with a 14-day retention that isn't firing)
- **A-0019** claim text corrected in place: hypothesis leak was 0, not 6; v1.0 had 0 `.mypy_cache` lines, not "excluded then dropped"
- A-0012 blast radius re-verified: `enabled: false` DOES gate — the ramp is inert (**S3 hygiene, not S1**), superseded by A-0023 for the deeper class-of-bug

---

## 0. Pre-Session-3 step outcomes

1. **Manifest sanity:** `.audit/manifest_{mac,vps}.txt` still held the v1.0 pair (32,225 / 29,479) — I forgot to copy the v1.1 pair over in Session 2. Fixed by `cp` from `.audit/exec/2026-07-29-phase1/manifest_{mac,vps}_hashed.txt`. Files gitignored per `.audit/.gitignore`. Recorded as execution error #5 for methodology ledger.
2. **Phase 1 artifact corrections applied:** A-0019 claim rewritten with verified counts (hypothesis=0, mypy_cache=18); PHASE_1_waste.md §2 dangling `(A-0021)` reference removed; PHASE_1_waste.md §13 A-0018 relabeled from `STATIC_ONLY (S3 cap)` to `Consolidate (verified-stale by dated filenames; no cap)`.
3. **A-0015 discriminator (RESOLVED-EARLY):** `backup_visual_assets.sh` line 121 has 14-day rolling prune. A-0015 downgrades to tolerated steady-state ~3.5 GB. See DEFERRALS.md.
4. **Snapshot:** `verify_policy_block_l1.sh` saved to `.audit/snapshots/`. **INCIDENT:** the script embedded `PGPASSWORD=genlab_***` on 3 lines — I introduced an S1 secret exposure into `.audit/` by snapshotting. Redacted immediately, added `snapshots/` to `.audit/.gitignore`, verified no commit touched it. The hardcoded password itself is a real prod finding → **A-0025**.
5. **DEFERRALS.md ledger created** with 37 entries seeded from Phase 0/1 deferrals + doc-delta items.

## 1. Config file enumeration

Per-member count (excluding `.venv`/`node_modules`/`.next`):

| Member | YAML | JSON | TOML |
|---|---:|---:|---:|
| genlab-core | 44 | 7 | 1 |
| dashboard | 1 | 9 | 2 |
| **BlackboxBrief** | 37 | **758** | 1 |
| CriticalRush | 32 | 46 | 1 |
| ClutchWire | 14 | 1 | 1 |
| SpliceReel | 14 | 1 | 1 |
| FrameDrift | 14 | 3 | 1 |

**BB's 758 JSON files is anomalous** — the three lean channels have 1-3 JSON each, and CR has 46. Deferred sub-investigation to A-0030.

## 2. INJECTED: writer-trace + loader-trace for A-0012 (headline of this phase)

### 2.1 Loader trace — auto_approver

From `genlab-core/src/genlab_core/scheduling/auto_approver.py` (verbatim):

```
558:        niche_root / "niches" / niche_id / "config" / "publishing.yaml",
559:        niche_root / "config" / "publishing.yaml",
```

Two paths tried in that order. For `gaming` niche (channel `CriticalRush`, niche_root `/opt/genlab/CriticalRush`), the first path resolves — **the auto-approver loads `CriticalRush/niches/gaming/config/publishing.yaml`, the OLD layout**. For every other niche (`sports`, `movies`, `anime`, `ai_creators`), the first path misses and it falls to `<niche_root>/config/publishing.yaml`. This is the "dual layout" a 6th channel would inherit → **A-0028**.

### 2.2 Gate authority — is `enabled: false` respected?

Verbatim journal output from `journalctl -u genlab-auto-approver --since '2026-07-29 16:00'`:

```
Jul 29 17:00:14 python[3266710]: [ai_creators] examined=2 approved=0 low_conf=2 rejected=0 idempotent=0 rollout_deferred=0 compliance_blocked=0 errors=0 dry_run=False disabled=False kill=False paused=False cap=False
Jul 29 17:00:14 python[3266710]: [gaming] examined=0 approved=0 low_conf=0 rejected=0 idempotent=0 rollout_deferred=0 compliance_blocked=0 errors=0 dry_run=False disabled=True kill=False paused=False cap=False
Jul 29 17:00:14 python[3266710]: [sports] examined=0 approved=0 low_conf=0 rejected=0 idempotent=0 rollout_deferred=0 compliance_blocked=0 errors=0 dry_run=False disabled=False kill=False paused=False cap=False
Jul 29 17:00:14 python[3266710]: [movies] examined=0 approved=0 low_conf=0 rejected=0 idempotent=0 rollout_deferred=0 compliance_blocked=0 errors=0 dry_run=False disabled=True kill=False paused=False cap=False
Jul 29 17:00:14 python[3266710]: [anime] examined=0 approved=0 low_conf=0 rejected=0 idempotent=0 rollout_deferred=0 compliance_blocked=0 errors=0 dry_run=False disabled=True kill=False paused=False cap=False
```

`disabled=True` for gaming/movies/anime — exactly the 3 niches with uncommitted `rollout_pct: 1.0`. `disabled=False` for ai_creators (which has `enabled: true`) and sports (which has `enabled: false` but disabled=False in the log — inconsistency worth Phase 4 look, but ai_creators is the one actively evaluating).

**Verdict:** `enabled: false` correctly gates the auto-approver. **The uncommitted `rollout_pct: 1.0` values are INERT while `enabled: false` remains.** A-0012's blast_radius corrects to S3 hygiene.

### 2.3 Writer trace — auto_ramp_auto2.py identified

Mtimes on the 3 modified files (verbatim `stat -c "%y %n"`):

```
2026-07-23 23:31:24.253815276 +0530  /opt/genlab/CriticalRush/niches/gaming/config/publishing.yaml
2026-07-23 23:31:27.905802818 +0530  /opt/genlab/SpliceReel/config/publishing.yaml
2026-07-23 23:31:30.702793276 +0530  /opt/genlab/FrameDrift/config/publishing.yaml
```

**6.4-second window** — unambiguously a machine writer, not an operator. Sibling `.lock` files show identical write timestamps ~30ms earlier (rule #20 flock discipline honored).

The writer:
```
Description=GenLab AUTO #2 Auto-Ramp — weekly +10% rollout_pct per qualifying niche
ExecStart=/opt/genlab/.venv/bin/python /opt/genlab/scripts/auto_ramp_auto2.py --apply
```

Grep for `rollout_pct` write sites confirmed:
```
/opt/genlab/scripts/auto_ramp_auto2.py:152:def write_rollout_pct(publishing_yaml: Path, doc: Any, new_rollout_pct: float) -> None:
```

**Class-of-bug confirmed:** `auto_ramp_auto2.py` is a **git-blind in-place YAML writer**. Same class as A-0020 (`cuelinks_campaigns.yaml` regenerated by `genlab-cuelinks-campaign-refresh.timer` weekly). Both are legitimate operational needs (weekly ramp, weekly campaign refresh) implemented against **git-tracked files**. Phase 9 merges A-0012 + A-0020 + A-0023 into one register entry: "git-blind in-place writers" class.

Nuance: `git diff` on VPS shows `rollout_pct: 0.0 → 1.0` — the endpoints. Intermediate steps in the ladder (0.1 → 0.25 → 0.5) were overwritten in place. Git sees only the current state, not the history. The **strategist auto-accept path** (`.runtime/auto_accept_strategist_proposals_last_error.txt`) has been failing with `KeyError: 0` since **2026-07-24 08:35** — 5 days — which means the human-review-gated ratchet (per CLAUDE.md AUTO #2 section: ≥30 samples + ≥90% agreement) is being bypassed by the ramp writer while the calibration path is broken.

### 2.4 Governance question surfaced

The ramp writes weekly regardless of `auto_publish.enabled`. So the moment operator flips `enabled: true` on gaming/movies/anime (expecting cautious 0.1 rollout per the ladder), rollout_pct is already 1.0 — instant 100% rollout, not the documented 4-week ramp. Footgun.

**A-0029** (default): `auto_approver.py:529` has `rollout_pct: float = 1.0` as the Python dataclass default. If YAML omits the key entirely, 100% rollout is the silent default. Same class of silent-wrong-default worry.

## 3. Missing-key safety (Phase 1 item 2 — sampled, not full 10)

Sampled test on the 3 uncommitted-drift publishing.yaml files: removing `auto_publish.rollout_pct` and loading via auto_approver's `AutoApprovalConfig.from_niche` code path (dataclass default kicks in at 1.0). **Silent-wrong-value confirmed for one key**: absent `rollout_pct` → 1.0 (not 0.0). A-0029.

Full 10-config missing-key sweep deferred → DEFERRALS.md → Phase 4 (empirical + journal grep for silent defaults). Marked PARTIAL.

## 4. Hardcoded values that belong in config

Not a full sweep; **specific real hits**:
- `pg_backup.sh:19` — DEFAULT DSN `postgresql://genlab:genlab_***@localhost:5432/genlab` is hardcoded as env fallback. Password is a live prod credential.
- `verify_policy_block_l1.sh` — 3× `PGPASSWORD=genlab_***` — no fallback, always hardcoded.
- `auto_approver.py:529` — `rollout_pct: float = 1.0` — default that should probably be 0.0 (see §2.4).

Deep sweep for hardcoded thresholds/URLs/model names deferred to Phase 8 (upgrade planning) per DEFERRALS.md.

## 5. Schema drift (Phase 1 item 4)

Not done this session. Requires Pydantic model enumeration + YAML-per-model cross-reference + DB column enumeration (which itself needs Phase 3 access). Rolled forward → DEFERRALS.md → Phase 7 (types coverage).

## 6. Cross-host config drift — CANONICAL 6 files

From `PHASE_1_drift_canonical.md`:

| File | Direction | Real change | Handled by |
|---|---|---|---|
| `.claude/scheduled_tasks.lock` | both | session lock — allow-listed | — |
| `CriticalRush/niches/gaming/config/publishing.yaml` | VPS-dirty | rollout_pct 0.0→1.0 | A-0012 (S3 inert) + A-0023 (writer) |
| `FrameDrift/config/publishing.yaml` | VPS-dirty | rollout_pct 0.0→1.0 + 3× null→empty | A-0012 + A-0023 |
| `SpliceReel/config/publishing.yaml` | VPS-dirty | rollout_pct 0.0→1.0 + 3× null→empty | A-0012 + A-0023 |
| `genlab-core/config/cuelinks_campaigns.yaml` | VPS regen | AUTO-GENERATED file rewritten weekly | A-0020 (class merge in Phase 9) |
| `genlab-core/config/affiliate_catalog.yaml` | bidirectional | Mac: 2 comment additions; VPS: 2 `affiliate_enabled: false` + FanCode entry | Investigation escalated below |

**`affiliate_catalog.yaml` bidirectional drift resolution:**
- Mac side: `git blame` shows the 2 comment additions are from the 2 commits Mac has ahead of VPS (part of the 13h deploy lag from A-0003). Deploying to VPS will pull those in.
- VPS side: the FanCode entry + `affiliate_enabled: false` flags look like operator edits (not the same 6-second-window signature as the auto_ramp). Author unknown. If similar to A-0012, the operator edited direct-on-VPS. Snapshot preserved in `PHASE_2_uncommitted_prod_state.diff`.
- Authoritative host is **VPS** for the operator flags (business decision) and **Mac** for the comment additions (developer commentary). Merge needs manual commit.

## 7. Duplicate / near-duplicate config (Phase 1 item 7 — inherited)

Sample: `SpliceReel/config/publishing.yaml` and `FrameDrift/config/publishing.yaml` are byte-similar in structure — same 3 sections (`platforms`, `platforms.threads`, `auto_publish`), same 3× `null → empty` diff signature after auto_ramp writes. **Not deep-scanned** — a proper duplication pass across all 32+37+14+14+14 = 111 channel YAML files is deferred to Phase 8 clone detection.

## 8. Dead YAML / JSON schemas (Phase 1 item 8 — inherited)

Sample: none obviously dead among sampled configs (visuals.yaml, publishing.yaml, sources.yaml are all loaded by real code paths verified via `grep`). BB's 758 JSON files pending sub-investigation → A-0030.

## 9. Multi-tenancy readiness

**Real blocker**: A-0028 (dual publishing.yaml layout). A 6th channel would face:
- Two conflicting templates to copy from (CR's `niches/<id>/config/` vs BB/SR/FD/CW's `<root>/config/`)
- The auto_approver would silently pick the first one that exists — so if the 6th channel writes to `niches/6thchan/config/publishing.yaml` AND `config/publishing.yaml`, only the first is read

Multi-tenant escalation: if 6th channel arrives with only `config/publishing.yaml` and something later writes to `niches/<id>/config/publishing.yaml`, the config silently changes.

## 10. D19 closure — whisper_sync.enabled across 5 niches

CLAUDE.md rule (D19, dated 2026-06-13): "whisper_sync.enabled = false across all 5 niches until the text_optimizer regression is fixed."

Verbatim:
```
BlackboxBrief/config/visuals.yaml:      enabled: true  # 2026-07-22 CANARY — ai_creators only
SpliceReel/config/visuals.yaml:         enabled: false
FrameDrift/config/visuals.yaml:         enabled: false
ClutchWire/config/visuals.yaml:         enabled: false
CriticalRush/niches/gaming/config/visuals.yaml:  enabled: false
```

**Status: DELTA** — 4/5 still `false` (D19 accurate for those); BB flipped to `true` on 2026-07-22 (CANARY per its comment). CLAUDE.md not updated to reflect the canary flip 7 days later — a small doc/reality drift → A-0031.

## 11. Findings carried (8 new: A-0023…A-0030 + A-0031)

Cap: 12. Carried: 9. Under cap.

| id | S | title |
|---|---|---|
| **A-0023** | S2 | Machine writer identified — `auto_ramp_auto2.py::write_rollout_pct` is a git-blind in-place YAML writer (class shared with A-0020) |
| **A-0024** | S1 | Two prod scripts have `KeyError: 0` on psycopg3 rows — same class as rule #19 fix but not covered (last errors 2026-07-23/24, actively broken) |
| **A-0025** | S1 | Hardcoded prod DB password `genlab_***` in `pg_backup.sh` + `verify_policy_block_l1.sh` — coordinate rotation with Phase 5 |
| **A-0026** | S2 | `pg_backup.sh` retention (`-mtime +14`) not firing — 20 files, 15 days retained. **Supersedes A-0014** with corrected script name |
| **A-0027** | S3 | A-0012 blast radius corrected: `enabled: false` gates rollout_pct per auto-approver journal — the ramp writes are INERT. Superseded by A-0023's class-of-bug scope |
| **A-0028** | S2 | Dual `publishing.yaml` layout (auto_approver.py:558-559) — multi-tenancy blocker; 6th channel gets 2 conflicting templates |
| **A-0029** | S2 | `auto_approver.py:529` defaults `rollout_pct: float = 1.0` — missing key = silent 100% rollout |
| **A-0030** | S3 | BlackboxBrief has 758 JSON files vs 1-3 in lean channels — investigation candidate |
| **A-0031** | S3 | D19 DELTA — whisper_sync flipped to `enabled: true` on BB (2026-07-22 canary); CLAUDE.md rule text stale |

### Not-carried (Noted)

- Deep dep sweep, dead config sweep, hardcoded values sweep — DEFERRED per DEFERRALS.md
- Positive: cross-member imports still 0/5 (confirmed by Phase 1 A-0022 informal note)
- `git blame` of `affiliate_catalog.yaml` Mac-side additions = expected 13h-lag delta (A-0003)

## 12. Deferrals added this session

Appended to `.audit/DEFERRALS.md`:
- Hardcoded prod DB password in shell scripts → Phase 5
- KeyError: 0 in 2 psycopg scripts → Phase 4 (silent-failure sweep)
- BB 758 JSON files inventory → Phase 1 retro or Phase 7
- pg_backup.sh 14-day retention prune not firing → Phase 4 (runtime why-not-firing)
- Dual-config-layout migration cost → Phase 8

## 13. Methodology issues encountered this phase (per §0.8)

1. **Introduced S1 secret exposure** by writing `verify_policy_block_l1.sh` (containing prod DB password) into `.audit/snapshots/`. Redacted within same session; `snapshots/` now gitignored. **Prevention:** any future snapshot must be piped through a redactor before landing in `.audit/`.
2. **Discovered I hadn't overwritten v1.0 manifests with v1.1 pair in Session 2** — files at `.audit/manifest_{mac,vps}.txt` were the wrong version. Fixed. Execution error #5 for ledger.
3. **A-0014 root-cause wrong** in Phase 1 (used the wrong script). Superseded by A-0026 with correct script (`pg_backup.sh`), correct retention (14 days, not 7).
4. **Continues the run's pattern** (A-0002→A-0012, A-0009→A-0014/15, A-0014→A-0026, A-0019 twice) — findings that assert-first-verify-later. §0.2 v1.2 verbatim-output rule directly targets this.
