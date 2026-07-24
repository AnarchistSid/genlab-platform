# PHASE 0 — Workspace & Ground Truth

## The map (workspace-level, LOC = line count over text-looking files)

| Directory | Files | LOC | Total bytes | Newest | Days stale |
|---|--:|--:|--:|---|--:|
| **Repo total** | 11,004 | 1,811,209 | 21.98 GB | 2026-07-24 | 0 |
| BlackboxBrief | 1,680 | 1,020,028 | 977 MB | 2026-07-23 | 0 |
| genlab-core | 1,613 | 299,640 | 13 MB | 2026-07-24 | 0 |
| dashboard | 561 | 101,993 | 3.9 MB | 2026-07-23 | 0 |
| CriticalRush | 792 | 49,347 | 9.80 GB | 2026-07-22 | 1 |
| scripts | 122 | 28,093 | 1.0 MB | 2026-07-24 | 0 |
| SpliceReel | 66 | 5,246 | 6.7 MB | 2026-07-23 | 0 |
| FrameDrift | 187 | 5,055 | 78 MB | 2026-07-22 | 1 |
| ClutchWire | 62 | 4,832 | 9.0 MB | 2026-07-22 | 1 |
| docs | 115 | 45,625 | 2.0 MB | 2026-07-23 | 0 |
| deploy | 209 | 1,036 | 254 KB | 2026-07-24 | 0 |
| tests | 10 | 2,395 | 98 KB | 2026-07-24 | 0 |
| .tmp | 2,221 | 56,000 | 8.65 GB | 2026-07-24 | 0 (runtime scratch) |
| .playwright-mcp | 760 | 185,735 | 23.5 MB | 2026-07-23 | 0 (tooling, non-source) |
| .logs | 157 | 3,706 | 54 MB | 2026-06-16 | **38** |

**Stale-directory candidate (>60d): 184 dirs.** [SUPERSEDED — see Corrections A1] Oldest: `BlackboxBrief/tests/fixtures` at 157 days (2026-02-16). Full list at `.audit/phase1/01_stale_dirs.csv`.

**BlackboxBrief 1M LOC anomaly**: [SUPERSEDED — see Corrections A2] BlackboxBrief net source = 34,189 LOC. The 1M number is BlackboxBrief/.tmp/ scratch (985,519). The measurement rule is what needs correcting, not the assessment of BlackboxBrief.

---

## Corrections (audit of the audit — 2026-07-24)

The three claims above were logged before every shell had exited. Rule for
every subsequent phase: no summary until all commands have returned.

### A1 — Stale directories

- **Retracted**: "Stale-directory candidate (>60d): none."
- **Correct**: `awk -F',' 'NR>1 && $7>60' .audit/phase1/00_tree.csv | wc -l` = **184**.
- Oldest = `BlackboxBrief/tests/fixtures` at **157 days** (2026-02-16).
- Full sorted list: `.audit/phase1/01_stale_dirs.csv` (185 lines with header).

### A2 — BlackboxBrief LOC + the missing cleanup finding

- **Retracted**: F-0003 saying BlackboxBrief is 200× other niches.
- **Verified**: net source LOC per niche:
  - BlackboxBrief: **34,189** (not 1,020,028; the 986K delta is `.tmp/`)
  - CriticalRush: **21,298** (not 49,347)
  - ClutchWire/SpliceReel/FrameDrift: ~4–5K each (unchanged)
- **Measurement rule** (INFO F-0003 rewritten): `00_tree.csv total_loc` counts
  scratch as source. Every LOC-derived ratio must exclude
  `.tmp/  .hypothesis/  .playwright-mcp/  .grimp_cache/  .import_linter_cache/
   *.egg-info/  node_modules/  .venv/  dist/  build/  __pycache__/  .audit/
   .serena/  .media/  .logs/  ..bfg-report/`.
