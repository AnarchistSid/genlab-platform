# FIX_A-0083 — Line-exists-doesn't-fire meta class-fix

## Closes
Three independent instances of "configured but not firing" — pattern (iv) in A-0088 taxonomy:
- **A-0026** — `pg_backup.sh:29` `find -mtime +14 -delete` present; 20 files retained > 14 days
- **A-0062** — `backup_visual_assets.sh:121` same predicate; 33-day-old files retained, 421 pruneable
- **A-0072** — `.pre-commit-config.yaml` gitleaks v8.24.3 configured; 2026-07-22 commit added 3× `PGPASSWORD=genlab_***` anyway

## Exact change

**Two independent-of-the-guarded-mechanism sentinels + one CI enforcement:**

### Change 1: prune sentinel (covers A-0026 + A-0062)
- New file: `scripts/verify_backup_pruning.sh`
- Content:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  DB_STALE=$(find /opt/genlab/.backups -maxdepth 1 -name 'genlab_*.sql.gz' -mtime +14 | wc -l)
  VISUAL_STALE=$(find /opt/genlab/.backups/visuals -type f -mtime +14 | wc -l)
  ALLOW_STALE_DB=0
  ALLOW_STALE_VISUAL=0  # tolerance for "in-progress prune"; adjust if needed
  if [ "$DB_STALE" -gt "$ALLOW_STALE_DB" ] || [ "$VISUAL_STALE" -gt "$ALLOW_STALE_VISUAL" ]; then
      echo "STALE: db=$DB_STALE visual=$VISUAL_STALE" >&2
      exit 1  # systemd alert fires
  fi
  echo "OK: db=$DB_STALE visual=$VISUAL_STALE"
  ```
- New unit: `/etc/systemd/system/genlab-verify-backup-pruning.service` + `.timer` (daily, 08:00 UTC — after both prune schedules)
- Alert wire: existing `OnFailure=genlab-service-failure-alert@%n.service` pattern.

### Change 2: CI enforcement of pre-commit hooks (covers A-0072)
- Edit `.github/workflows/ci.yml`: add a job `pre-commit-check`:
  ```yaml
  pre-commit-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install pre-commit
      - run: pre-commit run --all-files
  ```
- No changes to `.pre-commit-config.yaml` itself — the config was already correct; the gap was enforcement.

### Change 3 (bonus, generalize the pattern): Add to CLAUDE.md
- Rule to inherit: "Every guard mechanism (prune, hook, kill-switch, quota-cap, retention) ships with a paired independent 'did-it-fire?' check. The paired check must not share the guard's code path."

## Verification gate (execution evidence)

Must pass **both**:

1. **Prune sentinel fires**: after the fix, an intentionally-stale test file (`touch -d '30 days ago' /opt/genlab/.backups/test_stale.mp4`) causes `systemctl start genlab-verify-backup-pruning.service` to exit 1 and fire the alert unit. Post-test cleanup: `rm test_stale.mp4`.

2. **CI catches secret in test PR**: open a PR that adds a fake secret string (e.g. `test_secret_string_for_ci_check_12345 = 'do-not-commit'`) to any tracked file. Expected: PR check FAILS at the `pre-commit-check` job with gitleaks output. Close PR without merge.

Both must PASS. The point is that the mechanism is exercised by an intentional violation, not just "the workflow file exists."

## Sequencing
- Must land after: A-0053 (rotation done first, else the CI test PR would leak the current live literal)
- Blocks: A-0026, A-0062, A-0072 (all component fixes)

## Blast radius if wrong
Sentinel false-positives noisy but harmless (correct-and-tune, don't disable). CI enforcement adds ~30 seconds to every PR — negligible. The `pre-commit-check` job COULD block legitimate PRs if the pre-commit config itself has a bug — mitigation: keep the config well-tested (it is; A-0076 verified).

## Effort
M (half-day: two scripts, one unit file, one workflow job, two intentional-violation test cases)

## Owner
dev
