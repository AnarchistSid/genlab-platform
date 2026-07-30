# Phase 0 — Ground Truth Inventory

**Audit run:** A (2026-07-29)
**Scope:** Mac (`/Users/anarchistsid/GenLab`) + VPS (`genlab-prod` → `/opt/genlab`)
**Status:** COMPLETE
**Prior audit note:** `.audit/PHASE0.md` … `PHASE7.md` from 2026-07-24→28 exist; per spec §0.8 they are treated as untrusted claims. This artifact does not consume them.

---

## 1. Hosts

| Attribute | mac | vps |
|---|---|---|
| OS | Darwin 25.3.0 (arm64) | Linux 6.8.0-134 (x86_64) — Ubuntu 24.04 |
| Instance | user laptop | Hetzner nbg1 4GB (ubuntu-4gb-nbg1-1) |
| SSH user | anarchistsid | **root** *(noted for Phase 5)* |
| Repo root | `/Users/anarchistsid/GenLab` | `/opt/genlab` |
| Repo owner | anarchistsid:staff | genlab:genlab |
| Disk free | 56 Gi / 460 Gi (23% used) | 12 Gi / 38 Gi (67% used) |
| RAM | (laptop) | 3.7 GiB total, 151 MiB free, 200 MiB swap in use |

## 2. Git posture (verbatim command outputs — see `.audit/exec/2026-07-29-phase0/posture_{mac,vps}.txt`)

| Field | mac | vps |
|---|---|---|
| HEAD | `1f46f53f9898e611b09d9965ed9a5b6c16c7970a` | `34140a82183a5d9b9a1648a8b5460697014ddab8` |
| Branch | main | main |
| Last commit | 2026-07-28T12:44:04+05:30 | 2026-07-27T23:26:24+05:30 |
| Dirty files | 2 (`.claude/scheduled_tasks.lock`, `.audit/exec/...` created this session) | **24** (see §4) |
| Ahead/behind origin/main | 0 / 0 | 0 / 0 |
| `.git` size (`git count-objects -vH`) | 8757 loose · 16636 in-pack · **2 packs** · 50.01 MiB | 2075 loose · 39802 in-pack · **25 packs** · 133.84 MiB |

**Fact:** Mac is on commit `1f46f53f` (2026-07-28 12:44 IST); VPS is on `34140a82` (2026-07-27 23:26 IST) — production is running **~13 hours behind main HEAD** (2 commits: `1f46f53f docs(audit)` and `34140a82 test(quarantine)` … `1f46f53f` is Mac head, VPS is at the earlier commit). Neither host is ahead of origin/main.

## 3. Tool version parity (symmetric commands, §0.4)

| Tool | mac | vps | Delta |
|---|---|---|---|
| Python | 3.14.3 | 3.12.3 | **2 minor versions** |
| uv | 0.11.3 | 0.11.3 | match |
| ffmpeg | 8.1 (2026) | 6.1.1-3ubuntu5 (2023) | **~2yr behind** |
| psql | 14.19 (Homebrew) | 18.3 (Ubuntu pgdg) | **4 major** |
| node | v25.6.1 | v22.22.2 | 3 major |
| npm | 11.9.0 | 10.9.7 | 1 major |

## 4. VPS uncommitted state (§0.4 output for `git status`)

```
 M CriticalRush/niches/gaming/config/publishing.yaml
 M FrameDrift/config/publishing.yaml
 M SpliceReel/config/publishing.yaml
 M genlab-core/config/cuelinks_campaigns.yaml
?? .conformal_router_state.json
?? .genlab/  .local/  .npm/
?? .reddit_cookies.txt
?? .threads_tokens.json  .threads_tokens.lock
?? .version.env
?? .youtube_cookies.txt  .youtube_session.json
?? BlackboxBrief/assets/motion/
?? ClutchWire/assets/
?? CriticalRush/niches/gaming/assets/
?? CriticalRush/niches/gaming/config/publishing.yaml.lock
?? FrameDrift/config/publishing.yaml.lock
?? SpliceReel/assets/
```

