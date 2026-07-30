# Phase 1 — Canonical Mac ↔ VPS Drift (v1.1 manifests)

**Regenerated:** 2026-07-29 (Session 2 pre-step)
**Manifest exclusions:** per spec v1.1 §0.4 (nested-anchored globs, `.env*`, `.DS_Store` excluded by name, plus `.next/`, `frontend/dist/`, `.npm/`, `.playwright-mcp/`, `.cache/`, `.backups/`, `.bfg-report/`).
**Junk self-check:** all 9 categories = 0 on Mac; VPS shows 1 hit on `\.env` pattern which is a **false positive** — the matched file is `.version.env` (version tag metadata, not env-secrets). The `-not -name '.env*'` filter correctly excluded true env files.
**Manifests NOT committed** — `.audit/.gitignore` covers `manifest_*`, `*_paths.txt`, `*_hashed.txt`, and `exec/`.

## Headline collapse

| Metric | v1.0 | v1.1 | Δ |
|---|---:|---:|---:|
| MAC files | 32,225 | 2,972 | −91% |
| VPS files | 29,479 | 2,868 | −90% |
| MAC_ONLY | 8,961 | 314 | −97% |
| VPS_ONLY | 6,215 | 210 | −97% |
| CONTENT_DIFFERS | 637 | **6** | −99% |

The 631-file collapse in CONTENT_DIFFERS was almost entirely `dashboard/.next/` build artifacts (mac vs vps client bundles). The remaining 6 are the real signal.

## The 6 CONTENT_DIFFERS (canonical drift set — verbatim)

```
.claude/scheduled_tasks.lock
CriticalRush/niches/gaming/config/publishing.yaml
FrameDrift/config/publishing.yaml
genlab-core/config/affiliate_catalog.yaml
genlab-core/config/cuelinks_campaigns.yaml
SpliceReel/config/publishing.yaml
```

## Snapshot: uncommitted VPS state — saved to `.audit/PHASE_2_uncommitted_prod_state.diff` (permitted by §0.1; a redeploy would erase it)

### 1. `CriticalRush/niches/gaming/config/publishing.yaml` — real change
```yaml
@@ -121,4 +121,4 @@ auto_publish:
   enabled: false
   min_confidence: 0.85
   max_approvals_per_pass: 3
-  rollout_pct: 0.0
+  rollout_pct: 1.0
```

### 2. `FrameDrift/config/publishing.yaml` — real change + trivial YAML formatting
```yaml
@@ -85,4 +85,4 @@ auto_publish:
   enabled: false
   min_confidence: 0.85
   max_approvals_per_pass: 3
-  rollout_pct: 0.0
+  rollout_pct: 1.0
```
Also 3× `account_id: null → account_id:` (semantically identical in YAML — null vs empty; likely rewritten by a YAML-manipulating script).

### 3. `SpliceReel/config/publishing.yaml` — real change + trivial YAML formatting
```yaml
@@ -87,4 +87,4 @@ auto_publish:
   enabled: false
   min_confidence: 0.85
   max_approvals_per_pass: 3
-  rollout_pct: 0.0
+  rollout_pct: 1.0
```
Same 3× `null → empty` YAML rewrite as FrameDrift.

**Phase-2-critical observation:** all three auto-approval channels have the identical `rollout_pct: 0.0 → 1.0` change. This is a **systematic 3-channel rollout ramp** applied directly on the VPS filesystem, never committed. Whether it takes effect depends on the `auto_publish.enabled: false` master switch above it — a question that terminates in Phase 2 (spec injected item). Note also that the auto-approver **timer file** shows `enabled: false` in `publishing.yaml` but per Phase 0's CLAUDE.md read the actual gate flag is `GENLAB_AUTO_APPROVE_DISABLED=1` at env level and the touch-file kill switch — so YAML `enabled` may or may not be authoritative. Phase 2's loader-code trace resolves this.

### 4. `genlab-core/config/cuelinks_campaigns.yaml` — VPS regenerated file
```
@@ -1,19 +1,903 @@
-# AUTO-GENERATED — do not hand-edit.
```
The Mac copy still carries the AUTO-GENERATED header; the VPS copy has been regenerated to 903 lines. Consistent with the `genlab-cuelinks-campaign-refresh.timer` (weekly, per Phase 0 systemd list). This is expected behavior — the timer runs on VPS only and writes the file in place; git never sees the refresh unless someone manually commits it. **Real class-of-bug candidate: files marked "AUTO-GENERATED — do not hand-edit" whose regeneration lives outside git are functionally VPS-only state.**

### 5. `genlab-core/config/affiliate_catalog.yaml` — bidirectional drift
- Mac: has 2 dev comments (2026-07-14 evergreen fallbacks) VPS lacks
- VPS: has 2 `affiliate_enabled: false` flags + a FanCode Subscription entry Mac lacks
- Neither is committed. Direction of authority: unclear until Phase 2.

