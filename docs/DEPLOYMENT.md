# Gen Lab Deployment Runbook

**Audience:** the human operator (or any agent) who needs to get
freshly-merged code from `origin/main` running on the Hetzner prod box.

**Why this exists:** until 2026-06-14, this procedure was undocumented.
Prod fell 30+ commits behind main without anyone noticing. The migration
drift surfaced by PR #185's deploy script was a symptom of that gap.
See `[[session-2026-06-14-deploy-pipeline-gap]]` for the full
investigation.

## TL;DR — the happy path

```bash
ssh root@46.224.237.56
cd /opt/genlab
./scripts/deploy.sh             # dry-run — shows what would happen
./scripts/deploy.sh --apply     # actually pull + migrate + restart
```

That's it. The script handles pre-flight checks, fast-forward pull,
migration detection + backup + upgrade, service restart, and post-deploy
verification. Logs land in `.logs/deploy_*.log`.

## When to deploy

| Situation | Action |
|---|---|
| A PR merged to main and you want it live | `./scripts/deploy.sh --apply` |
| Multiple PRs merged this morning | One `--apply` deploys all of them at once |
| You just pushed a hotfix and want it live now | Wait for CI green (`gh pr checks`), merge, then `--apply` |
| You merged a migration | The script auto-detects and runs alembic; no extra step |
| You merged a render/UI fix | Script restarts services so timers re-fire on new code |
| You merged docs-only | Script still works; pull happens, no migration, services restart is a no-op |

## Deploy cadence (recommendation)

- **Daily**, end-of-day IST, after all expected merges have landed
- **On-demand** when a critical fix lands (silent-failure alerts, etc.)
- **NOT** on auto-pull-every-hour — see "Why no auto-deploy" below

## What the script protects against

The script refuses to run when any of these are true:

| Refusal | Reason |
|---|---|
| Working tree dirty | Avoids losing uncommitted prod-side fixes |
| Not on `main` branch | Production never runs a feature branch |
| Pull would be non-fast-forward | Someone force-pushed; needs investigation |
| Required binary missing | `git`, `systemctl`, or `uv` — environment broken |
| `alembic upgrade` fails | Leaves code on new HEAD with **old schema** — but tells you the rollback command (`git reset --hard <old-HEAD>`) before restarting services |

## What it does NOT do

- **No rollback** on app-level failure. If services restart but then crash,
  the script reports the failed services and exits. Rollback is manual:
  `git reset --hard <pre-deploy-HEAD>` (the script prints `HEAD_BEFORE`
  at the top of the log), then `./scripts/deploy.sh --apply --skip-migrate`
  to redeploy the old code. Schema rollback requires a separate
  `alembic downgrade`.

- **No smoke tests** post-deploy. Operator should manually verify after
  deploy by checking:
  - `systemctl status genlab-pipeline-ai.service` (or any niche)
  - `tail -50 /opt/genlab/.logs/genlab-spike-detector.log` for fresh
    timestamps
  - Dashboard at `https://dashboard.aspirehub.ai` — does it load?

- **No multi-region / canary**. Single-box deploy. If we add a second
  box, this script needs `--target` parameter.

## Why no auto-deploy

Considered options (in order of how-tempting-they-look):

| Option | Why deferred |
|---|---|
| `git pull` cron every hour | Auto-pulls test PRs that merge before they're ready; no human gate |
| Push-deploy on merge to main via GitHub Action | Requires storing prod SSH key in GitHub Secrets — sensitive enough to defer until rollback machinery exists |
| Webhook on PR merge → script trigger | Same problem; webhook auth + secret-handling overhead without clear win |

The blocker for any auto-deploy: **no rollback automation** today.
`scripts/pg_backup.sh` takes daily DB backups but there's no documented
"revert app + restore DB" procedure. Until that exists, a human pulling
deliberately is safer than a machine pulling automatically.

**This may change** once:
- `test-core` / `test-channels` jobs on `main` are green (currently red,
  blocking confidence in CI as a gate)
- A `scripts/rollback.sh` script exists with documented use
- Per-service health checks exist that auto-detect a bad deploy

Track this decision in the deploy-pipeline-gap memory.

## Common deploy scenarios

### Normal deploy (most common)

```bash
ssh root@46.224.237.56
cd /opt/genlab
./scripts/deploy.sh                   # review what's coming
./scripts/deploy.sh --apply           # apply
```

### Deploy code only, defer migrations

When a migration looks risky and you want to ship the code path first
(with `IF EXISTS` guards) before flipping the schema:

