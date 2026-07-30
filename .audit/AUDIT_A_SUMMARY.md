# AUDIT A — Summary (2026-07-30, Phase 9A)

## The one-picture view: dark self-correction, lit self-advancement

Across 9 sessions and 79 findings (47 in the register after merges + retractions), one pattern is more informative than any single row: **every self-correction faculty the audit measured is dark, while every self-advancing faculty runs clean.**

- **Publish success** → BB fell from "failing 4/day, 0 published for 8 days" (A-0034, Phase 3) to "0 attempts in the last 2 days" (Session-10 gate) — mode changed from failing to silent; root cause dark because journal rotated (A-0050).
- **Guardrail enforcement** → 3 independent instances of "configured but not firing" (A-0083 meta): the pg_backup prune, the visual-backup prune, and the gitleaks pre-commit hook. Three mechanisms, all in place, none doing their job.
- **Tenant isolation** → RLS policies exist (24 of them, on all tenant-scoped tables) but the app connects as a superuser role with `bypassrls=t` (A-0032). Empirically re-confirmed at gate: cross-niche read returned all 5 niches.
- **Quality signal** → test suite has 77 collection errors on 10,783 tests (A-0074) — coverage cannot be measured until this is fixed.
- **Credential rotation** → hash-verified live in public repo history (A-0053), sitting there **30+ hours after disclosure**, un-rotated.

Versus:
- **Auto-ramp writer** advances all 3 auto-approval channels to `rollout_pct: 1.0` cleanly (A-0023). Held back only by a single `enabled: false` gate (A-0027), whose sibling default is fail-open to 1.0 (A-0029), on a dual-layout system where a 6th channel gets 2 conflicting templates (A-0028). **Three findings safe apart, dangerous together** — the exact class of failure the July 2026 methodology missed.

The system builds new capabilities faster than it verifies its old ones still fire. **Every fix in this register is a form of "make the safety mechanism actually do what it's configured to do."**

Also positive, from Session-10 gate re-verification: BB + Movies pipelines RECOVERED since Phase 4; `auto_approval_calibration` went 0→305 rows; `tenants`/`tenant_niches` went 0→1+5 rows. Some things did fix themselves between phases. Sports pipeline newly failed. Details in `PHASE_9A_gate.md`.

## What is broken now (post-Session-10 gate)

- **A-0053** — `genlab_***` is the live prod DB password + in public source-repo history + `.env` is 644 world-readable. Un-rotated 30+ hours after disclosure. `RUNBOOK_credential_rotation.md` is a 30-minute action; not executed.
- **A-0032** — RLS bypass (24 policies no-op); `genlab_app` role ready with GRANTs on all 45 tables. Bundled into the same rotation runbook.
- **A-0044** — 3 systemd services failed at gate: `pipeline-sports`, `post-deploy-verify`, `strategist`.
- **A-0034** — BB pipeline mode changed from "failing" to "silent" (0 attempts last 2 days). Root cause dark.
- **A-0074** — pytest unrunnable (77 collection errors); coverage measurement blocked everywhere.
- **A-0072/A-0083** — hook exists, doesn't fire (gitleaks) — same class as prune scripts not firing.

## What is costing money now

- Rotation delay each day multiplies exposure (public repo indexed, historical commits searchable).
- A-0065 — Anthropic credit monitor is reactive not proactive; 15-min detection latency AFTER the failure.
- A-0064 — Redis unbounded memory on a 4 GB VPS shared with `aspirehub` project — one runaway = silent failed writes.
- A-0062 — visual-backup prune not firing; ~113 days to disk exhaustion under current low load, ~48 days when BB recovers.

## What blocks SaaS

- A-0032 (isolation) + A-0060 (dashboard RBAC) + A-0028 (dual config layout) + A-0080 (SaaS layer half-built) + A-0073 (affiliate emission path unknown).
- A-0080 downgrades S1 → S2 this session because tenants/tenant_niches populated (was empty in Phase 3). Real gap now: application-layer per-request session context + dashboard RBAC + per-tenant quota — all L-XL effort.

## What is deletable

- ~2.7 GB disk: mostly `.backups/visuals/` stale files (blocked by A-0062 prune-fix landing first) + old `.sql.gz` files.
- ~70 LOC no-blockers: 6 ruff auto-fixable dead imports, libx265 dead branch, 4 SharePoint legacy refs, orphan systemd unit files.
- Nested `genlab-core/.git` (12 MB, A-0011) — needs subject+date cross-ref first (not SHA across repos), then archive-before-delete.

## Top 5 actions in order

**Operator actions (audit cannot perform; from `OPERATOR_ACTIONS.md`):**
1. **Execute `RUNBOOK_credential_rotation.md`** — closes A-0053 + A-0025 + A-0051 + A-0052 + A-0016; also A-0032 if runbook Step 5b returns zero rows. Blocks A-0008, A-0084, and half the S2 tier. 30 minutes.
2. **Triage BB outage (A-0034)** — new mode (silent, not failing) means new diagnostic surface. Check `systemctl is-enabled genlab-pipeline-ai` first (may have been disabled).

**Fix-queue (post-rotation, per Phase 9A sequencing):**
3. **A-0083 line-exists-doesn't-fire class-fix** — one architectural fix covers pg_backup prune, visual-backup prune, and gitleaks hook enforcement. Prevents the next recurrence.
4. **A-0081 env-pinning class-fix** — one FIX file for Python/FFmpeg/Postgres pinning; likely unblocks a chunk of A-0074's collection errors.
5. **A-0074 test-suite fix** — unblocks coverage measurement, unblocks A-0049 silent-except classification, unblocks CI green/red measurement (A-0079).

---

### Register topology

- **Tier 1 (AWAITING_OPERATOR):** 4 findings blocked on rotation runbook.
- **Tier 2 (S1 FIX):** A-0044, A-0034, A-0074, A-0072.
- **Tier 3 (S1 DEFER, SaaS):** A-0080.
- **Tier 4 (S2 class-fixes):** A-0083, A-0082, A-0081.
- **Tier 5 (conditional preservation):** A-0027-interaction + A-0029 + A-0028.
- **Tier 6-9:** individual S2 fixes, S3 deletions, positives, and accepts.

Deferrals ledger (166 lines) is dispositioned in Session 11 Phase 9B → `AUDIT_A_DEFERRALS_DISPOSITION.md`.

Full per-finding evidence + blast_radius + effort in the referenced phase YAMLs (`PHASE_*_findings.yaml`). Register carries disposition + verification gate + sequencing + owner.
