# AUDIT A — Retracted / Materially Corrected Findings

This file lists findings whose original claim did not survive re-verification. Each entry names:
- **Original claim** and where it appeared
- **Why the evidence misled** (methodology error)
- **Superseded by** (which corrected finding, if any)

Not the same as `AUDIT_A_METHODOLOGY.md` §Errors-by-origin — this file is about the FINDINGS that changed, not the process errors that caused them.

---

## Retracted within-run (mid-run supersedes)

### A-0002 → A-0012 → A-0027 (chain)
- **Original claim (Phase 0):** CriticalRush `rollout_pct: 0.0 → 1.0` is live 100% rollout.
- **Why misleading:** ignored `auto_publish.enabled: false` two lines above the changed key.
- **Corrected via:** A-0012 (Phase 1, same finding + 3-channel scope), then A-0027 (Phase 2, "inert because gated" once loader trace showed enabled respected).
- **Register carries:** A-0027 + A-0029 + A-0028 interaction (safe apart, dangerous together).

### A-0009 → A-0014 → A-0026
- **Original claim (Phase 0):** `.backups/` = 3.7 GB with no verified consumer.
- **Why misleading:** static-only claim without checking systemd + scripts. Also filed S2 with STATIC_ONLY tag (§0.3 caps at S3).
- **Corrected via:** A-0014 (Phase 1, found backup-restore-test consumer — but wrong script), then A-0026 (Phase 2, correct script `pg_backup.sh` identified; real finding = retention wired-not-firing).
- **Register carries:** A-0026 + A-0062 (visual-backup twin) + A-0083 (class-fix).

### A-0015 → A-0062 (Pre-Session-9 merged A-0069)
- **Original claim (Phase 1):** visual-backup `.backups/visuals/` = 3.5 GB with no restore consumer.
- **Why misleading:** consumer was `backup_visual_assets.sh` line 121 prune (14-day rolling). I read the line and concluded "safe" without testing whether it fires.
- **Corrected via:** A-0062 (Phase 6, fire-verify showed 33-day-old files retained, prune NOT firing). Timeline (was A-0069) merged in Pre-Session-9.

### A-0019 (in-place corrected)
- **Original claim (Phase 1):** 6 `.hypothesis/examples/` files leaked into MAC_ONLY under v1.1 exclusion; v1.0 excluded `.mypy_cache/` then v1.1 dropped it.
- **Why misleading:** fabricated "6" (real count via grep = 0); wrong spec history (v1.0 mac paths had 0 `.mypy_cache` lines).
- **Corrected in-place:** A-0019 claim rewritten Pre-Session-3 with real counts (only .mypy_cache = 18 leaks; hypothesis = 0).

### A-0024 → A-0043
- **Original claim (Phase 2):** psycopg3 `KeyError: 0` in 2 scripts, actively broken 5 days, nothing paging.
- **Why misleading:** read `.runtime/*_last_error.txt` files and inferred current state from file presence + timestamps. Never checked whether the invoking service had run successfully since. Both scripts had the fix in current source; both had run SUCCESS at 2026-07-29 09:00 per systemd status.
- **Corrected via:** A-0043 (Phase 4). Class-of-bug (i): durable-error files don't self-clear.

### A-0030 → A-0078
- **Original claim (Phase 2):** BB has 758 JSON files (anomalous vs 1-3 in lean channels).
- **Why misleading:** ad-hoc grep didn't inherit the manifest exclusion set (`.tmp/`). 741 of 758 are `BlackboxBrief/.tmp/*` dev-cache files that v1.1+ manifests already exclude.
- **Corrected via:** A-0078 (Phase 7). Class-of-bug (ii sub-form): grep didn't use same exclusions.

### A-0055 → A-0072
- **Original claim (Phase 5):** no secret-scan hook exists in the codebase.
- **Why misleading:** I inferred absence from the presence of `pg_backup.sh`'s DSN. Never read `.pre-commit-config.yaml`. gitleaks v8.24.3 IS configured.
- **Corrected via:** A-0072 (Phase 7). Real finding: hook exists but was bypassed by the 2026-07-22 commit that introduced 3× `PGPASSWORD=genlab_***` (either `pre-commit install` not run or `--no-verify`).

### A-0068 → A-0073 (Pre-Session-9 correction; harder-earned than most)
- **Original claim (Phase 6-7):** revenue tracking not wired anywhere in the monorepo — zero `INSERT INTO affiliate_clicks`, zero `class AffiliateClick`.
- **Why misleading:** greps didn't match the codebase's backend abstraction pattern. Writer exists at `link_tracker.py:144: pg.create("affiliate_clicks", record)`. Invoker exists at `dashboard/server/api/links.py:1084-1086`. Full emission path exists in `threads.py`, `x_twitter.py`, `facebook.py`, `parallel_publish.py`, `affiliate_reply.py`.
- **Corrected via:** A-0073 rewrite (Phase 8 addendum). Real finding: situation 2 = wired but 0 conversions, either because audience doesn't click OR because emitted URLs bypass the dashboard redirect (direct `amzn.to/`/`cuelinks.com/`).
- **Class-of-bug repeated:** A-0055 was the same class one finding earlier. **Recognizing a class is not the same as not repeating it** — audit's clearest single lesson.