**Notable modified file diff — CriticalRush/niches/gaming/config/publishing.yaml:**
```
@@ -121,4 +121,4 @@ auto_publish:
-  rollout_pct: 0.0
+  rollout_pct: 1.0
```
This is a live prod rollout ramp that exists **only on the VPS filesystem** and is not committed. If the VPS is rebuilt / redeployed with a clean checkout, this state is lost. See finding A-0002.

The `publishing.yaml.lock` sidecar files are consistent with rule #20 (flock on sidecar for concurrent writers).

## 5. File manifest (paths listed relative to repo root; excludes: `.git .venv node_modules .tmp .audit __pycache__ .mypy_cache .ruff_cache .pytest_cache .hypothesis .playwright-mcp .logs .backups .cache .serena .import_linter_cache .media *.pyc`)

| Bucket | Count |
|---|---|
| files on **mac** (after excludes) | 32,225 |
| files on **vps** (after excludes) | 29,479 |
| common paths (present on both) | 23,264 |
| MAC_ONLY | 8,961 |
| VPS_ONLY | 6,215 |
| **CONTENT_DIFFERS on common paths** | **637** |

### 5.1 MAC_ONLY breakdown (dominant contributors)

Nearly all MAC_ONLY files are **local ephemera not tracked in git** (0 of 8,961 appear in `git ls-files`). Dominant sources: nested per-workspace `.venv/` dirs (my `find` exclusion only covered top-level `.venv/`, so `ClutchWire/.venv/` + `dashboard/.venv/` etc. leaked in), MP3/MP4 renders (~1 000 files), screenshots (~120 PNGs at repo root), `.DS_Store`, `.log`. Nothing tracked is missing from the VPS.

### 5.2 VPS_ONLY breakdown

| Top dir | Count | Note |
|---|---|---|
| `dashboard/` | 5,865 | JS + source maps — built artifacts (`.next/`), production build committed to disk not repo |
| `.npm/` | 136 | npm cache at repo root — waste candidate |
| `BlackboxBrief/` `SpliceReel/` `CriticalRush/` `ClutchWire/` | 45+40+40+39 | mostly runtime assets |
| `.genlab/` `.runtime/` | 6+4 | live state (retro-credit state, etc.) |
| `docker-compose.yml.bak-2026-07-24-2328` | 1 | 5-day-old backup — waste |
| `.youtube_cookies.txt` `.youtube_session.json` `.reddit_cookies.txt` `.threads_tokens.json` `.version.env` | 5 | prod runtime state, correctly untracked |

### 5.3 CONTENT_DIFFERS (637 common paths that hash differently)

| Top dir | Count | Cause |
|---|---|---|
| `dashboard/` | 626 | build artifacts (`.js` 344, `.ts` 163, `.map` 24, `.mjs` 19, `.json` 50 …) |
| `genlab-core/` | 4 | includes `config/affiliate_catalog.yaml` — see finding A-0011 |
| `CriticalRush/` `SpliceReel/` `FrameDrift/` | 2+1+1 | matches VPS-dirty `publishing.yaml` files |
| `docs/` | 1 | expected doc drift |
| `.env`, `.claude`, `.DS_Store` | 3 | expected local ephemera (allow-listed per §0.4) |

**Real drift signal (excluding build artifacts + expected local ephemera): 5 YAML config files.** 4 of these 5 are the git-dirty VPS files listed in §4. The 5th (`genlab-core/config/affiliate_catalog.yaml`) is bidirectional drift: Mac has 2 new dev comments (2026-07-14 evergreen fallbacks); VPS has 2 `affiliate_enabled: false` flags + a FanCode Subscription entry — neither is committed. Split across findings A-0002 (rollout_pct) and A-0011 (rest).

### 5.4 Allow-list (§0.4 — expected divergence, not drift)

`.env`, `.env.*`, `.claude/scheduled_tasks.lock`, `.DS_Store`, `.git/**` (excluded), `.venv/**`, `.tmp/**`, `.playwright-mcp/**`, `.backups/**`, `.logs/**`, `.cache/**`, `dashboard/.next/**` (dashboard build artifacts), `uv.lock` (kept in git but rebuilt).

## 6. LOC by workspace member (`tokei`, all-language totals, excluding `.venv`/`node_modules`/`__pycache__`/`.next`)