- **New HIGH finding F-0006**: Local `.tmp/runs/` never cleaned up. Confirmed:
  - `.tmp/runs/anime_20260405_060004` = **2.1 GB**, mtime 2026-04-05 (**110d old**)
  - `.tmp/runs/sports_20260404_100003` = **960 MB**, mtime 2026-04-04 (111d)
  - `.tmp/runs/sports_20260405_100004` = **1.0 GB**, mtime 2026-04-05 (110d)
  - `.tmp/runs/sports_20260406_100005` = **582 MB**, mtime 2026-04-06 (109d)
  - Total `.tmp/`: **8.1 GB**. `CriticalRush/.tmp/` (`rendered/` + `clips/`): **9.1 GB**.
  - Scratch is ~17 GB of the 22 GB local repo.
- **Cleanup cron on macOS** (`com.genlab.cleanup-runs.plist`): does NOT exist.
  `launchctl list | grep -i cleanup` returns nothing. Cleanup only runs on the
  VPS via `genlab-cleanup.timer`. The 8.1 GB local scratch has no scheduled
  reaper because local execution ceased ~2026-06-16 (see A3).

### A3 — Local vs VPS execution boundary

- **Retracted**: "likely superseded by journalctl" — journalctl doesn't exist on macOS.
- **Tested**: local `.logs/` newest file = **2026-06-16** (`check_permissions_drift.log`).
  Earliest VPS deploy entry in `/opt/genlab/.git/logs/HEAD`: **2026-05-17**.
  Local `~/Library/LaunchAgents/*genlab*`: none. Local Prefect activity: none
  since March. VPS systemd `genlab-*` services: 5 active running, 2 failed
  (strategist + proposal-auto-accept).
- **Conclusion**: local pipeline execution stopped ~**2026-06-16**. Between
  ~05-17 and ~06-16 both ran; after 06-16, VPS-only. **This reshapes the
  audit**: Phase 5 subsystem reads describe code whose only live instance
  is on the VPS. Phase 4 drift is the primary risk surface, not a
  secondary one.

### A4 — Import-linter is installed and IN USE

- **Retracted**: "grimp is unavailable, use pydeps."
- **Verified**: `genlab-core/.importlinter` declares 1 layers contract
  ("Service modules may depend on interfaces, not vice versa"). Ran
  `lint-imports` under `--package genlab-core --with import-linter`.
  Analyzed 423 files, 804 dependencies.
- **Contract status**: **1 kept: 0 / broken: 1**.
- **Violation**: `genlab_core.media.frame_compositor` imports
  `genlab_core.tts.factory` at line 660 — media is below tts in the layer
  stack. Filed as **F-0007** (medium correctness).
- **Undeclared architectural rules from CLAUDE.md**:
  - "genlab-core never imports from channel packages" → 0 violations today,
    but has NO contract enforcing it.
  - "Niches never import from each other" → 0 violations today, but has NO
    contract enforcing it.
  - Filed both as **F-0008** (medium architecture) — the rules exist in docs
    but nothing at test-time prevents regression.

### A5 — BFG Repo-Cleaner ran on 2026-04-03

- **Filed as HIGH security finding F-0009.**
- Report at `..bfg-report/2026-04-03/21-58-04/`:
  - `changed-files.txt` — 41 file rewrites
  - `object-id-map.old-new.txt` — 1,079 objects rewritten
- **Files touched (partial list)**: `.env.example`, `settings.local.json` (5
  versions), `paapi_client.py`, `network_registry.py`, `affiliate_catalog.yaml`
  (4 versions), pipeline logs `*_pipeline_logs.jsonl` (4 files), various
  design docs + test files.
- **Blob reachability**:
  - Old `.env.example` blob **STILL EXISTS locally** (`git cat-file blob
    eae2eb49...`). Content is clean — 0 credential-shaped values found via
    entropy scan.
  - Old `settings.local.json` blob `c463c6ed` **STILL EXISTS locally**,
    **29 high-entropy 35-char+ tokens** found via entropy scan (values NOT
    printed here). This is the Claude Code local settings file — MCP tokens,
    API keys, or hashes are candidates.
  - Most other old blobs (network_registry.py, paapi_client.py, older
    affiliate_catalog.yaml versions) are GONE locally — BFG + gc completed.
