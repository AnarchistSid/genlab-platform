# GenLab Production Deploy — Hetzner

Phase 1 production runs on a Hetzner CX23 (Nuremberg, `46.224.237.56`).
Phase 2 / SaaS deploy is tracked in `systemd-phase2/`.

## TL;DR — How code gets to production

**rsync via `deploy/scripts/deploy.sh`, not bare `scp`** (R-02).
The Hetzner `.git/` checkout is a reference copy and lags behind
`origin/main`. Files are synced individually by absolute path. Do
not assume `git log` on Hetzner reflects what's actually running.

```bash
# Canonical deploy — pass each repo-root-relative path explicitly.
# deploy.sh runs the post-transfer md5 round-trip and aborts before
# any service restart if a single file mismatches.
deploy/scripts/deploy.sh \
  BlackboxBrief/config/sources.yaml \
  genlab-core/src/genlab_core/pipeline/stages/video_gate.py
```

The script enforces three properties bare `scp` cannot:

1. **Explicit src→dst per file** — refuses absolute paths and
   `..` components. No flat staging dir to collide in (Cluster A's
   exact failure mode).
2. **Post-transfer checksum verify** — md5 round-trip on every
   file; mismatch aborts with exit 2 BEFORE any service restart.
3. **Audit log** — `deploy/.deploy.log` (gitignored) records what
   was sent, with checksums, plus the deploying user, host, and
   `git rev-parse HEAD`.

Bare `scp` is still possible (`deploy.sh` is just a wrapper around
rsync+ssh), but the wrapper is the on-call playbook.

## Cluster A lesson (2026-05-18) — flat staging is forbidden

The cluster A incident published a Blackbox Brief reel with SpliceReel
branding. Root cause: I scp'd multiple per-niche `niche.yaml` files to
a flat `/tmp/genlab_deploy4/` directory, each overwrote the previous,
and SpliceReel's was the last one through — then a `cp` to BB's
destination painted SR config over BB.

**Rule:** every deploy command must name both the local and remote
absolute paths. No flat staging dirs. If you need to batch many files,
build a `for` loop with explicit src→dst pairs, never a wildcard copy
into a staging dir.

## Verifying a deploy

After scp, confirm byte-equality:

```bash
LOCAL=$(md5 -q path/to/file)
REMOTE=$(ssh root@46.224.237.56 "md5sum /opt/genlab/path/to/file | awk '{print \$1}'")
[ "$LOCAL" = "$REMOTE" ] && echo OK || echo MISMATCH
```

## Pipeline timer schedule (IST)

| Niche | Timer fires |
|---|---|
| gaming | 09:30 |
| shared-ingestion | 10:30 |
| anime | 11:30 |
| publisher | 12:05 |
| insights-collector | 12:15 |
| movies | 13:30 |
| db-maintenance | 14:15 |
| sports | 15:30 |
| affiliate-scraper | 17:30 |
| ai_creators | 18:00 |
| feedback-collector | 19:00 |
| audience-collector | 20:00 |
| token-refresh | 07:30 (next day) |

Deploy **before** the first scheduled niche timer so the next run uses
the new code. If you deploy mid-day, only later niches see the change.

## Code paths on Hetzner

```
/opt/genlab/                       # repo root (scp target)
├── .env                            # secrets, never tracked in git
├── BlackboxBrief/                  # BB niche
├── ClutchWire/                     # sports
├── CriticalRush/niches/gaming/     # gaming
├── FrameDrift/                     # anime
├── SpliceReel/                     # movies
├── dashboard/                      # ops dashboard
├── genlab-core/src/genlab_core/    # shared library
├── deploy/                         # this file lives here
└── .tmp/runs/<niche>_<timestamp>/  # per-run artifacts
```

## Service inventory

```bash
ssh root@46.224.237.56 'systemctl list-units --type=service --no-pager | grep genlab'
```

Active services as of audit 2026-05-18:
- `genlab-dashboard.service` — Flask UI + REST API
- `genlab-engagement-poller.service` — YouTube/X mention polling
- `genlab-engagement-worker.service` — Dramatiq reply queue
- `genlab-quota-monitor.service` — disk + API quota
- `genlab-webhook.service` — Meta webhook receiver

Per-niche pipelines are timer-driven oneshots, not daemons.

## Restarts after code change

The pipeline oneshots reload code each time they fire (no daemon
state). Long-running services need a `systemctl restart`:

```bash
# After changing engagement code
ssh root@46.224.237.56 systemctl restart genlab-engagement-worker.service

# After dashboard changes
ssh root@46.224.237.56 systemctl restart genlab-dashboard.service
```

## Audit-time spot checks

```bash
# 1. Confirm Hetzner files match local for a critical path
for f in genlab-core/src/genlab_core/pipeline/stages/video_gate.py \
         BlackboxBrief/config/sources.yaml ; do
  L=$(md5 -q "$f")
  R=$(ssh root@46.224.237.56 "md5sum /opt/genlab/$f | awk '{print \$1}'")
  [ "$L" = "$R" ] && echo "OK  $f" || echo "MISMATCH  $f  local=$L remote=$R"
done

# 2. Confirm a fix actually ran (probe the journal or .tmp/logs)
ssh root@46.224.237.56 'grep VideoGate /opt/genlab/.tmp/logs/clutchwire/*.log | tail -5'

# 3. Quick blueprint state snapshot
ssh root@46.224.237.56 'set -a && source /opt/genlab/.env && set +a && \
  /opt/genlab/.venv/bin/psql "$DATABASE_URL" -c \
  "SELECT niche_id, status, COUNT(*) FROM blueprints \
   WHERE created_at > NOW() - INTERVAL '\''7 days'\'' \
   GROUP BY 1,2 ORDER BY 1,2"'
```

## When NOT to scp

- `.env` files. Only edit them in-place via `ssh root@...` to avoid
  clobbering production tokens with a local stale value. (Sprint 62
  incident: env consolidation after token provisioning overwrote
  fresh tokens with stale ones.)
- Anything with `(assume-unchanged)` in `git ls-files -v` — those have
  real values locally but sanitized in tracked git, and a careless
  scp from a different machine could overwrite the real values.
- Run artifacts under `.tmp/runs/` — those are produced on Hetzner, not
  pushed to it.

## Rollback

There's no transactional deploy, but `deploy/scripts/rollback.sh`
handles the file-level case (R-02):

```bash
# Roll a single config file back to whatever it was 5 commits ago.
deploy/scripts/rollback.sh HEAD~5 BlackboxBrief/config/sources.yaml

# Roll multiple files back to a specific tagged release.
deploy/scripts/rollback.sh v2026.05.18 \
  CriticalRush/niches/gaming/config/sources.yaml \
  genlab-core/src/genlab_core/pipeline/stages/video_gate.py
```

How it works:
1. Validates the git ref exists in this repo.
2. Extracts each requested path from `<ref>:<path>` into a temp
   staging dir.
3. Hands the staged files to `deploy.sh`, so the same
   md5-checksum-verify round-trip applies. A mismatch aborts before
   any service restart fires.
4. Cleans up the staging dir on success; preserves it on failure
   for forensics.

For Postgres rollback, the `pg-backup.timer` runs daily at 06:30 IST;
the dump sits under `/opt/backups/` on Hetzner.