| Member | Files | Total lines | Python code | YAML code | Notes |
|---|---:|---:|---:|---:|---|
| genlab-core | 1,279 | 299,983 | 229,627 | 6,563 | shared infra — expected dominance |
| dashboard | 437 | 102,975 | 40,299 | 31 | Next.js + Flask/Dramatiq blend |
| BlackboxBrief | 150 | 34,375 | ~13,600¹ | (mixed in md) | legacy first channel |
| CriticalRush | 123 | 21,226 | 11,547 | 2,782 | **5× siblings — consolidation candidate** |
| SpliceReel | 45 | 5,115 | 2,846 | 1,273 | thin |
| FrameDrift | 43 | 4,882 | 2,614 | 1,339 | thin |
| ClutchWire | 42 | 4,702 | 2,400 | 1,265 | thin |
| **Aggregate** | **~2,119** | **~473,258** | **~316,000** | **~17,000** | 1,703 Python files total |

¹ BlackboxBrief’s tokei output collapses many Python into markdown-fenced counts; ~3,660 pure `.py` + ~9,940 mixed.

## 7. Workspace topology (from `pyproject.toml`)

```toml
[tool.uv.workspace]
members = [
    "genlab-core",   # Layer 1 shared infra
    "dashboard",     # ops UI + review server
    "BlackboxBrief", # niche channel (ai_creators)
    "CriticalRush",  # niche channel (gaming)
    "ClutchWire",    # niche channel (sports)
    "SpliceReel",    # niche channel (movies)
    "FrameDrift",    # niche channel (anime)
]
```

7 members confirmed. Excludes: `OpenSandbox/`, `_reference/`, `Socioboard-5.0/` (ruff exclusion list — external / vendored code, out of scope for this audit).

## 8. Entrypoints

### 8.1 Mac side
- **launchd**: no genlab-* jobs (`launchctl list | grep -i genlab` → empty).
- **cron**: no genlab entries (only unrelated `ai_trading_bot` jobs).
- **[project.scripts]**: none defined in root `pyproject.toml` or any member.
- Conclusion: Mac is dev-only. All scheduled work runs on VPS.

### 8.2 VPS systemd inventory (`systemctl list-units --all --plain --no-legend "genlab-*"`)

| Bucket | Count |
|---|---:|
| Total genlab-* units (all types) | **266** |
| `inactive dead` | 173 (mostly `genlab-service-failure-alert@*` instances) |
| `active waiting` (timers) | 83 |
| `active running` (services) | **5** |
| `failed failed` | **4** |
| `active elapsed` | 1 |
| Timer files on `/etc/systemd/system/` | 85 |
| Service files on `/etc/systemd/system/` | 96 |

**Active running services:**
- `genlab-dashboard.service` — ops UI + Flask review server
- `genlab-engagement-poller.service` — YT/X/Threads polling
- `genlab-engagement-worker.service` — Dramatiq queue
- `genlab-quota-monitor.service` — disk quota
- `genlab-webhook.service` — Meta webhook receiver

**FAILED services (see finding A-0001):**
```
genlab-pipeline-ai.service         failed  2026-07-29 08:02 IST  (BlackboxBrief cron_wrapper.sh exit=2, ~7h ago)
genlab-pipeline-movies.service     failed  2026-07-29 09:02 IST  (SpliceReel daily_intel.sh exit=2, ~6h ago)
genlab-post-deploy-verify.service  failed  2026-07-27 09:30 IST  (2 days ago, exit=1)
genlab-strategist.service          failed  2026-07-26 07:30 IST  (3 days ago, exit=1, weekly LLM meta-cognition)
```

**Dangling references** (unit is referenced but no file exists on disk — see finding A-0004):
```
genlab-pipeline-ai-creators.service   referenced by /etc/systemd/system/genlab-verify-whisper-canary.service
genlab-postgres-ready.target          referenced by  genlab-auto-approver.timer, genlab-auto-approver.service,
                                                     and their .pre-2026-07-05 backup siblings
```

**Naming drift confirmation:** `genlab-pipeline-ai-creators.service` matches the naming referenced in CLAUDE.md AUTO #2 section and MEMORY.md; the actual unit on disk is `genlab-pipeline-ai.service` (with description "GenLab AI Creators Pipeline (BlackboxBrief)"). This is a class-of-bug pattern the project has noted before (session-2026-07-09 memory: "Systemd unit naming drift observed").