### A-0069 → A-0062 (Pre-Session-9 merged)
- **Original claim (Phase 6):** ~113 days to disk exhaustion at low load, ~48 days when render volume recovers.
- **Why misleading:** filed as a separate S3 finding alongside A-0062's S1 mechanism. Register alarmism — one truth split across two severities read more alarming than the evidence.
- **Corrected via:** merged into A-0062 as the timeline half of a S2 (not S1) finding. Sequencing rule: fix before BB recovery.

---

## Partial retractions this session (Phase 9A gate re-verifications)

State changed between Phase 3-8 (2026-07-29) and Session 10 gate (2026-07-30). Original findings not wrong — reality moved. See `PHASE_9A_gate.md` for evidence.

### A-0033 partial retraction
- **Original (Phase 3):** `auto_approval_calibration` = 0 rows AND `strategist_reports` = 0 rows. Ratchet path dark.
- **Session-10 gate:** `auto_approval_calibration` = **305 rows**; `strategist_reports` = **5 rows**. Calibration path NO LONGER DARK.
- **Register disposition:** DEFER with trigger "next successful strategist run confirms consistency OR operator confirms whether the 5 rows are seed/test data" (systemd shows strategist STILL failed, but DB shows 5 report rows — inconsistency worth resolving).

### A-0038 partial retraction
- **Original (Phase 3):** `tenants` (64 KB, 0 rows), `tenant_niches` (48 KB, 0 rows). SaaS schema drafted-but-empty.
- **Session-10 gate:** `tenants` = **1 row**; `tenant_niches` = **5 rows**. Tenant tables ARE populated (1 tenant, 5 niches — matches 5-channel deploy).
- **Register disposition:** ACCEPT with reversal condition. Half of A-0038's original claim (empty tenant tables) retracts. Other half survives (`product_embeddings` empty, monetization tables empty — but A-0068/A-0073 rewrote that story anyway).

### A-0044 amended
- **Original (Phase 0/4):** 4 failed services (pipeline-ai, pipeline-movies, post-deploy-verify, strategist).
- **Session-10 gate:** 3 failed services (`pipeline-sports` newly, `post-deploy-verify`, `strategist`). BB + Movies pipelines RECOVERED. Sports newly failed.
- **Register disposition:** FIX with amended list. Root-cause hunt still blocked by A-0050 journal rotation.

### A-0034 mode-changed
- **Original (Phase 3):** BB pipeline: 32 attempts / 8 days / 0 PUBLISHED (failing loudly).
- **Session-10 gate:** BB has **0 attempts** in last 2 days (Jul 29-30). Rows show 4/day through Jul 28, then silence. Combined with A-0044 recovery of `pipeline-ai`, mode changed from "failing" to "silent" (either operator-disabled or silent-early-exit before writing publishing_analytics).
- **Register disposition:** FIX. Verification gate: `PUBLISHED > 0` in a subsequent day.

### A-0080 downgraded S1 → S2
- **Original (Phase 8):** SaaS multi-tenant layer half-built; DB/role ready, dashboard/quota/per-request session context unfinished. S1.
- **Session-10 gate:** tenants populated, calibration active. More built than Phase 8 assessed.
- **Register disposition:** DEFER at S2. Still open: dashboard RBAC, per-request `SET LOCAL app.niche_id`, dual layout collapse, affiliate emission path (A-0073).

---

## Findings ACCEPT-with-reversal (positive posture)

These are not retractions — they're findings dispositioned as ACCEPT with a named condition that would flip them. Included here for completeness of the "what changed since original claim" ledger.

- A-0043 (durable-error retraction confirmation)
- A-0054 (`genlab_app` GRANTs verified — consumed by runbook)
- A-0059 (`git gc` un-gated by BFG check)
- A-0061 (nested `.git` no secret exposure)
- A-0077 (typing discipline healthy)
- A-0085 (upgrade currency clean)
- A-0087 (cross-channel imports 0/5 clean)
- A-0088 (class-of-bug taxonomy — inherit forward)

---

## No S0/S1 failed re-verification this session (none moved into this file as a fresh retraction)

All S1 findings re-verified fresh in Session 10 held. The 5 amendments above are partial (state changed, not "original was wrong"). If Session 11 Phase 9B or any post-audit re-verification finds new problems, append here rather than rewriting.