### 6. `.claude/scheduled_tasks.lock` — expected local ephemera
Both hosts hold a Claude Code session lock. Allow-listed.

## MAC_ONLY (314 files) — non-drift categorisation

| Top-level dir | Count | Verdict |
|---|---:|---|
| `.media/` | 87 | local render cache (mostly `.mp4`); ephemera |
| `BlackboxBrief/` | 27 | screenshots + assets not deployed |
| `genlab-core/` | 25 | needs investigation (see below) |
| `.mypy_cache/` | 18 | should have been excluded — v1.1 exclusion misses it |
| `CriticalRush/` | 9 | screenshots + local assets |
| `.claude/` | 7 | session state |
| `.serena/` | 6 | Serena MCP state |
| `SpliceReel/`, `FrameDrift/`, `ClutchWire/` | 5+5+5 | assets not deployed |
| `.superpowers/` | 4 | plugin state |
| Repo-root PNG screenshots (`yt-quota-step-1.png` etc.) | ~12 | leftover from operator UI walkthroughs |

`.mypy_cache/` leaked into MAC_ONLY (18 files) — the v1.1 exclusion list omits `.mypy_cache/`. Recorded as a v1.1 §0.4 correction candidate for the next spec revision; ignoring these 18 doesn't change any finding.

`genlab-core/` MAC_ONLY (25 files) — sampled: `.hypothesis/examples/*` sub-tree wasn't fully caught by `*/.hypothesis/*` (the file paths are `genlab-core/.hypothesis/examples/…`). Another v1.1 §0.4 gap. Non-issue for drift, noted for spec revision.

## VPS_ONLY (210 files) — non-drift categorisation

| Path pattern | Count | Verdict |
|---|---:|---|
| Channel `assets/` dirs (BB/SR/CR/CW motion/) | ~150 | prod media assets not committed (`.mp3`/`.mp4`) — expected |
| `.media/` | 14 | prod render intermediates |
| `genlab-core/` (mostly runtime `.json`) | 10 | includes `.genlab/`, `.runtime/`, YouTube quota lock |
| `.genlab/youtube_quota.json.lock`, `.reddit_cookies.txt`, `.threads_tokens.{json,lock}`, `.version.env`, `.youtube_{cookies.txt,session.json}`, `.conformal_router_state.json` | 8 | live prod runtime state, correctly untracked |
| **`.runtime/auto_accept_strategist_proposals_last_error.txt`** | 1 | matches Phase 0 finding A-0001 — strategist failed 3 days ago; error captured on disk |
| **`.runtime/parse_testable_predictions_last_error.txt`** | 1 | related durable error from same subsystem |
| **`BlackboxBrief/.engagement_replied.jsonl`** | 1 | engagement engine state — appears untracked but load-bearing |
| **`docker-compose.yml.bak-2026-07-24-2328`** + **`deploy/docker-compose.prod.yml.bak-2026-07-24-2330`** | 2 | 5-day-old backup files — Phase 1 waste candidate |
| **`{CR,FD,SR}/config/publishing.yaml.lock`** | 3 | sidecar flock files per rule #20 |
| **`scripts/verify_policy_block_l1.sh`** | 1 | operator utility script written on VPS, never committed |
| `line 1: /Users/anarchistsid/…` | 1 | **methodology error** — a shell stderr line from my VPS hash command leaked into paths_vps.txt (a `tr` reading a Mac-local path via SSH). Recorded for Phase 9 methodology ledger (v1.1 self-error). Does not affect any finding. |

## Allow-list (justifications per §0.4)

- `.claude/scheduled_tasks.lock` — session lock, expected divergence
- `.serena/`, `.superpowers/`, `.mypy_cache/`, `.hypothesis/`, `.import_linter_cache/` — dev tool state
- `.media/`, channel `assets/motion/` — prod media assets; live-generated, not tracked
- `.runtime/*.txt`, `.genlab/*.lock`, `.threads_tokens.*`, `.youtube_*`, `.reddit_cookies.txt`, `.conformal_router_state.json`, `.version.env`, `.engagement_replied.jsonl` — prod live state, correctly untracked
- Repo-root `.png` screenshots — operator UI walkthroughs, dev-only

## Downstream

- **Phase 2** consumes this file: canonical drift set = 6 files. Trace loader code for the 3 publishing.yaml + 2 genlab-core/config/ files. The 3× rollout_pct pattern is a **systematic** VPS-local change requiring one architectural answer (loader), not three per-channel investigations.
- **Phase 8** consumes this file: no consolidation candidates specific to drift (build artifacts already excluded).
- Additional MAC_ONLY/VPS_ONLY paths worth Phase 1 attention: `docker-compose.yml.bak-*` sprawl (2 files, ~1 KB each — trivial to delete), `scripts/verify_policy_block_l1.sh` uncommitted operator script.
