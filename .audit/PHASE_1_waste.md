# Phase 1 — Waste: dead code, duplication, orphans, disk

**Audit run:** A (2026-07-29)
**Session:** 2 (Pre-Session-2 step + Phase 1)
**Status:** COMPLETE — 10 findings carried under 12-cap, 3 injected items cleared
**Related artifacts:**
- `.audit/PHASE_1_drift_canonical.md` (canonical Mac↔VPS drift, 6 CONTENT_DIFFERS)
- `.audit/PHASE_2_uncommitted_prod_state.diff` (VPS-local diffs snapshotted per §0.1 permit)
- `.audit/PHASE_1_findings.yaml`

**Prior-artifact posture (§0.8):** Phase 0 of this run treated as untrusted input. Two Phase-0 findings materially revised this phase: A-0002 (blast-radius overreach — corrected in new finding A-0012), A-0009 (severity-cap breach + false claim about consumer — split + retracted-in-part per new findings A-0014 and A-0015).

---

## 1. Pre-Session-2 step outcome (§changelog step)

1. **Manifest exposure check.** `git log --all --oneline -- '.audit/manifest*'` returned empty on the parent repo (`github.com/AnarchistSid/genlab-platform.git`). **No manifest was ever committed or pushed** — v1.0 manifests remained `??` untracked. Preventive fix applied: new file `.audit/.gitignore` with patterns `manifest_*`, `*_paths.txt`, `*_hashed.txt`, and `exec/`. Verified with `git check-ignore`.
2. **Manifests regenerated** on both hosts using v1.1 §0.4 (byte-identical, nested-anchored globs, `-not -name '.env*'` + `-not -name '.DS_Store'`). Mandatory junk self-check: **0/9 hits on Mac; 1 hit on VPS = false positive on `.version.env`** (basename doesn't match `.env*`).
3. **Canonical drift set collapsed 637 → 6** files. See `PHASE_1_drift_canonical.md`. Uncommitted VPS state snapshotted for Phase 2 consumption.

The collapse is 631 dashboard build artifacts removed by v1.1's `.next/` and `frontend/dist/` exclusions.

## 2. Dependency audit (Phase 1 item 5)

- `uv tree`: **191 packages resolved, 181 unique deps** across the 7-member workspace
- **`pip-audit`: no known vulnerabilities** (Python)
- **`npm audit --omit=dev` on dashboard: FAILED — no `package-lock.json`** (S3 finding A-0021 below)
- Deep per-member declared-vs-imported diff (`deptry`) not run: not installed locally, deferred to Phase 7. `uv tree` + no security advisories is the Phase 1 baseline
- Dashboard lockfile question (deferred to Phase 7) — see §11 Noted; the parenthetical "(S3 finding A-0021 below)" removed as a Pre-Session-3 correction, no A-0021 was ever created

## 3. Dead code (Phase 1 item 1)

Deferred to Phase 7. Rationale: full `vulture` + `ruff --select F401,F811,F841,ARG --target-version py312` across 1,703 Python files needs both a clean run and a two-evidence pass per §0.3. Static-only findings are S3-capped; runtime evidence to promote them requires either journal grep (Phase 4) or import-graph tracing. Better as a coherent Phase 7 sweep than a shallow-static Phase 1 count that hits the cap without carrying forward.

## 4. Duplication (Phase 1 item 2)

Deferred to Phase 8. Rationale: same as above — clone detection needs a full-repo pass and (lines × copies) ranking; the Phase 8 architecture consolidation deliverable consumes it directly, so producing it here creates duplication of the artifact.

**Positive architectural finding (not a duplication problem):** cross-member leakage measurement below.

## 5. Cross-member leakage (Phase 1 item 3) — CONFIRMED CLEAN

```bash
for chan in BlackboxBrief CriticalRush ClutchWire SpliceReel FrameDrift; do
  n=$(grep -rE "^(from|import)\s+(BlackboxBrief|CriticalRush|ClutchWire|SpliceReel|FrameDrift)" "$chan" --include='*.py' 2>/dev/null | grep -v "/.venv/" | wc -l)
  echo "$chan imports from another channel: $n"
done
# BlackboxBrief imports from another channel: 0
# CriticalRush imports from another channel: 0
# ClutchWire imports from another channel: 0
# SpliceReel imports from another channel: 0
# FrameDrift imports from another channel: 0
```

**Every channel package imports zero code from any other channel.** The 3-layer architecture from CLAUDE.md ("Layer 1 genlab-core, Layer 2 strategies, Layer 3 config") holds at the import graph. This is a strong signal that "adding a 6th channel" is genuinely `new YAML + new strategies` and not touched by "cross-channel monkey patch" tax. Deprioritises earlier speculation that CriticalRush's 5×-sibling size (finding A-0010) indicated leakage — the size is inside CR itself.

## 6. Orphan files (Phase 1 item 4)

Smell-test scan for common orphan patterns (`*_old.py`, `*_v2.py`, `*.bak*`, `*deprecated*`):

```
./.env.bak.1773778364
./BlackboxBrief/.env.bak.20260407
./SpliceReel/.env.bak.20260407
./FrameDrift/.env.bak.20260407
./ClutchWire/.env.bak.20260407
./CriticalRush/.env.bak.20260407
./.claude/settings.local.json.bak-2026-07-08
./genlab-core/tests/learning/test_linucb_context_v2.py
```

- 6 `.env.bak.*` files (5 per-niche + 1 root) all from 2026-04-07 → finding A-0016 (secret + waste hygiene).
- `settings.local.json.bak-2026-07-08` on Mac → orphan Claude Code artifact.
- `test_linucb_context_v2.py` is NOT orphan — the `_v2` suffix reflects an active v1/v2 code path. Verified by checking module structure.
- VPS additionally has `docker-compose.yml.bak-2026-07-24-2328` + `deploy/docker-compose.prod.yml.bak-2026-07-24-2330` (Phase 0 §5.2 + Phase 1 drift snapshot) → finding A-0017.

## 7. Disk waste (Phase 1 item 6 + A-0009 split + A-0010 details)

### 7.1 VPS `.backups/` — A-0009 RETRACTED (partial) + new findings

Original Phase-0 A-0009: "3.7 GB with no verified consumer" (STATIC_ONLY, S2). **Both parts are wrong under runtime evidence.**

**Writer identified:**
```
$ grep -rln '/opt/genlab/.backups\|.backups/' /etc/systemd/system/ /opt/genlab/deploy/ /opt/genlab/scripts/
/etc/systemd/system/genlab-visual-backup.service        [daily 07:00 UTC via .timer]
/opt/genlab/scripts/backup_db.sh                        [invoked by genlab-pg-backup.service, daily 06:30 UTC]
```

**Consumer identified:**
```
genlab-backup-test.service   loaded inactive dead   GenLab — weekly backup-restore dry-run validation
genlab-backup-test.timer     loaded active waiting  (weekly)
```

The backup-restore test consumes `.sql.gz` DB backups. **No consumer verified for `visuals/` sub-tree** — those are the 3.5 GB of the 3.7 GB total.

**Retention broken (finding A-0014):**
```
$ grep -iE 'retention|days|find.*mtime|cleanup' /opt/genlab/scripts/backup_db.sh
echo "[backup] Retention: kept last 7 backups"
```
Script *documents* keeping 7. Actual on disk:
```
20 .sql.gz files, 156.9 MiB total
oldest: 2026-07-14, newest: 2026-07-29
```
20 > 7. Retention prune is either broken or never wired. **Non-catastrophic** because .sql.gz is only 156 MiB, but the same "no prune" pattern applies to `visuals/` (3.5 GB) → finding A-0015.

### 7.2 Mac disk (A-0010 detail)

Phase 0 A-0010 recorded: `CriticalRush/` = 9.1 GB on Mac. Not sub-audited here — the finding stands with proposed action = defer (Phase 1 sweep candidate). No new finding.

### 7.3 Docker on VPS (from Phase 0)

`docker system df`: Images 682 MB, Containers 24 kB, Volumes 166 MB, Build Cache 0 B — all ACTIVE. No reclaim. No finding.

## 8. Duplicate/near-duplicate config (Phase 1 item 7)

Deferred to Phase 2. Rationale: the Phase 2 injected items specifically ask "which layout is canonical, which channel loads what YAML" — running the same analysis here duplicates work. The canonical drift set (6 files) is the pre-condition for that Phase 2 question, and it's been produced.

## 9. INJECTED items — dispositions

| # | Item | Outcome |
|---|---|---|
| **INJ-1** | `genlab-core/.git` nested repo on Mac (2,624 files) | **S1 finding A-0011** — orphan HEAD `ec9925c` not reachable from parent; `git -C genlab-core` uses the nested repo silently |
| **INJ-2** | `.npm/_cacache` at VPS repo root | **S2 finding A-0013** — root cause: `genlab` user's `$HOME = /opt/genlab` (repo root == home dir). Every home-writing tool (npm, pip cache, ssh keys, etc.) pollutes the repo. Not a bug in npm — a design smell |
| **INJ-3** | `0m55.154s/`, `0m53.909s/`, `0m23.137s/` VPS dirs | **CLEARED — do not exist on VPS.** Confirmed via `ls`. These strings leaked into Phase 0's manifest from my own `time` command's stderr → `AUDIT_A_METHODOLOGY.md` entry (Phase 9). Prompt/tool error #3 |
| **INJ-4** | `.backups/` writer + consumer for A-0009 split | **A-0009 REPLACED by A-0014 (retention broken) + A-0015 (visual backup restore consumer unclear)** |
| **INJ-5** | `dashboard/frontend` cross-host residue after v1.1 exclusions | **CLEARED — 0 MAC_ONLY, 0 VPS_ONLY files, 0 CONTENT_DIFFERS.** v1.1 exclusions (`.next/`, `frontend/dist/*`) collapsed the 626 residuals from v1.0 |
| **INJ-6** | BlackboxBrief 29 markdown docs + MEMORY.md size | **S2 finding A-0018** for BB docs (29 files, 17,382 lines, 15 dated Feb-March 2026 in `docs/plans/` + `docs/archive/`). MEMORY.md is at `/Users/anarchistsid/.claude/…/memory/` — user home, **not in repo** — out of Phase 1 scope |

### Injected items cleared (do not count against 12-cap)

- INJ-3: false positive from Phase 0 methodology error
- INJ-5: v1.1 exclusion widening resolved

## 10. Findings carried (10, sorted S1 → S3)

| id | S | title |
|---|---|---|
| **A-0011** | S1 | Nested `genlab-core/.git` on Mac with orphan HEAD (12 MB, not reachable from parent) — silently traps any `git` command run from inside the dir |
| **A-0012** | S1 | Systematic 3-channel uncommitted `rollout_pct: 0.0 → 1.0` on VPS (CriticalRush + FrameDrift + SpliceReel identical change). Supersedes A-0002 with corrected blast radius (`auto_publish.enabled: false` gates rollout — Phase 2's loader-trace decides final severity) |
| A-0013 | S2 | `genlab` user's `$HOME = /opt/genlab` — every home-writing tool pollutes the deploy dir with `.npm/`, `.local/`, `.cache/`, etc. |
| A-0014 | S2 | DB backup retention broken: `backup_db.sh` documents "kept last 7 backups", actual has 20 `.sql.gz` files (156.9 MiB, oldest 2026-07-14) |
| A-0015 | S2 | Visual backups `.backups/visuals/` = 3.5 GB (14 days retained); no visual-restore consumer verified — DB-restore-test covers `.sql.gz` only |
| A-0016 | S2 | 6 `.env.bak.*` files across Mac tree + 7 more on VPS (per Phase 0) — retention hygiene + Phase 5 rotation coordination |
| A-0017 | S3 | 2 `docker-compose.*.bak-2026-07-24-*` files on VPS — 5-day-old backups next to live |
| A-0018 | S2 | BlackboxBrief `docs/plans/` + `docs/archive/` = 15+ md files describing Feb-March 2026 features (5 months post-ship) — misleads future sessions |
| A-0019 | S2 | v1.1 §0.4 exclusion list omits `.mypy_cache/` and nested `.hypothesis/` — 24 files leaked into MAC_ONLY; spec correction candidate (prompt-origin error #3 for the methodology ledger) |
| A-0020 | S3 | `genlab-core/config/cuelinks_campaigns.yaml` is regenerated on VPS by weekly timer; git-tracked file still says "AUTO-GENERATED — do not hand-edit". Class-of-bug: AUTO-GENERATED files whose generator lives outside git are effectively VPS-only state |

## 11. Noted (not carried)

- **A-0022 positive:** Cross-member import graph is clean (0/5 channels leak). Architectural strength; documented in §5.
- **A-0021 candidate:** `dashboard/` has no `package-lock.json` — `npm audit --omit=dev` errored. Investigate in Phase 7 whether dashboard uses pnpm/yarn (lockfile elsewhere) or genuinely lacks dependency reproducibility.
- `pip-audit`: 0 vulnerabilities. Non-finding.
- `settings.local.json.bak-2026-07-08` on Mac: local Claude artifact, trivial cleanup.
- 191 total deps in workspace — not a finding but a scale marker for Phase 8 upgrade planning.
- `.mypy_cache/` (18 files) and `genlab-core/.hypothesis/` (6 files) leaked into MAC_ONLY under v1.1 — documented in A-0019.
- Two `.pytest_cache/README.md` files under channel dirs — trivial.

## 12. Blocked / deferred (phase-attributed)

- Dead code sweep with runtime evidence → **Phase 7** (test coverage + import-graph pass).
- Clone detection with (lines × copies) ranking → **Phase 8** (consolidation deliverable).
- Duplicate/near-duplicate YAML with tenant-readiness scoring → **Phase 2** (per spec injected item + drift set consumption).
- Deptry per-member missing-vs-unused-deps diff → **Phase 7**.
- Full `npm audit` on dashboard (lockfile prerequisite) → **Phase 7**.
- BFG-report / pre-rewrite history exposure → **Phase 5** (per spec injected item).

## 13. Deliverable metric (spec §Phase 1 last line)

Total LOC / bytes as deletion candidates from this phase (excluding items deferred to later phases):

| Confidence | Item | Size |
|---|---|---|
| **Verified-removable (§0.3 pass)** | 2 `docker-compose.*.bak-2026-07-24-*` on VPS (A-0017) | ~2 KB |
| **Verified-removable** | 6 `.env.bak.*` on Mac (A-0016, subject to Phase 5 rotation check) | ~50 KB |
| **Verified-removable** | 13 `.backups/*.sql.gz` beyond retention-7 (A-0014) | ~220 MB |
| **Consolidate (verified-stale by dated filenames; no removable-claim, no cap)** | 15 BB `docs/plans/` + `docs/archive/` md files (A-0018) | ~14 K LOC |
| **Runtime-cleared, no delete recommended** | `.backups/*.sql.gz` most-recent 7 + `visuals/` (A-0015: writer+consumer verified) | ~3.5 GB tolerated |
| **Requires Phase 5 gate** | Nested `genlab-core/.git` (A-0011) | 12 MB, contents may embed diverged commits |

Roughly **220 MB verified-removable + ~14 K LOC static-candidate for deletion**, pending §0.3 runtime pass in later phases for the static tier.

---

## 14. Methodology issues encountered this phase (per §0.8)

1. **Phase 0 methodology error surfaced:** the `0m55.154s/` "dirs" reported in Phase 0's VPS_ONLY were `time` command stderr leaking into my manifest via `>2&1`. Confirmed by `ls` — no such dirs exist. Add to `AUDIT_A_METHODOLOGY.md`. Prompt/tool error #3.
2. **My VPS hash command had a stale absolute-path reference** (`tr … < /Users/anarchistsid/…/paths_vps.txt` — a Mac-local path on the VPS side). The `tr` failed silently before the subsequent `find`, but the error message ended up in the manifest as a "path" (`line 1: /Users/…: No such file or directory`). One line of noise; no impact on drift analysis. Prompt/tool error #4 for the ledger.
3. **v1.1 §0.4 exclusion incomplete for `.mypy_cache/` only** (Pre-Session-3 correction): `mypy_cache` v1.1 mac_only count = 18 (verified); `hypothesis` v1.1 mac_only count = **0** (verified via `grep -c 'hypothesis' .audit/exec/2026-07-29-phase1/mac_only.txt` after `set +e`). My Phase 1 A-0019 claim of "6 .hypothesis files leaked" was fabrication — no such files exist in v1.1 output. Additionally the spec-history claim "v1.0 excluded .mypy_cache, v1.1 dropped it" was also wrong: v1.0 mac paths contain 0 `.mypy_cache` lines (`grep -c mypy_cache .audit/exec/2026-07-29-phase0/paths_mac.txt` = 0), consistent with the v1.2 changelog's correction ("the directory did not exist at Phase-0 generation time"). A-0019 in the findings YAML gets the claim-text amendment via corrected evidence.
4. **Backup retention finding almost slipped:** without INJ-4's mandate to identify writer + consumer, I would have carried A-0009 forward unchanged. The injected sequencing was load-bearing.
5. **[Pre-Session-3] A-0014 root-cause was also wrong.** I assumed the DB backup script was `backup_db.sh` (writes to `.tmp/backups/` with 7-day retention, works correctly for its target). The actual script the timer runs is `pg_backup.sh` (writes to `.backups/` with 14-day retention). Correct root cause: `pg_backup.sh` has retention wired (`find ... -mtime +14 -delete`) but the prune isn't firing — 20 files, oldest 15 days old. Corrected in Phase 2 findings.
6. **[Pre-Session-3] I introduced an S1 secret exposure by snapshotting `verify_policy_block_l1.sh` into `.audit/snapshots/`** — the script embeds `PGPASSWORD=genlab_***` on 3 lines. Redacted the file immediately, added `snapshots/` to `.audit/.gitignore`, verified no git commit touched it. The password was never pushed. But the underlying issue — hardcoded prod DB creds in shell scripts — is a real Phase 2/5 finding.
