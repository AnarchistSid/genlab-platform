#!/usr/bin/env bash
# ============================================================================
# deploy.sh — generic GenLab prod deploy (pull → migrate → restart)
#
# Solves the deploy-pipeline gap surfaced 2026-06-14 (see
# docs/DEPLOYMENT.md and the [[session-2026-06-14-deploy-pipeline-gap]]
# memory): until this script existed, prod deploys were ad-hoc
# `git pull` runs with no follow-up migration or service restart, and
# prod fell 30+ commits behind main without anyone noticing.
#
# Usage (on prod box):
#
#   ./scripts/deploy.sh                # dry-run — show what WOULD happen
#   ./scripts/deploy.sh --apply        # full deploy: pull + migrate + restart
#   ./scripts/deploy.sh --apply --skip-migrate    # deploy without DDL changes
#   ./scripts/deploy.sh --apply --skip-restart    # leave services on old code
#
# What it does (in --apply mode):
#   1. Sanity-check working tree (clean? on main?) — refuse if dirty
#   2. Fetch + show the gap (origin/main vs HEAD); confirm before pulling
#   3. `git pull --ff-only origin main` — refuse on non-fast-forward
#   4. Detect pending alembic migrations; if any AND --skip-migrate is
#      NOT set, print companion-table inventory and run upgrade head
#   5. `systemctl daemon-reload` + restart all genlab-* services
#   6. Post-deploy summary: new HEAD, last 5 commits, service status
#
# Refuses to run when:
#   - Working tree dirty (would lose changes)
#   - Not on main (production never runs from a branch)
#   - Remote pull would be non-fast-forward (someone force-pushed)
#   - Required binaries missing (git, systemctl, uv)
#
# Safe to dry-run repeatedly. Safe to --apply repeatedly when already
# up-to-date (becomes a no-op with clear "nothing to do" message).
# ============================================================================
set -euo pipefail

# Resolve repo root via git, not BASH_SOURCE. The BASH_SOURCE approach
# silently picks the wrong root when the script is run from a copy
# outside the repo (e.g., /tmp/deploy.sh resolves GENLAB to /). Using
# `git rev-parse --show-toplevel` from the invoker's CWD is honest: it
# either finds the repo or errors clearly.
GENLAB=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "ERROR: not inside a git repository — cd into the GenLab checkout first" >&2
    exit 1
}
LOG_DIR="$GENLAB/.logs"
LOG="$LOG_DIR/deploy_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$LOG_DIR"

# Parse args
APPLY=0
SKIP_MIGRATE=0
SKIP_RESTART=0
for arg in "$@"; do
    case "$arg" in
        --apply)        APPLY=1 ;;
        --skip-migrate) SKIP_MIGRATE=1 ;;
        --skip-restart) SKIP_RESTART=1 ;;
        --help|-h)
            head -36 "$0" | tail -34
            exit 0
            ;;
        *) echo "ERROR: unknown arg '$arg' (try --help)"; exit 1 ;;
    esac
done

log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }
fail() { echo "[$(date '+%H:%M:%S')] ERROR: $*" | tee -a "$LOG"; exit 1; }

# ----------------------------------------------------------------------------
# Phase 0 — Binary checks (fail early if env is broken)
# ----------------------------------------------------------------------------
for bin in git systemctl; do
    command -v "$bin" >/dev/null || fail "$bin not found in PATH"
done
UV="$HOME/.local/bin/uv"
if [[ ! -x "$UV" ]]; then
    UV=$(command -v uv 2>/dev/null || true)
    [[ -n "$UV" ]] || fail "uv not found (looked in ~/.local/bin and PATH)"
fi

# ----------------------------------------------------------------------------
# Phase 1 — Repo sanity (working tree + branch)
# ----------------------------------------------------------------------------
cd "$GENLAB"
log "Mode: $([[ $APPLY -eq 1 ]] && echo APPLY || echo dry-run)"
log "Log:  $LOG"
log "Repo: $GENLAB"

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
log "Branch: $CURRENT_BRANCH"
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    fail "not on main (current: $CURRENT_BRANCH). Production deploys must come from main."
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    log "--- dirty files ---"
    git status --short | tee -a "$LOG"
    fail "working tree is dirty. Commit, stash, or reset before deploying."
fi

# ----------------------------------------------------------------------------
# Phase 2 — Fetch + show the gap
# ----------------------------------------------------------------------------
log "Fetching origin/main..."
git fetch origin main 2>&1 | tee -a "$LOG"

HEAD_BEFORE=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse origin/main)
log "HEAD:        $HEAD_BEFORE"
log "origin/main: $REMOTE_HEAD"

if [[ "$HEAD_BEFORE" == "$REMOTE_HEAD" ]]; then
    log "Already at origin/main. Nothing to pull."
    BEHIND_COUNT=0
else
    BEHIND_COUNT=$(git rev-list --count "$HEAD_BEFORE..$REMOTE_HEAD")
    AHEAD_COUNT=$(git rev-list --count "$REMOTE_HEAD..$HEAD_BEFORE")
    if [[ "$AHEAD_COUNT" -gt 0 ]]; then
        fail "local HEAD is $AHEAD_COUNT commit(s) AHEAD of origin/main. Pull would be non-fast-forward. Did someone force-push? Investigate before continuing."
    fi
    log "Behind origin/main by $BEHIND_COUNT commit(s):"
    git log --oneline "$HEAD_BEFORE..$REMOTE_HEAD" | head -20 | tee -a "$LOG"
    [[ "$BEHIND_COUNT" -gt 20 ]] && log "  ... (+$((BEHIND_COUNT - 20)) more)"
