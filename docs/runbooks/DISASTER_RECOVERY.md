# Disaster Recovery Runbook

> **Authoritative — codifies the procedures discovered DURING the
> 2026-06-29 incident so the next data loss does not require
> re-discovery under pressure.**

This runbook covers PostgreSQL backup inventory, recovery procedures
for the three classes of failure we have either survived or are
exposed to, known gotchas (PG-version mismatch, no PITR), and a test
procedure that validates the runbook *before* it is needed.

## 1. Backup inventory

| What | Where | When | Retention |
|---|---|---|---|
| Daily pg_dump | `/opt/genlab/.backups/genlab_YYYYMMDD_HHMM.sql.gz` | Daily 01:00 UTC = 06:30 IST | 14 days |
| Producer | `scripts/pg_backup.sh` | `genlab-pg-backup.timer` | — |
| Format | pg_dump plain SQL + COPY blocks | Gzip-compressed | — |

Verify backups exist and are recent:

```bash
ls -la /opt/genlab/.backups/genlab_*.sql.gz | tail -3
```

Most-recent file should be <30h old. If it is older, the
`genlab-pg-backup.timer` has not fired — check
`systemctl status genlab-pg-backup.timer` and the most recent
`journalctl -u genlab-pg-backup.service`.

**A weekly dry-run validates the backup actually loads** — see §6
(Test procedure). This validation is the difference between
"we have a backup" and "we have a backup that works".

## 2. Recovery — deleted blueprints (proven on 2026-06-29)

**Symptom:** Operator (or a runaway cleanup script) deleted blueprint
rows from `public.blueprints` and the schedule has gaps.

**Recovery time:** ~3 minutes for ~65 blueprints; scales linearly.

**Side effects on live DB:** ZERO during extract + TEMP-table staging.
Only the final `INSERT ... WHERE NOT IN` writes to live storage; it
is bounded to rows that do not exist in live.

### Step 1 — Extract the COPY block