- **Remote branches predating BFG (2026-04-03)**: **0** (all 764 remote
  branches have first commit ≥ 2026-05-22). Suggests GitHub-side history
  was successfully force-pushed post-BFG.
- **git count-objects**: 8,550 loose + 16,636 in-pack, 31.85 MiB size-pack,
  garbage: 0. History is now compact.
- **Whether the 29 tokens were rotated**: UNVERIFIED — no operator confirmation
  in memory. Rotation MUST be checked as follow-up.
- **Phase 4 secret scan MUST cover**: pre-BFG blobs still in local pack,
  every remote branch (764), reflog. Not just HEAD.

### A6 — Process failure noted

Summaries written before shells exit produce the three errors above.
Rule for every later phase: no PHASE_.md write until every command from
that phase has returned.

## Git state — `.audit/evidence/00_git_state.txt`

- HEAD: `edf91209` (2026-07-24 14:43 IST, `feat(observability): scanner for stale durable-error files`)
- Branch: `main`
- Uncommitted: 2 trivial files (`.claude/scheduled_tasks.lock`, `.gitignore` add of `.audit/`). No code drift.
- Local branches: **98**. Remote branches: **764**. Stale-branch load is severe.

## Toolchain — `.audit/evidence/00_toolchain.txt`

All 8 audit tools installed OK: `cloc 2.10`, `tokei 14.0.0`, `radon 6.0.1`, `vulture 2.16`, `ruff 0.15.14`, `pip-audit 2.10.1`, `pydeps 3.0.6`, `jscpd 5.0.12`. Runtimes: Python 3.14.3, Node 25.6.1, uv 0.11.3.

Substitution: `grimp` has no CLI entrypoint on install; using `pydeps` instead for import-graph work. Both are ast-based; equivalence to be re-validated in Phase 2. **[SUPERSEDED — see A4. grimp is available as a library and already used by the project (`.grimp_cache/`, `.import_linter_cache/`); `lint-imports` CLI installed via `uv tool install import-linter` and is the correct tool. `pydeps` substitution not needed.]**

## VPS — `.audit/evidence/00_vps_access.txt`

- Host `genlab-prod` = `46.224.237.56` (Hetzner nbg1). SSH as root, key auth.
- Ubuntu 6.8.0-134, uptime 8d 20h.
- Disk `/`: **29 GB / 38 GB used (80%)**. Provisional CRITICAL — logged for Phase 4.
- Memory: 3.7 GB total, 452 MB free (matches CLAUDE.md "4 GB VPS OOMs on libx265").
- `/opt/genlab` owned `genlab:genlab` (rule #15 satisfied).
- Sudo available; can read `/opt/genlab/.env`; can `psql` to PostgreSQL 16.14 as `genlab`.

## Blindness list

- **`grimp` import-graph CLI** unavailable — using `pydeps`. Any layer-violation finding must be re-checked with a Python-script grimp walker if Phase 2 anomalies appear. **[SUPERSEDED — see A4. Actual state: `import-linter` (uses `grimp` internally) installed and running.]**
- **`node_modules`, `.venv`, `__pycache__`, `dist`, `.audit`, `.playwright-mcp`** excluded from map. Any finding pointing at these needs explicit re-inclusion.
- **Assets on VPS beyond `/opt/genlab`** unmapped this phase (media caches, systemd unit files, cron entries). Phase 4 scope.
- **Local macOS launchd + cron** not inspected. Phase 4 scope.
- **Remote branch content** not inspected — 764 remote branches record only exists as names; drift within them (stale hot-patched work) is out of scope until specifically probed.
- **Actual runtime output** (was any stage exercised in the last 7d?) — deliberately unknown until Phase 3 by design.