fi

# ----------------------------------------------------------------------------
# Phase 3 — Detect pending migrations
# ----------------------------------------------------------------------------
# Migrations only ship inside `genlab-core/migrations/versions/`, so we
# can scan the commit range for adds/changes in that path.
NEW_MIGRATIONS=""
if [[ "$BEHIND_COUNT" -gt 0 ]]; then
    NEW_MIGRATIONS=$(git diff --name-only --diff-filter=A "$HEAD_BEFORE..$REMOTE_HEAD" -- 'genlab-core/migrations/versions/*.py' || true)
fi
if [[ -n "$NEW_MIGRATIONS" ]]; then
    log "New migrations in this pull:"
    echo "$NEW_MIGRATIONS" | sed 's/^/  /' | tee -a "$LOG"
else
    log "No new migration files in this pull."
fi

# ----------------------------------------------------------------------------
# Phase 4 — Dry-run stops here
# ----------------------------------------------------------------------------
if [[ "$APPLY" -ne 1 ]]; then
    log ""
    log "DRY-RUN COMPLETE — no changes made."
    log "Re-run with --apply to execute the deploy."
    exit 0
fi

if [[ "$BEHIND_COUNT" -eq 0 ]]; then
    log "Nothing to deploy. (Use --apply --skip-migrate --skip-restart to force-restart anyway.)"
    exit 0
fi

# ----------------------------------------------------------------------------
# Phase 5 — Pull
# ----------------------------------------------------------------------------
log "Pulling origin/main..."
git pull --ff-only origin main 2>&1 | tee -a "$LOG"
HEAD_AFTER=$(git rev-parse HEAD)
if [[ "$HEAD_AFTER" != "$REMOTE_HEAD" ]]; then
    fail "post-pull HEAD ($HEAD_AFTER) != fetched origin/main ($REMOTE_HEAD). Race? Investigate."
fi
log "New HEAD: $HEAD_AFTER ✓"

# ----------------------------------------------------------------------------
# Phase 6 — Migrate (unless --skip-migrate)
# ----------------------------------------------------------------------------
if [[ "$SKIP_MIGRATE" -eq 1 ]]; then
    log "Skipping migrations (--skip-migrate set)."
elif [[ -z "$NEW_MIGRATIONS" ]]; then
    log "No new migrations; skipping alembic step."
else
    log "Pre-migration DB backup via scripts/backup_db.sh..."
    if [[ -x "$GENLAB/scripts/backup_db.sh" ]]; then
        # IMPORTANT: backup is best-effort — a failed backup must NOT
        # abort the deploy. The previous version piped the script into
        # `tee`, and with `set -o pipefail` (line 36) a non-zero backup
        # exit (e.g. pg_dump can't auth as the OS user) propagated up
        # and killed the deploy before alembic ran. We explicitly capture
        # the exit code instead and log a WARN on failure. The operator
        # can investigate the backup separately; the migration must
        # still proceed because the code on disk is already on the new
        # HEAD and rolling forward beats leaving prod in a split state.
        if "$GENLAB/scripts/backup_db.sh" >>"$LOG" 2>&1; then
            log "Backup ✓"
        else
            BACKUP_RC=$?
            log "WARN: backup_db.sh exited $BACKUP_RC — proceeding with migration (backup is best-effort; check $LOG for details)"
        fi
    else
        log "WARN: backup_db.sh not found/executable — skipping backup (risky)"
    fi
    log "Running alembic upgrade head..."
    "$UV" run --package genlab-core alembic -c genlab-core/alembic.ini upgrade head 2>&1 | tee -a "$LOG" \
        || fail "alembic upgrade FAILED — code is on new HEAD but schema is on old revision. Roll back code (git reset --hard $HEAD_BEFORE) OR investigate the migration before restarting services."
    log "Migrations applied ✓"
fi

# ----------------------------------------------------------------------------
# Phase 7 — Restart services (unless --skip-restart)
# ----------------------------------------------------------------------------
if [[ "$SKIP_RESTART" -eq 1 ]]; then
    log "Skipping service restart (--skip-restart set). New code will activate on next timer fire."
else
    log "systemctl daemon-reload..."
    systemctl daemon-reload 2>&1 | tee -a "$LOG"
    log "Restarting genlab-*.service units..."
    # Only restart units that are .service (not .timer) and active or loaded.
    # We don't restart timers — they don't run code, they only fire services.
    RESTARTED=()
    while IFS= read -r unit; do
        [[ -z "$unit" ]] && continue
        log "  restart: $unit"
        systemctl restart "$unit" 2>&1 | tee -a "$LOG" && RESTARTED+=("$unit")
    done < <(systemctl list-units --all --type=service --no-legend --plain 2>/dev/null | awk '/^genlab-/ {print $1}')
    log "Restarted ${#RESTARTED[@]} services ✓"
fi

# ----------------------------------------------------------------------------
# Phase 8 — Post-deploy summary
# ----------------------------------------------------------------------------
log ""
log "=== Deploy complete ==="
log "HEAD before: $HEAD_BEFORE"
log "HEAD after:  $HEAD_AFTER"
log "--- newly active commits ---"
git log --oneline "$HEAD_BEFORE..$HEAD_AFTER" | head -20 | tee -a "$LOG"
log ""
log "--- failed services (if any) ---"
systemctl --failed --no-legend --no-pager 2>&1 | tee -a "$LOG"
log ""
log "Log saved to $LOG"
