#!/usr/bin/env bash
# ============================================================================
# check_permissions_drift.sh — Nightly detector for ownership drift in /opt/genlab
#
# Why this exists
# ---------------
# The 2026-06-16 outage went undetected because no system was checking
# whether files under /opt/genlab were owned by the genlab user. By the
# time the AI pipeline failed at 08:00 IST, root-owned drift had been
# accumulating for ~13 hours (since yesterday evening's deploys).
#
# This script runs nightly. If it finds any root-owned files outside the
# allowlisted paths (.git/, node_modules/), it writes a CRITICAL alert to
# the pipeline_alerts table — which the dashboard's CriticalAlertsBanner
# surfaces to the operator within seconds of the next Mission Control load.
#
# It does NOT auto-repair. Auto-repair masks the deploy bug that caused
# the drift; the operator should know (a) drift happened, and (b) when.
# repair_permissions.sh is the operator's one-command fix.
#
# Exit codes
# ----------
#   0 — no drift found OR alert successfully written
#   1 — fatal pre-flight (no DATABASE_URL, no psql)
#   2 — drift found AND alert write succeeded (script reports the count
#       so cron job operators see something; systemd sees this as
#       "alert written"; the alert in the DB is the actual signal)
# ============================================================================
set -euo pipefail

GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$GENLAB/.logs/check_permissions_drift.log"
ROOT_DIR="${GENLAB_ROOT_DIR:-/opt/genlab}"

EXCLUDE_PATHS=(
    "$ROOT_DIR/.git"
    "$ROOT_DIR/node_modules"
    "$ROOT_DIR/dashboard/frontend/node_modules"
    # Deploy logs are routinely created by root-invoked deploy.sh runs
    # (e.g. one-shot SSH+sudo deploys for hot patches). Their ownership
    # doesn't affect pipelines — logs are write-only operator records.
    # Excluded 2026-06-19 after 2 deploy log files from earlier day's
    # SSH-as-root hot-patches repeatedly tripped the drift check.
    "$ROOT_DIR/.logs"
)

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

if [[ -f "$GENLAB/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$GENLAB/.env"
    set +a
fi

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

if [[ -z "${DATABASE_URL:-}" ]]; then
    log "FATAL: DATABASE_URL not set"
    exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
    log "FATAL: psql not on PATH"
    exit 1
fi

DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
export PGPASSWORD=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')

# Build the find exclusion args
find_root_owned() {
    local args=()
    for p in "${EXCLUDE_PATHS[@]}"; do
        args+=(-not -path "$p" -not -path "$p/*")
    done
    find "$ROOT_DIR" \( -uid 0 -o -gid 0 \) "${args[@]}" 2>/dev/null
}

log "Scanning $ROOT_DIR for root-owned files"
DRIFT_FILE=$(mktemp)
trap 'rm -f "$DRIFT_FILE"' EXIT
find_root_owned > "$DRIFT_FILE" 2>/dev/null
DRIFT_COUNT=$(wc -l < "$DRIFT_FILE" | tr -d ' ')

if [[ "$DRIFT_COUNT" -eq 0 ]]; then
    log "[ ✓ ] no drift found"
    # Also resolve any previously-open alert
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q -c "
        UPDATE pipeline_alerts
        SET resolved_at = NOW()
        WHERE check_name = 'permissions_drift'
          AND severity = 'critical'
          AND resolved_at IS NULL;
    " 2>&1 >> "$LOG_FILE" || log "WARN: could not auto-resolve prior alert"
    unset PGPASSWORD
    exit 0
fi

log "[ ✗ ] drift found: $DRIFT_COUNT file(s) owned by root:root"
log "Sample (first 5):"
head -5 "$DRIFT_FILE" | sed 's/^/      /' | tee -a "$LOG_FILE"

# Build the alert message — keep it actionable and short. The
# Mission Control CriticalAlertsBanner shows the message verbatim
# so the operator can copy-paste the fix command.
MESSAGE="$DRIFT_COUNT file(s) in $ROOT_DIR are not owned by genlab:genlab.
Pipelines will fail at startup with 'Permission denied' when uv tries
to refresh uv.lock. Fix:

    sudo /opt/genlab/scripts/repair_permissions.sh --apply

Sample drifted file: $(head -1 "$DRIFT_FILE")"

# Insert a CRITICAL row. The message uses Postgres dollar-quoting
# (\$msg\$...\$msg\$) so multi-line MESSAGE with embedded quotes
# round-trips clean. SQL passes via stdin (heredoc) to avoid
# argument-length limits.
SQL_INSERT=$(cat <<SQL
INSERT INTO pipeline_alerts (
    niche_id, check_name, severity, message, created_at, resolved_at
) VALUES (
    'all', 'permissions_drift', 'critical',
    \$msg\$$MESSAGE\$msg\$,
    NOW(), NULL
);
SQL
)

if ! echo "$SQL_INSERT" | psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q 2>&1 >> "$LOG_FILE"; then
    log "FATAL: alert write failed"
    unset PGPASSWORD
    exit 1
fi

log "[ ✓ ] CRITICAL alert written to pipeline_alerts ($DRIFT_COUNT drifted files)"
unset PGPASSWORD
exit 2
