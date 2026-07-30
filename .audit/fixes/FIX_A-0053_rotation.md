# FIX_A-0053 — Rotate live prod DB password (bundle A-0025 + A-0032 + A-0051 + A-0052 + A-0016)

## Closes
Live prod DB password `genlab_***` (hash `c7b89bffef3a`) sits in 10+ public source-repo commits and in a world-readable `.env`. Un-rotated 30+ hours after disclosure.

## Exact change
Execute `.audit/RUNBOOK_credential_rotation.md` end-to-end. The runbook is idempotent, has rollback, and bundles:
- Rotation on `genlab_app` role (Step 2)
- `DATABASE_URL` username swap `genlab` → `genlab_app` (Step 3)
- `.env` chmod 600 + chown genlab:genlab (Step 3 final line)
- Consumer restart (Step 4: dashboard, engagement-poller, engagement-worker, quota-monitor, webhook)
- Verification Steps 5a/5b/5c (see below)
- Source cleanup of hardcoded literals (Step 6)

## Verification gate (execution evidence, not "code looks correct")
Named evidence — **all three must pass**:

1. **Hash comparison** (A-0053 verified rotated):
   ```bash
   ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
     printf "%s" "$DATABASE_URL" | sed -n "s|.*://[^:]*:\([^@]*\)@.*|\1|p" | sha256sum | cut -c1-12'
   ```
   Expected: **≠ `c7b89bffef3a`**

2. **Runbook Step 5a — connectivity + role** (A-0032 half):
   ```bash
   ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
     psql "$DATABASE_URL" -tAc "select current_user, rolsuper, rolbypassrls"'
   ```
   Expected: `genlab_app|f|f`

3. **Runbook Step 5b — isolation FIRES** (A-0032 the other half — HARDEST GATE):
   ```bash
   ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
     psql "$DATABASE_URL" -tAc "SELECT niche_id, COUNT(*) FROM blueprints GROUP BY niche_id"'
   ```
   - Expected PASS: **0 rows** (RLS filters; A-0032 CLOSES)
   - Alternative outcome: rows returned = A-0032's isolation half STAYS OPEN as new code finding "app doesn't `SET LOCAL app.niche_id` per request" (this is the runbook's own documented failure mode; spawns FIX_A-0032b)

4. **.env lockdown** (A-0051):
   ```bash
   ssh genlab-prod 'stat -c "%a %U" /opt/genlab/.env'
   ```
   Expected: `600 genlab`

5. **Source-cleanup** (A-0025):
   ```bash
   ssh genlab-prod 'grep -rIn "genlab_***" /opt/genlab --include="*.sh" --include="*.py" 2>/dev/null | grep -v "\.git/" | wc -l'
   ```
   Expected: `0`

6. **`.env.bak.*` cleanup or 600** (A-0052 + A-0016):
   ```bash
   ssh genlab-prod 'find /opt/genlab -maxdepth 2 -name ".env.bak.*" -printf "%m %p\n"'
   ```
   Expected: either empty (deleted) or all lines start with `600`

## Sequencing
- Must land after: none (this IS first)
- Blocks: A-0025, A-0032, A-0051, A-0052, A-0016, A-0084 (all bundled), A-0008 (git gc after this)
- OPERATOR_ACTIONS dependency: row #1 = this fix; row #3 = same runbook

## Blast radius if wrong
Runbook has explicit rollback (Step 7). If Step 5a fails: bad DSN or Step 2 didn't take → restore `.env` from `/root/.env.rotation-backup-*`. If Step 5b returns rows: the DB-side change is fine (role does not bypass RLS anymore); the isolation gap moves to application code where `SET LOCAL app.niche_id` is missing per-request. Documented outcome, not a runbook failure.

## Effort
S (30 minutes at a terminal per runbook estimate)

## Owner
operator (audit cannot execute per §0.1)