### 8.3 Code entrypoints (`if __name__ == "__main__":` count per member, excluding `.venv`)

```
genlab-core:    89
dashboard:       5
BlackboxBrief:   6
CriticalRush:    6
ClutchWire:      1
SpliceReel:      1
FrameDrift:      1
```

genlab-core has 89 executable-as-script modules — most are timer targets (`python -m genlab_core.*` in `.service` files). The 3 lean channels have exactly 1 entrypoint each (the pipeline runner), consistent with the "config-driven Layer 3" pattern.

## 9. Disk usage

### 9.1 VPS `/opt/genlab/` (top eaters)

| Path | Size | Comment |
|---|---:|---|
| `.backups/` | **3.7 GB** | 25% of the VPS's remaining 12 GB free — see A-0009 |
| `.venv/` | 2.5 GB | necessary; largest single item after backups |
| `.cache/` | 699 MB | mixed uv/pip cache |
| `dashboard/` | 549 MB | built + node_modules deps |
| `.git/` | **148 MB** | 25 packs — see A-0008 (Mac equivalent is 50 MB with 2 packs) |
| `BlackboxBrief/` | 139 MB | |
| `SpliceReel/` `CriticalRush/` `ClutchWire/` | 116+110+108 MB | assets |
| `.media/` | 97 MB | |
| `genlab-core/` | 22 MB | code only, very lean |
| `FrameDrift/` | 1.8 MB | (assets untracked) |

Docker: 682 MB images, 166 MB volumes, 0 build cache.

### 9.2 Mac `~/GenLab/` (top eaters)

| Path | Size | Comment |
|---|---:|---|
| `CriticalRush/` | **9.1 GB** | media assets not deployed — see A-0010 |
| `.tmp/` | 8.1 GB | dev cache |
| `.media/` | 2.2 GB | |
| `.venv/` | 1.3 GB | |
| `BlackboxBrief/` | 943 MB | |
| `dashboard/` | 345 MB | |
| `.git/` | 88 MB | |
| `FrameDrift/` `.logs/` `genlab-core/` | 76+52+52 MB | |

## 10. Prior audit context (§0.8)

`.audit/` on Mac contains: `PHASE0.md` … `PHASE7.md`, plus `phase1/` … `phase7/` subdirs, `BACKLOG.md`, `findings.jsonl` (98 KB, ~500 line-delimited findings), `EXPOSURE.md`, `OPERATOR_TASKS.md`, `PHASE2.5.md` … `PHASE2.7.md`, `PHASE3A.md` / `PHASE3B.md`. All dated 2026-07-24→28. **Not consumed by this audit.** My artifacts use the spec's `PHASE_0_*` (underscore) naming and do not overwrite prior work.

VPS `.audit/` also contains a divergent copy (see manifest diff). Not consumed either.

## 11. What runs where

| Runs on… | mac | vps | code only |
|---|---|---|---|
| Scheduled work | 0 | 85 timers, 5 always-on services | — |
| Interactive tools | dev shell, editor | rare (root-shell for ops only) | — |
| Data store | Postgres 14.19 (probably empty; see §11 note) | Postgres 18.3 running database `genlab` | — |
| Dashboard | can serve locally | live at `genlab-dashboard.service` | — |
| Media rendering | can render (`ffmpeg 8.1`) | renders in prod (`ffmpeg 6.1.1`) | — |
| Repo entries never exercised anywhere | — | — | see Phase 1 |

Note: from `psql -d genlab` on VPS (unprivileged root), got `FATAL: database "genlab" does not exist` — the DB is presumably reachable only from the `genlab` user via `pg_hba.conf`, not root. Phase 3 will do this correctly.

---

## 12. Findings carried (10 of 12 budget used) — see `.audit/PHASE_0_findings.yaml`