A full `pg_restore -d genlab <backup>` does NOT work because of a
PG-version mismatch (see §5 Gotcha #1). The workaround is to extract
only the table's COPY block from the gzipped plain-SQL dump and
load it directly with `\copy`.

```bash
BACKUP=/opt/genlab/.backups/genlab_$(date -u -d 'today 06:30 IST' +%Y%m%d_0100).sql.gz
# … or simply pick the most-recent file:
BACKUP=$(ls -t /opt/genlab/.backups/genlab_*.sql.gz | head -n1)

gzip -dc "$BACKUP" \
    | awk '/^COPY public.blueprints/{flag=1; next} /^\\.$/{flag=0} flag' \
    > /tmp/bp_data_only.copy

wc -l /tmp/bp_data_only.copy   # expect ~1900-2000 rows on a healthy day
```

The AWK keeps everything BETWEEN the `COPY public.blueprints …` header
line and the `\.` terminator (exclusive of both). Output is
tab-separated data ready for `\copy`.

> **Portability note:** Use `gzip -dc` (POSIX). Apple `zcat` only
> handles `.Z` files and silently rejects `.gz` — do not use it.

### Step 2 — Load into a TEMP TABLE in the LIVE database (zero risk)

```bash
docker cp /tmp/bp_data_only.copy genlab-postgres:/tmp/bp_data_only.copy

cat > /tmp/restore.sql <<'SQL'
BEGIN;
CREATE TEMP TABLE blueprints_staging (LIKE public.blueprints INCLUDING ALL);
\copy blueprints_staging FROM '/tmp/bp_data_only.copy';

-- ON CONFLICT DO NOTHING is paranoid safety; the WHERE clause
-- already eliminates rows that exist in live.
INSERT INTO public.blueprints
SELECT * FROM blueprints_staging
WHERE id NOT IN (SELECT id FROM public.blueprints)
ON CONFLICT (id) DO NOTHING;

COMMIT;
SQL

docker cp /tmp/restore.sql genlab-postgres:/tmp/restore.sql
docker exec genlab-postgres psql -U genlab -d genlab -f /tmp/restore.sql
```

`TEMP TABLE` lives only for the session and writes to no persisted
storage. `WHERE id NOT IN (SELECT id FROM public.blueprints)`
guarantees no row in live is overwritten.

### Step 3 — Mark broken-media rows as DRAFTED

After restoration, the DB rows are back but the actual MP4 files at
`extra->>'visual_paths'` may have been swept by the cleanup script.
Flip those blueprints back to `DRAFTED` so the next pipeline run
re-renders them, preserving `scheduled_for`.

```bash
sudo -u genlab psql "$DATABASE_URL" -t -A -F '|' -c "
SELECT id, niche_id, extra->>'visual_paths'
FROM blueprints
WHERE status='VISUAL_READY' AND created_at < NOW() - INTERVAL '3 days'
" > /tmp/restored_bps.psv

python3 <<'PY'
import json, os
broken = []
with open('/tmp/restored_bps.psv') as f:
    for line in f:
        bp_id, niche, vp = line.rstrip('\n').split('|', 2)
        if not vp:
            broken.append(bp_id); continue
        try:
            paths = json.loads(vp) if vp.startswith('[') else [vp]
        except json.JSONDecodeError:
            broken.append(bp_id); continue
        if not any(p and os.path.exists(p) for p in paths if p):
            broken.append(bp_id)
with open('/tmp/broken_bp_ids.txt', 'w') as f:
    for i in broken: f.write(i+'\n')
PY

IDS=$(awk '{printf "'"'"'%s'"'"',", $0}' /tmp/broken_bp_ids.txt | sed 's/,$//')
sudo -u genlab psql "$DATABASE_URL" -c "
UPDATE blueprints SET status = 'DRAFTED', updated_at = NOW()
WHERE id IN ($IDS)"
```

DRAFTED-with-scheduled is a supported state per `pre_download_dedup.py`
comments: pipelines will re-render these on their next cycle.
Publishers skip non-VISUAL_READY rows automatically.

### Step 4 — Cleanup

```bash
docker exec genlab-postgres rm -f /tmp/bp_data_only.copy /tmp/restore.sql
rm -f /tmp/bp_data_only.copy /tmp/restored_bps.psv /tmp/broken_bp_ids.txt
```

## 3. Recovery — corrupted database

**Symptom:** Postgres fails to start, or queries return errors
indicating block-level corruption (`could not access status of
transaction`, `invalid page header`, etc.).

**Recovery time:** ~10-30 minutes depending on database size.

### Step 1 — Stop the dashboard + pipeline services

```bash
sudo systemctl stop genlab-dashboard genlab-publisher \
    genlab-auto-approver genlab-engagement-poller
```

This prevents new writes against the (about-to-be-replaced) DB.

### Step 2 — Snapshot the corrupted state

Even when the DB is corrupted, preserving the volume gives a forensic
trail. Tag the existing volume so it is not auto-purged:

```bash
docker stop genlab-postgres
docker rename genlab-postgres genlab-postgres-corrupted-$(date -u +%Y%m%d_%H%M)
```

### Step 3 — Bring up a fresh container from the most-recent backup

```bash
BACKUP=$(ls -t /opt/genlab/.backups/genlab_*.sql.gz | head -n1)
echo "Restoring from $BACKUP"

# Spin up a new postgres container with a fresh volume
docker run -d --name genlab-postgres \
    -e POSTGRES_DB=genlab \
    -e POSTGRES_USER=genlab \
    -e POSTGRES_PASSWORD="$DB_PASS" \
    -p 5432:5432 \
    -v genlab-pg-data-new:/var/lib/postgresql/data \
    postgres:16  # match the major version expected by the schema

# Wait for it to accept connections
until docker exec genlab-postgres pg_isready -U genlab; do sleep 1; done

# pg_restore won't work cross-version (see Gotcha #1) but a
# pg_dump-plain file can be loaded with psql. Strip the pg17
# `\restrict` token first; psql ≥16 will reject unknown
# directives.
gzip -dc "$BACKUP" \
    | sed '/^\\restrict /d; /^\\unrestrict /d' \
    | docker exec -i genlab-postgres psql -U genlab -d genlab
```

### Step 4 — Verify + restart services

```bash
# Sanity row counts
docker exec genlab-postgres psql -U genlab -d genlab -c "
SELECT 'blueprints' AS t, count(*) FROM blueprints
UNION ALL SELECT 'stories', count(*) FROM stories
UNION ALL SELECT 'publishing_analytics', count(*) FROM publishing_analytics;"

# If counts look right, restart
sudo systemctl start genlab-dashboard genlab-publisher \
    genlab-auto-approver genlab-engagement-poller
```

## 4. Recovery — VPS dead (Hetzner reachable but VPS unrecoverable)

**Symptom:** VPS is down at the provider level (network, kernel
panic, disk failure) and a reboot does not bring it back.

**Recovery time:** ~2-4 hours (manual reprovision; no automation).

> **Warning:** There is currently NO offsite backup. The 14-day
> `.backups/genlab_*.sql.gz` files live on the VPS itself. If the VPS
> disk is unrecoverable, the database is gone.
>
> **Required improvement (out of scope for this runbook):** push the
> daily backup to S3 or Hetzner Object Storage. Estimate: 30min PR
> after the script choice (rclone vs awscli).

If the VPS is recoverable but VPS-level (e.g. broken kernel, can
rescue-mode boot):

1. Boot Hetzner rescue system (Hetzner UI → Rescue → Activate)
2. Mount the system disk: `mount /dev/sda1 /mnt`
3. Copy `/mnt/opt/genlab/.backups/genlab_*.sql.gz` off-box via
   `scp` / `rsync`
4. Reinstall the OS from a Hetzner image
5. Re-bootstrap: clone the repo, run `scripts/deploy.sh` (which
   handles env, systemd installs, schema migrations)
6. Restore the most-recent backup using §3 Step 3

## 5. Known recovery gotchas

### Gotcha #1 — PG version mismatch on full pg_restore

The backup file was produced by `pg_dump` from PG 17 (binary version
of the source Postgres). The current production container is PG 16.
A direct `pg_restore -d genlab <backup>` errors out:

```
ERROR:  unrecognized configuration parameter "transaction_timeout"
```

The COPY-extract workaround in §2 sidesteps this entirely because the
COPY block is portable across PG major versions.

### Gotcha #2 — PointInTimeRecovery is NOT enabled

`archive_mode=off` and `archive_command=disabled`. The 01:00 UTC daily
snapshot is the ONLY recovery point. A second data-loss in the same
day = up to 24 hours of work gone.

**Required improvement:** enable WAL archiving for PITR. Estimate
4-8h PR (`archive_mode=on`, `archive_command='cp %p
/opt/genlab/.backups/wal/%f'`, `pg_basebackup` every 6h).

### Gotcha #3 — DRAFTED-with-scheduled is intentional

Per `pre_download_dedup.py` comments: "DRAFTED and SCORED are
deliberately NOT blocking here: those statuses mean 'render failed,
retry next run' or 'scored but not yet drafted'." During recovery,
do NOT delete or `scheduled_for=NULL` on DRAFTED rows — the pipeline
re-renders them on the next cycle and the publisher picks them up
once they reach VISUAL_READY again.

### Gotcha #4 — pg_dump password gate at the file top

PG 17 backups have a `\restrict <token>` line at the top
(password-required gate). Stripping it with `sed` (as in §3 Step 3)
is safe for plain-SQL loading. If you ever DO need to keep it,
`\unrestrict <token>` in your psql session first.

### Gotcha #5 — `scheduled_for` rows are sacred per cleanup_safety.md

Recovery procedures MAY flip status to DRAFTED on rows with
`scheduled_for` set — that is the documented force-true override.
Recovery procedures MUST NOT delete those rows. See
`.claude/rules/cleanup_safety.md`.

## 6. Test procedure — validate this runbook works

Running through the actual recovery procedures requires either a
real outage or a maintenance window. Instead, the following automated
test exercises the §2 COPY-extract path against the most-recent
backup on a weekly schedule, surfacing any breakage long before the
next crisis.

**Script:** `scripts/test_backup_restore.sh`
**Timer:** `deploy/systemd-phase2/genlab-backup-test.timer`
(Sundays 22:30 UTC = Mondays 04:00 IST)
**Failure surface:** `OnFailure=genlab-service-failure-alert@%n.service`
writes a CRITICAL row to `pipeline_alerts` →
`CriticalAlertsBanner` on the Mission Control dashboard.

Run manually any time:

```bash
sudo -u genlab /opt/genlab/scripts/test_backup_restore.sh
# Or, on dev / CI / without a backup directory:
/opt/genlab/scripts/test_backup_restore.sh --self-test
```

Exit codes:

- `0` = most-recent backup is valid; blueprints COPY block extracts
  + loads into TEMP table with >0 rows
- `1` = backup missing, empty, or 0-row blueprints table
- `2` = unexpected error

Pin tests at
`genlab-core/tests/deploy/test_backup_restore_dry_run.py` guard the
script's contract so a future PR cannot quietly break the validator.

## 7. Related documents

- `backup-recovery-procedure-2026-06-29.md` (memory) — the original
  postmortem this runbook was extracted from
- `.claude/rules/cleanup_safety.md` — "scheduled posts are sacred"
  rule, relevant during the DRAFTED flip step
- `docs/architecture/stage_context_population_audit.md` —
  unrelated to recovery but lives in the same Phase-1 hardening
  sprint
- `scripts/pg_backup.sh` — the daily backup producer
- `scripts/test_backup_restore.sh` — the weekly validator
- `deploy/systemd-phase2/genlab-pg-backup.{service,timer}` — backup unit
- `deploy/systemd-phase2/genlab-backup-test.{service,timer}` — validator unit
