# RUNBOOK — Credential Rotation + Role Switch + `.env` Lockdown

**Source:** operator-supplied 2026-07-29 (Session 6 end).
**Status:** OPERATOR ACTION — makes prod changes; NOT part of the read-only audit.
**§0.5 note:** the literal `genlab_***` in this document is a redaction marker. Anywhere the runbook says `genlab_***`, the operator substitutes the actual live password value. This file is committable to `.audit/` only in redacted form.
**Audit's role:** archive the runbook, verify state post-execution, update `OPERATOR_ACTIONS.md`. **Not to execute.**

**Closes in one maintenance window:** A-0053 (live password rotation), A-0032/A-0054 (RLS role switch), A-0051 (world-readable `.env`). Optionally A-0057 (world-readable cred files).
**Estimated time:** 30–45 min. **Estimated downtime:** one dashboard restart + brief pipeline pause; no data migration.
**Prereq:** SSH to VPS as sudo-capable user; reach Postgres as an admin role.

---

## Why now (one-paragraph justification)

The audit hash-confirmed `genlab_***` is the **current live** prod DB password (A-0053), it sits in **10+ commits of a public source repo** (A-0055, a post-BFG re-introduction), and the prod `.env` holding it is **mode 644, world-readable to any shell user on the box** (A-0051). Rotation has been PENDING 24h+. Because the app currently connects as the `genlab` superuser role (`rolsuper=t, rolbypassrls=t`) and the correctly-scoped `genlab_app` role **already holds GRANTs on all 45 tables** (A-0054), the password change and the RLS-fixing role swap are the same one-line DSN edit — do them together.

---

## ⚠️ Pre-flight — resolve the two-Postgres ambiguity FIRST (A-0058)

Two Postgres instances exist: Docker on `5432` (app uses this, per DATABASE_URL) and native on `5433` (purpose unknown). Do not proceed until you confirm which one `DATABASE_URL` points at and whether 5433 holds a copy of the data.

```bash
ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
  python3 - <<PY
import os,urllib.parse as u
d=u.urlparse(os.environ["DATABASE_URL"])
print("app DB -> host:",d.hostname,"port:",d.port,"user:",d.username,"db:",d.path.lstrip("/"))
PY'

ssh genlab-prod 'sudo ss -tlnp | grep -E ":543[23]"'
ssh genlab-prod 'psql -h 127.0.0.1 -p 5433 -U genlab -d genlab -tAc "select 1" 2>&1 | head -3'
```

**Decision:**
- port 5432 = app; 5433 stale → rotate 5432 per this runbook; add OPERATOR_ACTIONS row to investigate/shut-down 5433 (S1 if 5433 holds a data copy with the old password).
- port 5433 = app → substitute `-p 5433` everywhere below; treat 5432 as the instance to investigate.
- 5433 bound to `0.0.0.0` → immediate escalation; firewall it before doing anything else.

Record the answer in `.audit/OPERATOR_ACTIONS.md` before continuing. Steps below assume **5432 is the app instance**.

---

## Step 0 — Snapshot current state (rollback insurance)

```bash
ssh genlab-prod '
  ts=$(date +%Y%m%d-%H%M%S)
  sudo cp -a /opt/genlab/.env /root/.env.rotation-backup-$ts
  sudo chmod 600 /root/.env.rotation-backup-$ts
  echo "backup: /root/.env.rotation-backup-$ts (mode 600, root-only)"
  set -a; . /opt/genlab/.env; set +a
  psql "$DATABASE_URL" -tAc "select current_user, rolsuper, rolbypassrls"
'
```

Expected: `genlab|t|t`. If already `genlab_app|f|f`, someone partially did this — STOP and reassess.

> Backup lands in `/root/`, NOT `/opt/genlab/` — the repo root is genlab's `$HOME` (A-0013). Keep rotation artifacts out of the repo tree entirely.

---

## Step 1 — Generate new password (never printed, never in shell history)

```bash
ssh genlab-prod '
  umask 077
  NEWPW=$(openssl rand -base64 30 | tr -d "/+=" | cut -c1-32)
  printf "%s" "$NEWPW" | sudo tee /root/.newpw >/dev/null
  sudo chmod 600 /root/.newpw
  echo "new password generated; sha256 prefix: $(printf "%s" "$NEWPW" | sha256sum | cut -c1-12)"
  unset NEWPW
'
```

Record the sha256 prefix in `OPERATOR_ACTIONS.md` — Phase 5's rotation-verification gate compares it against the old `genlab_***` hash (`c7b89bffef3a`) to prove they differ.