| id | S | title |
|---|---|---|
| A-0001 | S1 | 4 systemd services in FAILED state on VPS (2 pipelines + post-deploy-verify + strategist) |
| A-0002 | S1 | CriticalRush `rollout_pct: 0.0 → 1.0` is uncommitted VPS-local prod state |
| A-0003 | S2 | VPS is 2 commits (~13h) behind main HEAD |
| A-0004 | S2 | 2 dangling systemd unit references (`genlab-pipeline-ai-creators.service`, `genlab-postgres-ready.target`) + 2 orphaned `.pre-2026-07-05` unit files |
| A-0005 | S2 | FFmpeg version drift: mac 8.1 (2026) vs vps 6.1.1-3ubuntu5 (2023) — media reproducibility risk |
| A-0006 | S2 | Python version drift: mac 3.14.3 vs vps 3.12.3 |
| A-0007 | S2 | Postgres version drift: mac 14.19 vs vps 18.3 — mac dev DB useless for validating vps SQL/DDL |
| A-0008 | S3 | VPS `.git/` has 25 packs vs mac's 2 — `git gc` will reclaim ~50-80 MB and speed operations |
| A-0009 | S2 | VPS `.backups/` occupies 3.7 GB (25% of free disk) with no visible consumer |
| A-0010 | S3 | CriticalRush repo dir is 9.1 GB on mac (media assets untracked, not deployed) — Phase 1 consolidation |

### Noted (not carried into findings)

- Prior `.audit/` PHASE0–PHASE7 exist on both hosts; untrusted per §0.8. Not consumed.
- Dashboard build artifacts (626 CONTENT_DIFFERS in `dashboard/`) are expected (client vs server builds).
- `.env.bak.*` sprawl on VPS (7 backup files) — deferred to Phase 5 (secret exposure).
- Mac `.venv/` at every workspace member (root + nested per member) is likely an artifact of dev environment; nested venvs are gitignored. Confirmed no code drift, only noise in my manifest.
- BlackboxBrief has 27 markdown docs (9,130 lines) that tokei collapses into mixed-language totals — legacy docs. Phase 1.
- CriticalRush Python code is ~5× the size of CW/SR/FD siblings (11,547 vs ~2,600 LOC). Documented in CLAUDE.md as "consolidation candidate" — carried as A-0010 with disk symptom + Phase 1 marker.
- `verify-2026-07-22-fixes.timer` shows `active elapsed`, last fired 2026-07-23; purpose has expired. Deferred to Phase 4.
- Node 25 on mac vs Node 22 LTS on vps — noted, tolerable (dashboard build parity is what matters).
- No pre-commit hook enforcement of committed CriticalRush `rollout_pct` — deferred to Phase 4/7.

### Blocked / deferred

- DB schema inventory (`\d+`, index count, RLS role check) — deferred to Phase 3. My root-shell `psql -d genlab` failed with `database "genlab" does not exist`; need to run as `genlab` user or via pg_hba-permitted connection.
- `pg_stat_statements` — Phase 3.
- Full doc/reality reconciliation for CLAUDE.md's 28 numbered rules — 5 items verified this phase (see `PHASE_0_doc_reality_delta.md`); the remaining 23 need code/DB access appropriate to later phases.
- Prefect deployment registry — none found in inventory; if it existed, it would surface in the systemd scan. Treated as "does not exist in prod" pending Phase 4 confirmation.

---

## 13. Methodology issues encountered (per §0.8, honest disclosure)

1. **My initial `find` command didn't exclude nested `.venv/` dirs** (`ClutchWire/.venv/`, `dashboard/.venv/`, etc. — only top-level `.venv/`). This inflated MAC_ONLY `.py` counts by ~1,500 files and misled the first drift analysis. Corrected in §5.1 by cross-checking against `git ls-files`. The spec's manifest command (§0.4) also has this gap — worth revising for future runs.
2. **First `xargs` invocation lost 85% of files** on Mac (4,607 / 32,225 hashed) — silent failure of `xargs -I{} shasum` under load. Re-ran with NUL-delimited `xargs -0 -n 1` which handled all 32,225. Both manifests in `.audit/manifest_{mac,vps}.txt` are the corrected versions.
3. **`psql -d genlab` on VPS as root got a misleading error** (`database does not exist`), which actually indicates a user/pg_hba mismatch rather than a missing DB. Recorded as a blocker for Phase 3 rather than a Phase 0 finding.
4. **SSH aliases were tried before finding the correct name.** Tried `vps` and `genlab-vps` (both fail); real alias is `genlab-prod`. No misleading data captured from failures.
