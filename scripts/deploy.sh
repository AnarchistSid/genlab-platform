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
#   ./scripts/deploy.sh --apply --force           # run Phases 6.5 + 7 even
#                                                 # when HEAD is up-to-date
#                                                 # (auto-inferred if
#                                                 # .version.env is stale)
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
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --apply)        APPLY=1 ;;
        --skip-migrate) SKIP_MIGRATE=1 ;;
        --skip-restart) SKIP_RESTART=1 ;;
        --force)        FORCE=1 ;;
        --help|-h)
            head -40 "$0" | tail -38
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
# Phase 0b — Privilege check for --apply mode
# ----------------------------------------------------------------------------
# 2026-07-09 (task #619): daemon-reload + systemctl restart in Phase 7
# require root. Prior to this check the script would silently drop those
# steps when invoked as a non-root user (e.g. `sudo -u genlab bash -c
# ./deploy.sh --apply`), leaving the log with a benign-looking
# "Reload daemon failed: Interactive authentication required" line and
# continuing anyway. Config-only deploys survived that (pipelines
# re-read YAML at runtime) but any systemd unit change silently didn't
# take effect. Six deploys in one session hit this before it was
# noticed. Fail loud instead of silently skipping.
if [[ "$APPLY" -eq 1 && "$SKIP_RESTART" -ne 1 ]]; then
    if [[ "$(id -u)" -ne 0 ]]; then
        fail "--apply requires root (systemctl daemon-reload + restart need it). \
Run as root, or pass --skip-restart if you know the deploy is code-only \
and doesn't need service restarts."
    fi
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

# Auto-reset dashboard/frontend/package-lock.json drift before the dirty
# check. Background: when a Dependabot frontend-dep bump merges and the
# operator runs `npm install` on prod to pull the new packages, npm
# rewrites the lockfile with cosmetic differences (binary hash bytes
# differ even when semantic deps match). Subsequent `deploy.sh --apply`
# runs then fail the dirty-tree check with a single "M dashboard/
# frontend/package-lock.json" — which is benign because the next deploy
# would `git pull` the canonical lockfile from origin/main anyway, and
# `npm install` runs again post-deploy as needed.
#
# Hit on 2026-06-26 (twice — Dependabot batch merge of #595-#599 caused
# both the initial lockfile drift AND a follow-up "deploy.sh refused"
# moment when the operator tried to ship PR #603's fix).
#
# This auto-reset is narrowly scoped to the ONE file npm install
# legitimately rewrites without our intent. Any other modified tracked
# file still triggers the dirty-tree failure below.
if git diff --quiet -- dashboard/frontend/package-lock.json 2>/dev/null; then
    : # lockfile clean, nothing to do
else
    log "Resetting dashboard/frontend/package-lock.json drift (likely from prior npm install)"
    git checkout -- dashboard/frontend/package-lock.json 2>&1 | tee -a "$LOG"
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    log "--- dirty files (tracked modifications only — untracked .bak files do not block deploy) ---"
    git status --short | grep -vE '^\?\?' | tee -a "$LOG"
    fail "working tree has modified tracked files. Commit, stash, or reset before deploying."
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
    # PR #507 (2026-06-24): scope `pipefail` disable to a subshell.
    # `set -o pipefail` (line 36) makes the pipeline return git log's
    # SIGPIPE exit code (141) when `head -20` exits early after
    # capturing 20 lines, and `set -e` then kills the entire deploy.
    # This hit on 2026-06-23 trying to deploy a 22-commit pull —
    # operator had to bypass the script entirely (manual git pull +
    # systemctl restart) to land the fix. `tee` has already received
    # the 20 lines before SIGPIPE fires upstream, so the log content
    # is correct either way; only the exit-status semantics differ.
    (
        set +o pipefail
        git log --oneline "$HEAD_BEFORE..$REMOTE_HEAD" | head -20 | tee -a "$LOG"
    )
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

# 2026-08-10: gate the "nothing to deploy" short-circuit on drift detection.
# Old behavior exited 0 whenever HEAD == origin/main, even if `.version.env`
# was stale from a prior manual `git pull` (which is exactly what happens
# when an operator pulls without running deploy.sh). Post-deploy-verify
# then fired systemd_unit_failed weekly because the pin didn't match HEAD.
# New behavior: bypass short-circuit when the on-disk `.version.env` doesn't
# reflect the current HEAD, OR when --force is passed. Down-stream phases
# (Phase 5's `git pull --ff-only`, Phase 5.5 chmod, Phase 5.6 uv sync) are
# all idempotent when already up-to-date, so falling through is safe.
STALE_VERSION_ENV=0
STALE_REASON=""
VERSION_ENV_PROBE="/opt/genlab/.version.env"
if [[ -f "$VERSION_ENV_PROBE" ]]; then
    DEPLOYED_SHA=$(grep '^GENLAB_GIT_COMMIT=' "$VERSION_ENV_PROBE" | cut -d= -f2- | tr -d '"' | tr -d "'")
    CURRENT_SHORT=$(git rev-parse --short HEAD)
    if [[ -z "$DEPLOYED_SHA" ]]; then
        STALE_VERSION_ENV=1
        STALE_REASON="GENLAB_GIT_COMMIT empty in $VERSION_ENV_PROBE"
    elif [[ "$DEPLOYED_SHA" != "$CURRENT_SHORT"* && "$CURRENT_SHORT" != "$DEPLOYED_SHA"* ]]; then
        # Prefix-match either direction so short (deploy.sh convention) or
        # full 40-char SHA both count as fresh when the leading bytes align.
        STALE_VERSION_ENV=1
        STALE_REASON="$VERSION_ENV_PROBE has $DEPLOYED_SHA but HEAD is $CURRENT_SHORT"
    fi