---

## Step 2 — Change password on `genlab_app` role (and kill leaked `genlab_***` on `genlab` too)

```bash
ssh genlab-prod '
  NEWPW=$(sudo cat /root/.newpw)
  sudo -u postgres psql -p 5432 -d genlab <<SQL
ALTER ROLE genlab_app WITH PASSWORD '"'"'$NEWPW'"'"';
ALTER ROLE genlab     WITH PASSWORD '"'"'$NEWPW'"'"';
SQL
  unset NEWPW
  echo "passwords updated for genlab_app and genlab"
'
```

> Preferred: two separate passwords for `genlab_app` and `genlab` (app vs admin role should not share a secret). Generate a second `openssl rand` value for `genlab` and run two `ALTER ROLE` statements. Sharing is acceptable short-term only; note as follow-up in OPERATOR_ACTIONS.

---

## Step 3 — Update `.env`: new password AND username → `genlab_app`, then lock down

```bash
ssh genlab-prod '
  NEWPW=$(sudo cat /root/.newpw)
  sudo env NEWPW="$NEWPW" python3 - <<PY
import os, re, urllib.parse as u
p="/opt/genlab/.env"
src=open(p).read()
m=re.search(r"^DATABASE_URL=(.*)$", src, re.M)
assert m, "DATABASE_URL not found in .env"
old=m.group(1).strip().strip("\x22\x27")
d=u.urlparse(old)
newpw=os.environ["NEWPW"]
netloc=f"genlab_app:{u.quote(newpw)}@{d.hostname}:{d.port or 5432}"
newdsn=u.urlunparse((d.scheme, netloc, d.path, d.params, d.query, d.fragment))
src2=re.sub(r"^DATABASE_URL=.*$", "DATABASE_URL="+newdsn, src, flags=re.M)
open(p,"w").write(src2)
print("DATABASE_URL rewritten: user=genlab_app, port=", d.port or 5432)
PY
  unset NEWPW
  sudo chown genlab:genlab /opt/genlab/.env
  sudo chmod 600 /opt/genlab/.env
  echo "post-edit mode: $(stat -c %a /opt/genlab/.env)  owner: $(stat -c %U /opt/genlab/.env)"
'
```

Expected final line: `post-edit mode: 600  owner: genlab`.

> If `.env` uses different variable name(s), grep KEY NAMES only (never the matched line): `sudo grep -oE '^[A-Z_]+=' /opt/genlab/.env | grep -iE 'DB|POSTG|DATABASE'`.

---

## Step 4 — Restart consumers (order matters)

Always-on (restart now):
```bash
ssh genlab-prod '
  sudo systemctl restart \
    genlab-dashboard.service \
    genlab-engagement-poller.service \
    genlab-engagement-worker.service \
    genlab-quota-monitor.service \
    genlab-webhook.service
  sleep 3
  for s in dashboard engagement-poller engagement-worker quota-monitor webhook; do
    printf "%-22s %s\n" "$s" "$(systemctl is-active genlab-$s.service)"
  done
'
```
Expected: all five `active`.

Timer-driven (self-update on next fire). Note: the 4 already-FAILED units from A-0044 will still be failed for their own reasons — rotation does not fix those.

If Docker Compose runs any of these, `docker compose up -d --force-recreate` the relevant services.

---

## Step 5 — THE VERIFICATION GATE (do not skip)

### 5a — Connectivity: app logs in as `genlab_app` with new password
```bash
ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
  psql "$DATABASE_URL" -tAc "select current_user, rolsuper, rolbypassrls"'
```
**PASS =** `genlab_app|f|f`. FAIL → roll back.

### 5b — Isolation FIRES (the A-0032 gate — this is the whole point)
Cross-niche read WITHOUT setting session niche variable. Under working RLS must return **zero rows**.

```bash
ssh genlab-prod 'set -a; . /opt/genlab/.env; set +a; \
  psql "$DATABASE_URL" -tAc \
  "SELECT niche_id, COUNT(*) FROM blueprints GROUP BY niche_id ORDER BY niche_id"'
```

- **PASS =** zero rows (RLS filters; success condition for A-0032).
- **FAIL =** rows returned for multiple niches. **Application code does not `SET LOCAL app.niche_id` per request.** DSN swap alone removed superuser bypass but did not create isolation. **A-0032 stays OPEN as a code finding** — do NOT declare closed. Rotation + `.env` lockdown still stand; only isolation half is open.

