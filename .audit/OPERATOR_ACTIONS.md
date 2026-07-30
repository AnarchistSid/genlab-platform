# AUDIT A — Operator Actions (human-required changes)

This file is for **changes** the audit cannot perform. The audit is read-only; these three
items each have a measured, verified basis from earlier phases. Polishing findings further
does not substitute for them.

Status conventions: `PENDING` | `IN_PROGRESS` | `DONE` | `BLOCKED`. Each session that touches
one of these items updates the row.

**2026-07-29 update:** operator supplied `.audit/RUNBOOK_credential_rotation.md` — a combined runbook that closes rows #1 + #3 (rotation + role switch + `.env` lockdown) in one maintenance window. Actions #1 and #3 now point at the runbook; the runbook's Step 5 gates define the audit-side verification. Row #2 (BB outage) is unrelated to the runbook.

| # | Action | Owner | Target date | Status | Verifying evidence when complete |
|---|---|---|---|---|---|
| 1 | **Rotate the prod DB password `genlab_***`.** Live prod credential (hash-confirmed Phase 3, `c7b89bffef3a`), sits in 10+ commits of a **public** source repo. Execute via `RUNBOOK_credential_rotation.md`. Bundles Row #3 in the same window per Phase 5 A-0054. | operator | this week (2026-08-05) | **PENDING** as of 2026-07-29 | Runbook Step 5a passes (`current_user=genlab_app, rolsuper=f, rolbypassrls=f`); Step 6 grep for old literal in `/opt/genlab/` returns empty; new password sha256 prefix recorded here ≠ `c7b89bffef3a`. |
| 2 | **Investigate Blackbox Brief publish outage.** Phase 3 measured 32 publish attempts / 8 days / **0 PUBLISHED** (A-0034). Compounded by strategist calibration path dark (A-0033 + A-0024). Manual triage: is BB's pipeline failing, or is a guard correctly refusing bad output? Journal rotation (A-0050) killed the traceback evidence — root-cause hunt now needs a live fresh failure to catch. | operator | 2026-08-01 | **PENDING** as of 2026-07-29 | Phase 4 A-0034 reconstruction + a subsequent day showing PUBLISHED > 0. |
| 3 | **Plan the `genlab` → `genlab_app` role switch.** App connects as superuser role with `rolbypassrls=t` (A-0032); 24 RLS policies silently no-op. Bundled with Row #1 in `RUNBOOK_credential_rotation.md`. **Design pre-work COMPLETE** per Phase 5 A-0054 (all 45 tables already have GRANTs for `genlab_app`). | operator (via runbook) | this week (2026-08-05) | **Runbook Step 5b:** cross-niche `SELECT niche_id, COUNT(*) FROM blueprints GROUP BY niche_id` as `genlab_app` returns **0 rows** without `SET LOCAL app.niche_id`. If Step 5b returns rows, Row #3 STAYS OPEN — the DSN swap removed the bypass but the app doesn't set the session variable per request; that becomes a code-fix finding for Phase 9. |

## Repo visibility recommendation (out of audit scope, in ops scope)

Setting `github.com/AnarchistSid/genlab-platform` **private** deletes the entire S0-publication
pathway for a click. Three same-class near-misses of secret exposure into `.audit/` in this run
justify it. The source-repo history exposure of `genlab_***` becomes reader-limited too.
**Rotation of the DB password stays mandatory regardless of visibility.** Not filed as an
action row because it's a policy choice, not a fix; noted for the operator.

## What Phase 9 will NOT do

- Rotate credentials (action #1) — this file's row terminates it. Runbook Step 5a is the gate.
- Restart failed pipelines (action #2) — the audit measures; the operator diagnoses and fixes.
- Perform the role switch (action #3) — bundled into Row #1's runbook now; Phase 9 verifies via Step 5b.

The audit's role is to keep these visible and to verify state on each new session.

## Post-runbook verification checklist (audit-side, when operator signals "runbook executed")

The audit will run these READ-ONLY commands to confirm the runbook took effect. Nothing here modifies prod.

```
# Row #1 rotation verification (must PASS)
ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
  printf "%s" "$DATABASE_URL" | sed -n "s|.*://[^:]*:\([^@]*\)@.*|\1|p" | sha256sum | cut -c1-12'
# expect: != c7b89bffef3a (the old literal hash)

# Row #3 isolation-fires verification (must PASS for Row #3 to close)
ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
  psql "$DATABASE_URL" -tAc "SELECT current_user, rolsuper, rolbypassrls"'
# expect: genlab_app|f|f

ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
  psql "$DATABASE_URL" -tAc "SELECT niche_id, COUNT(*) FROM blueprints GROUP BY niche_id ORDER BY niche_id"'
# expect: 0 rows returned. If rows returned, Row #3 STAYS OPEN as a code-side finding

# .env lockdown verification (part of Row #1)
ssh genlab-prod 'stat -c "%a %U" /opt/genlab/.env'
# expect: 600 genlab

# Hardcoded-source cleanup (part of Row #1 Step 6)
ssh genlab-prod 'grep -rIn "genlab_***" /opt/genlab --include="*.sh" --include="*.py" 2>/dev/null | grep -v "\.git/" | wc -l'
# expect: 0

# .env.bak.* files scrubbed or 600
ssh genlab-prod 'find /opt/genlab -maxdepth 2 -name ".env.bak.*" -printf "%m %p\n"'
# expect: either empty, or all lines start with 600
```

After running these, update Row #1 and Row #3 status to `DONE` or `OPEN — <reason>` with the observed evidence pasted. Then run `.audit/` §0.5 self-scan.

## New follow-ups spawned by the runbook (add rows here when runbook executes)

The runbook explicitly generates these downstream items:
- **Row #4 (new):** 5433 native Postgres instance disposition (A-0058 pre-flight decision — investigate / shut-down / firewall). Add when pre-flight ran and answer known.
- **Row #5 (new):** Install secret-scan CI (`gitleaks` pre-commit + CI) — A-0055's missing control. Would have caught the post-BFG `genlab_***` re-introduction.
- **Row #6 (new):** Delete or 600 the 14 `.env.bak.*` files (A-0016/A-0052) — dead keys after rotation but 7 are 644 world-readable.
- **Row #7 (new):** Dedup FB_APP_SECRET / META_APP_SECRET (A-0056) — same value under two confusable names.
- **Row #8 (new, if applicable):** Split shared password between `genlab` (admin) and `genlab_app` (app) roles — if runbook Step 2 took the shared-password shortcut.
- **Row #9 (new, if 5b fails):** Wire `SET LOCAL app.niche_id` per request in application code — A-0032's isolation half.
