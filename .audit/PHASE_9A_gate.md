# Phase 9A — Gate Outcomes

**Session:** 10 (Phase 9A)
**Date:** 2026-07-30

## Gate probe results (v1.5 Pre-Session-9A)

### 1. §0.5 scrub verify — PASS
```
grep -c 'genlab_***' -r .audit/  → 0 in every file
```

### 2. Rotation status — NOT DONE
```
live prod DB password hash prefix: c7b89bffef3a
old-literal hash                 : c7b89bffef3a
STATUS: rotation NOT DONE
```
**A-0053 stays S1.** Elapsed since first disclosure (Phase 3, 2026-07-29): **~30+ hours** and unrotated. Runbook `RUNBOOK_credential_rotation.md` remains queued and un-executed.

### 3. Runbook Step 5b outcome — RUNBOOK NOT EXECUTED
```
SELECT current_user, rolsuper, rolbypassrls → genlab|t|t
```
Still connecting as superuser role with `bypassrls=t`. Runbook Step 5b not applicable yet (needs Steps 2-3 first). **A-0032/A-0060 stay PENDING** with runbook as their named trigger.

### 4. OPERATOR_ACTIONS state snapshot (2026-07-30)

| Row | Action | Status | Change since last session |
|---|---|---|---|
| 1 | Rotate `genlab_***` prod DB password | **PENDING** | unchanged 30+ hours |
| 2 | Investigate BB publish outage | **CHANGED** — pipeline no longer FAILED; 0 attempts in last 2 days (silent, not failing) | see §5 below |
| 3 | Plan/execute `genlab` → `genlab_app` role switch | **PENDING** — runbook bundled with #1 | unchanged |

### 5. Shell quiescence — CONFIRMED at session start

## S1 re-verification (fresh commands this session)

Prior-artifact posture (§0.8) applied. Several findings materially changed between Phase 3-8 (2026-07-29) and this Session 10 gate (2026-07-30):

### A-0044 CHANGED (failed services)
```
Phase 4 failed list:  genlab-pipeline-ai, genlab-pipeline-movies, genlab-post-deploy-verify, genlab-strategist  (4)
Session 10 failed:    genlab-pipeline-sports, genlab-post-deploy-verify, genlab-strategist                    (3)
```
- BB pipeline (`-ai`) recovered
- SR pipeline (`-movies`) recovered
- **CW pipeline (`-sports`) newly failed**
- Post-deploy-verify + strategist still failed

### A-0033 MATERIALLY CHANGED
```
Phase 3:       auto_approval_calibration = 0 rows   ; strategist_reports = 0 rows
Session 10:    auto_approval_calibration = 305 rows ; strategist_reports = 5 rows
```
Calibration path is NO LONGER DARK. Ratchet has data. A-0033 in its Phase-3 form is superseded — see `AUDIT_A_RETRACTED.md`. New status = calibration active; strategist ran successfully at least 5 times despite systemd state showing failed (likely fixed and re-fired, then failed again). New finding: `strategist_reports` count vs systemd state is inconsistent — worth an audit sub-question.

### A-0038 MATERIALLY CHANGED
```
Phase 3:       tenants = 0 rows      ; tenant_niches = 0 rows
Session 10:    tenants = 1 row       ; tenant_niches = 5 rows
```
SaaS multi-tenant tables are POPULATED. Likely a single-tenant seed matching the 5 channels. A-0038 half retracts (empty-tenant claim), other half stands (product_embeddings + monetization empties still true — not re-checked but no reason to change).

### A-0034 CHANGED
```
Phase 3:       BB 32 attempts / 8 days / 0 PUBLISHED
Session 10:    BB rows for last 4 days: 2026-07-28|4|0, 2026-07-27|4|0, 2026-07-26|4|0
               (no rows for 2026-07-29 or 2026-07-30)
```
Attempt rate went 4/day → **0/day since 2026-07-28**. Combined with A-0044 recovery of `pipeline-ai`, BB pipeline went from "failing" to "silent" — either operator-disabled or early-exit before writing publishing_analytics. Different failure mode than Phase 3 documented. Root cause dark (A-0050 journal-rotation still applies).

### A-0032 confirmed (RLS bypass)
```
SELECT COUNT(DISTINCT niche_id) FROM blueprints → 5
```
Should be 1 if RLS filtered. Returned 5 → bypass empirically re-confirmed.

### A-0051 confirmed (`.env` mode 644)
```
stat -c '%a %U' /opt/genlab/.env → 644 genlab
```

### A-0072 confirmed (hardcoded creds still in scripts)
```
grep -c genlab_*** /opt/genlab/scripts/pg_backup.sh         → 1
grep -c genlab_*** /opt/genlab/scripts/verify_policy_block_l1.sh → 3
```
4 total occurrences, matches Phase 5. Rotation runbook Step 6 required.

### A-0074 confirmed (test suite unrunnable)
```
uv run pytest --collect-only -q → 10783 tests collected, 77 errors in 7.55s
```
Same errors as Phase 7. Suite still blocks coverage measurement.

### A-0073 confirmed (backend abstraction writer still present)
```
grep -n 'pg\.create.*affiliate' link_tracker.py → 144: pg.create("affiliate_clicks", record)
```
Pre-Session-9 correction holds; A-0073 stays S2/investigate.

### A-0011 confirmed (nested .git still present)
```
ls -d genlab-core/.git → genlab-core/.git
```
No cleanup happened.

### A-0080 partially updated
SaaS layer is now MORE built than Phase 8 concluded: tenants/tenant_niches populated (1+5 rows), calibration ratchet has data. Dashboard RBAC + role switch + dual layout + affiliate-emission-path questions still open. A-0080 downgrades from S1 to S2 in the register.

## Summary of gate-driven register adjustments

| Original | Adjustment |
|---|---|
| A-0033 (S1, calibration + strategist dark) | **PARTIAL RETRACTION** — calibration active now (305 rows); strategist ran 5x. Systemd-vs-DB inconsistency = new sub-question. |
| A-0038 (S2, empty tenant tables) | **PARTIAL RETRACTION** — tenants populated. Monetization empties + product_embeddings survive. |
| A-0044 (S1, 4 failed) | **AMENDED** — 3 failed now; BB + SR recovered; CW newly failed. |
| A-0034 (S1, BB 32/8/0) | **AMENDED** — mode changed from "failing" to "silent" (0 attempts last 2 days). Root cause still dark. |
| A-0080 (S1, SaaS half-built) | **DOWNGRADED to S2** — more built than assessed; still open: dashboard RBAC, role switch, dual layout, affiliate emission-path. |