> Why 5b can fail: RLS policies filter on `current_setting('app.niche_id')`. If app never sets that variable, a non-superuser sees rows the policy would filter because the predicate evaluates against an unset variable. GRANTs (A-0054) prove role CAN touch tables; only 5b proves isolation FIRES.

### 5c — App smoke test
```bash
ssh genlab-prod 'curl -fsS -o /dev/null -w "dashboard: %{http_code}\n" http://127.0.0.1:5151/ || echo "dashboard check failed"'
ssh genlab-prod 'journalctl -u genlab-dashboard.service --since "2 min ago" --no-pager | tail -15'
```
Look for: successful DB queries, no `permission denied for table`. Permission denied = missing GRANT that A-0054 didn't catch — grant explicitly, note the gap.

---

## Step 6 — Invalidate leaked credential everywhere it still lives

```bash
# A-0025: remove literal from source scripts
ssh genlab-prod 'grep -rIn "genlab_***" /opt/genlab --include="*.sh" --include="*.py" 2>/dev/null | grep -v "\.git/"'
# ^ expected AFTER cleanup: empty. Any hit is a remaining live-credential-in-source site.

# A-0016/A-0052: 14 .env.bak.* files — contain OLD password, are now dead keys, but many are 644
ssh genlab-prod 'ls -la /opt/genlab/.env.bak.* 2>/dev/null; \
  echo "--- niche .env.bak files ---"; \
  sudo find /opt/genlab -maxdepth 2 -name ".env.bak.*" -printf "%m %p\n"'
```
Recommendation: delete the .env.bak files (backups of a now-rotated secret). Retained ones → chmod 600.

**Source-repo history (the actual public exposure):** rotation makes the 10+ commits harmless *for that password*. You do NOT need to rewrite history once the credential is dead. But:
- **Make the repo private** (three same-class near-misses this run).
- Add **secret-scan CI gate** (A-0055's missing control). Pre-commit + CI `gitleaks`. This is what would have caught the post-BFG return of `genlab_***`.

---

## Step 7 — Clean up rotation artifacts

```bash
ssh genlab-prod '
  sudo shred -u /root/.newpw 2>/dev/null || sudo rm -f /root/.newpw
  # keep /root/.env.rotation-backup-* until 24h clean, then shred
  ls -la /root/.env.rotation-backup-* 2>/dev/null
'
```

Do NOT leave `/root/.newpw`, do NOT move backup into repo tree.

---

## Rollback

```bash
ssh genlab-prod '
  latest=$(ls -t /root/.env.rotation-backup-* | head -1)
  sudo cp -a "$latest" /opt/genlab/.env
  sudo chown genlab:genlab /opt/genlab/.env
  sudo chmod 600 /opt/genlab/.env    # 644 was itself a finding — keep 600 even on rollback
  sudo systemctl restart genlab-dashboard genlab-engagement-poller \
    genlab-engagement-worker genlab-quota-monitor genlab-webhook
  echo "rolled back to $latest"
'
```
Rollback restores service but re-opens the `genlab_***` exposure; rotation must be re-attempted promptly.

---

## What this closes and doesn't

**Closes (with Step 5 gates):**
- **A-0053** — password rotated (5a passes, sha256 differs from `genlab_***`)
- **A-0051** — `.env` mode 600, owner genlab (Step 3 final line)
- **A-0032** — **only if 5b returns zero rows.** If 5b returns rows, A-0032's isolation half stays OPEN.
- **A-0025** — hardcoded-password sites cleaned (Step 6 grep returns empty)

**Explicitly does NOT close:**
- **A-0044** (4 FAILED units — separate failure modes)
- **A-0034** (BB 8-day outage — unrelated to credentials)
- **A-0058** (5433 instance — pre-flight decides)
- **A-0055** (missing secret-scan CI — install in Step 6)
- **A-0056** (FB/META identical secrets — separate dedup)
- shared-password shortcut if taken — split as follow-up

---

## Post-run: audit trail updates required

Record in `.audit/OPERATOR_ACTIONS.md`:
- **Row #1 (rotation): DONE**, date/time, new sha256 prefix, 5a result
- **Row #3 (role switch): DONE if 5b=zero-rows**, else **OPEN — app does not SET LOCAL**, with 5b output attached
- **New follow-ups:** 5433 disposition, secret-scan CI, `.env.bak` cleanup, FB/META dedup, role-password split

Then run `.audit/`-side §0.5 self-scan (`grep -rIn 'genlab_***' .audit/` must return exit 1 — redaction markers only). Do NOT paste the new password anywhere; only its 12-char sha256 prefix.

Phase 5's rotation-verification injected item then has evidence to consume; it should confirm state, not re-perform.