```bash
./scripts/deploy.sh --apply --skip-migrate
# inspect, smoke-test, then later:
~/.local/bin/uv run --package genlab-core alembic -c genlab-core/alembic.ini upgrade head
```

### Force-restart without pulling

After a config edit on the box that doesn't need a code update:

```bash
./scripts/deploy.sh --apply --skip-migrate --skip-restart  # no-op pull
# then manually:
sudo systemctl daemon-reload
sudo systemctl restart genlab-*.service
```

### Recovery from a stuck-behind state (the 2026-06-14 scenario)

When `git pull` hasn't run in days and the drift is large:

```bash
./scripts/deploy.sh                   # see how many commits behind
./scripts/deploy.sh --apply           # one shot fixes everything
# Then verify the migration drift is closed:
./scripts/deploy_pr183_cost_persistence.sh --verify
```

### After a force-push to main (rare; investigation required)

Script will refuse with `local HEAD is N commit(s) AHEAD of origin/main`.
Don't override with `--force`; first figure out why someone force-pushed.
If legitimate (e.g., history rewrite to remove a leaked secret):

```bash
cd /opt/genlab
git fetch origin main
git reset --hard origin/main      # discard local commits
./scripts/deploy.sh --apply
```

## Migrations: what to know

Migrations live in `genlab-core/migrations/versions/`. The chain is
linear (no branches). Current head can be read with:

```bash
~/.local/bin/uv run --package genlab-core alembic -c genlab-core/alembic.ini current
```

Migrations are **forward-only in practice** — `alembic downgrade` is
implemented but rarely tested. Treat every applied migration as
permanent unless you have a verified `downgrade()` for it.

The PR #185 deploy script (`scripts/deploy_pr183_cost_persistence.sh`)
is a one-shot for the cost-persistence migration and is superseded by
this generic script for normal use. Keep PR #185's script around as
an audit-trail artifact and reference for the `ACCEPTABLE_ANCESTORS`
+ `COMPANION_TABLES` pattern when future migrations need stricter
deploy guardrails.

## Service inventory (what gets restarted)

`scripts/deploy.sh` restarts every unit matching `genlab-*.service`.
As of 2026-06-14, that's the 24 timers + services listed by:

```bash
systemctl list-units --type=service --no-legend | awk '/^genlab-/ {print $1}'
```

Restarting these is safe because:
- They're all batch-style (timer-fired or worker pollers), not long-running
  user-facing HTTP servers
- The dashboard runs separately (not under genlab-*)
- Pipelines retry on failure, so a restart mid-run is recoverable

If/when we add a long-running HTTP service prefixed `genlab-`, the
restart list needs explicit exclusion or graceful-shutdown wiring.

## Troubleshooting

### "fatal: refusing to merge unrelated histories"
You're on a different branch than `main`, or someone rewrote history.
`git status` first; do not blindly use `--allow-unrelated-histories`.

### "alembic upgrade FAILED"
Script halts before restarting services. Code is on new HEAD, schema
is on old revision. Two options:
1. Roll back code: `git reset --hard <HEAD_BEFORE printed at top of log>`
   then `./scripts/deploy.sh --apply --skip-migrate`
2. Fix the migration in a follow-up PR + re-deploy

Whatever you do, **don't restart services with new code against old
schema** — that's how silent integration bugs land.

### "systemctl restart" returns failed-state for some service
Check `journalctl -u <service> -n 50` for the error. Common causes
post-deploy:
- Python import error (new module renamed/moved)
- Config file missing (new YAML key not on box)
- Permissions (new file group-owned by non-genlab)

### Operator deployed but `auto_approval_calibration` still empty
The table was created by migration `o5j6k7l8m9n0` which only ran when
this runbook started getting followed. If empty post-deploy, it's
because no operator review has happened *since the deploy*. Click
something on the dashboard's Focus Review, then check the table again.

## See also

- `scripts/deploy.sh` — the script this runbook describes
- `scripts/deploy_pr183_cost_persistence.sh` — one-shot variant for
  PR #183's migration (audit-trail artifact)
- `scripts/backup_db.sh` — pg_dump wrapper, called by deploy.sh
- `scripts/pg_backup.sh` — daily backup service (separate from manual)
- `.github/workflows/ci.yml` — `test-storage` job validates migration
  chain on ephemeral DB (does **not** deploy to prod)
- `genlab-core/alembic.ini` — alembic config (database URL, script
  location)