else
    STALE_VERSION_ENV=1
    STALE_REASON="$VERSION_ENV_PROBE missing"
fi

if [[ "$BEHIND_COUNT" -eq 0 && "$FORCE" -ne 1 && "$STALE_VERSION_ENV" -ne 1 ]]; then
    log "Nothing to deploy. HEAD, .version.env, and origin/main are all in sync."
    log "Pass --force to run Phases 5.5+ (dep sync + .version.env write + service restart) anyway."
    exit 0
fi

if [[ "$BEHIND_COUNT" -eq 0 ]]; then
    if [[ "$STALE_VERSION_ENV" -eq 1 ]]; then
        log "HEAD already at origin/main but .version.env is stale ($STALE_REASON) — running post-pull phases to restore consistency."
    else
        log "HEAD already at origin/main. --force set: running post-pull phases anyway."
    fi
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
# Phase 5.5 — Restore +x bit on shell scripts
# ----------------------------------------------------------------------------
# Discovered 2026-06-30: git stores the executable bit but a fresh
# `git pull` against files whose mode was lost (e.g. scp'd onto prod
# earlier without -p) can leave the working copy without +x. Symptom
# was disk_cleanup.sh failing with exit 203 (EXEC) on first systemd
# fire after deploy. Single chmod on the scripts/*.sh glob is
# idempotent + safe (root or genlab can run; both are owners here).
log "Restoring +x bit on shell scripts in scripts/..."
chmod +x scripts/*.sh 2>&1 | tee -a "$LOG" || true

# ----------------------------------------------------------------------------
# Phase 5.6 — Sync .venv against uv.lock
# ----------------------------------------------------------------------------
# Discovered 2026-07-06: a dep-only pull (PR #711, dramatiq 2.1.0 →
# 2.2.0) landed on prod cleanly but the running services stayed on the
# old package because deploy.sh restarts systemd units WITHOUT first
# refreshing /opt/genlab/.venv against the new uv.lock. Symptom:
# `/opt/genlab/.venv/bin/python -c "import dramatiq; print(dramatiq.
# __version__)"` returned 2.1.0 even after --apply reported success and
# the engagement worker was restarted. Manual `uv sync --frozen`
# followed by a second worker restart was needed to actually pick up
# the new package.
#
# The root cause is subtle: `"$UV" run --package genlab-core alembic`
# on line 247 DOES do a workspace sync — but scoped to genlab-core's
# transient venv, not the root `/opt/genlab/.venv` that systemd
# services use. Result: alembic migrations run against fresh packages
# while services keep running against stale ones.
#
# Fix: always run `uv sync --frozen` after pull. It's a fast no-op
# when uv.lock didn't change (uv checks package hashes and short-
# circuits). When the lockfile DID change, it installs/updates exactly
# what the lockfile pins — no drift, no accidental upgrades.
#
# --frozen refuses to update uv.lock even if pyproject.toml disagrees
# — this catches the "someone edited pyproject on prod but forgot to
# push" case as a hard fail rather than a silent lockfile rewrite that
# would then get lost on the next deploy.
LOCKFILE_CHANGED=""
if [[ "$BEHIND_COUNT" -gt 0 ]]; then
    LOCKFILE_CHANGED=$(git diff --name-only "$HEAD_BEFORE..$HEAD_AFTER" -- 'uv.lock' || true)
fi
if [[ -n "$LOCKFILE_CHANGED" ]]; then
    log "uv.lock changed in this pull — syncing .venv against new lockfile..."
else
    log "uv.lock unchanged; running sync anyway (idempotent no-op)..."
fi
"$UV" sync --frozen 2>&1 | tee -a "$LOG" \
    || fail "uv sync --frozen FAILED — .venv is now in an inconsistent state (may still have OLD packages while code is on NEW HEAD). Investigate: does uv.lock match pyproject.toml? Was pyproject.toml edited on prod without a matching lockfile? Run 'uv lock' locally + push before retrying."

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
# Phase 6.5 — Write deployed-version env file (resolves Dashboard 0.0.0)
# ----------------------------------------------------------------------------
# The dashboard's Settings → System "Dashboard Version" card reads from
# the GENLAB_GIT_COMMIT + GENLAB_BUILD_TIME env vars (review_server.py
# _app_version / _build_time helpers; injected into vite build via
# VITE_APP_VERSION). Pre-fix the version field was hardcoded "2.0.0"
# so operators couldn't tell "fresh deploy" from "stale deploy."
#
# 2026-06-26 fix: file lives at /opt/genlab/.version.env (NOT
# /etc/genlab/version.env as in the original PR #592). The genlab
# user cannot write to root-owned /etc/genlab, so the original write
# silently failed on every deploy — caught by the post-deploy-verify
# harness (PR #602) on its first prod run. /opt/genlab is genlab-owned
# so this write always succeeds; the canonical systemd drop-in at
# deploy/systemd-phase2/genlab-dashboard.service.d/version.conf loads
# from BOTH paths with the new one winning, keeping legacy installs
# backwards-compatible during the transition.
VERSION_ENV_FILE="/opt/genlab/.version.env"
GIT_SHA=$(git rev-parse --short HEAD)
BUILD_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if tee "$VERSION_ENV_FILE" >/dev/null 2>&1 <<EOF
GENLAB_GIT_COMMIT=$GIT_SHA
GENLAB_BUILD_TIME=$BUILD_TS
EOF
then
    log "Wrote version env to $VERSION_ENV_FILE (commit=$GIT_SHA build=$BUILD_TS)"
else
    log "WARN: could not write $VERSION_ENV_FILE — dashboard will show 'unknown' version"
fi

# ----------------------------------------------------------------------------
# Phase 7 — Restart services (unless --skip-restart)
# ----------------------------------------------------------------------------
if [[ "$SKIP_RESTART" -eq 1 ]]; then
    log "Skipping service restart (--skip-restart set). New code will activate on next timer fire."
else
    log "systemctl daemon-reload..."
    systemctl daemon-reload 2>&1 | tee -a "$LOG"
    # ------------------------------------------------------------------
    # 2026-06-27 — restart ONLY long-running (Type=exec) services.
    #
    # Background — today's stale-alerts incident:
    #   The previous loop here `systemctl restart`-ed EVERY genlab-*
    #   service, including all the Type=oneshot units (token-refresh,
    #   snapshots, archive-stale-*, fb-survival-check, dpo-export,
    #   pipeline-*, etc). For a oneshot, `restart` actually STARTS
    #   the unit — outside its normal schedule, often during a busy
    #   moment. At 14:59 IST on 2026-06-27 a deploy fired 20 oneshots
    #   simultaneously; they hit transient errors (TTS API 429s in the
    #   journal) and each one tripped its OnFailure=genlab-service-
    #   failure-alert@.service handler (PR #615), writing 20 CRITICAL
    #   rows to pipeline_alerts. Operator's Mission Control banner
    #   suddenly showed 20 unresolved critical alerts — every one was
    #   stale within minutes (next normal-schedule run succeeded), but
    #   the rows stayed until manually cleared via SQL.
    #
    # Fix: only restart the units that actually need a restart to pick
    # up new code — the long-running daemons. Type=oneshot units pick
    # up new code naturally on their next timer fire; there's nothing
    # to "restart" because they're not running between fires.
    #
    # Companion fix in this same PR: an auto-resolve sweeper
    # (genlab-alert-auto-resolve.timer) bleeds out any
    # systemd_unit_failed alert whose unit has since had a successful
    # run, so even if a future deploy triggers an unexpected oneshot
    # failure the operator banner self-cleans within 5 minutes.
    # ------------------------------------------------------------------
    log "Restarting long-running genlab services (Type=exec only)..."
    LONG_RUNNING_SERVICES=(
        genlab-dashboard.service
        genlab-engagement-poller.service
        genlab-engagement-worker.service
        genlab-quota-monitor.service
        genlab-webhook.service
    )
    RESTARTED=()
    for unit in "${LONG_RUNNING_SERVICES[@]}"; do
        # 2026-08-10: use `systemctl cat` instead of grepping list-unit-files
        # output. The previous check `systemctl list-unit-files --plain |
        # grep -q "^$unit "` false-negatived intermittently during heavy
        # systemd load (e.g., mid-loop after a slow dashboard restart) —
        # 3 of 5 services skipped as "not installed" during the 2026-08-10
        # 18:28 deploy despite being installed + running. `systemctl cat`
        # exits 0 iff the unit file exists; no output parsing, no
        # format-brittleness. Silence stdout since we only care about
        # exit code.
        if systemctl cat "$unit" >/dev/null 2>&1; then
            log "  restart: $unit"
            if systemctl restart "$unit" 2>&1 | tee -a "$LOG"; then
                RESTARTED+=("$unit")
            fi
        else
            log "  skip (not installed): $unit"
        fi
    done
    log "Restarted ${#RESTARTED[@]} long-running services ✓"
    log "Type=oneshot units (pipeline-*, token-refresh, snapshots, etc.) will pick up new code on next timer fire."
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
