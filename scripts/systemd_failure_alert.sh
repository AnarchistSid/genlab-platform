#!/usr/bin/env bash
# ============================================================================
# systemd_failure_alert.sh — Write a CRITICAL alert when a genlab-* service fails
#
# Why this exists
# ---------------
# The 2026-06-16 audit found:
#   * genlab-health-monitor: 12 runs / 16 failures (100% silent failure rate)
#   * genlab-threads-token-refresh: 1 run / 2 failures
#   * Most failures were the same uv.lock permission denied
#
# None of these reached the operator because nothing was watching for
# "Result=exit-code". Systemd records the failure in journalctl but the
# only path to operator awareness was journalctl-grep — which no one does.
#
# This script is invoked via systemd's OnFailure= directive on every
# genlab-* service. It writes a CRITICAL row to pipeline_alerts with the
# failing unit's name + last 20 journal lines + the exact failure
# timestamp, so the dashboard CriticalAlertsBanner picks it up.
#
# Usage (from systemd unit file)
# ------------------------------
#     [Service]
#     ...
#     [Unit]
#     OnFailure=genlab-service-failure-alert@%n.service
#
# Where %n is the failed unit's full name (e.g. "genlab-health-monitor.service").
#
# Exit codes
# ----------
#   0 — alert written (or DB unavailable; we don't fail the
#       failure-alert because that would mask the original failure)
#   1 — usage error
# ============================================================================
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <failing-unit-name>" >&2
    exit 1
fi

FAILING_UNIT="$1"
GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$GENLAB/.logs/systemd_failure_alert.log"

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

log "Failure alert triggered for $FAILING_UNIT"

# Collect context — last 20 journal lines from the failed unit. Skip
# anything older than 5 min so we don't capture stale state from
# prior runs.
CONTEXT=$(journalctl -u "$FAILING_UNIT" --since "5 minutes ago" --no-pager 2>/dev/null \
            | tail -20 \
            | sed 's/[^[:print:]]//g' || echo "(journalctl failed)")

# Sanitize the unit name into a niche_id field (best-effort heuristic).
case "$FAILING_UNIT" in
    *pipeline-ai*)     NICHE="ai_creators" ;;
    *pipeline-gaming*) NICHE="gaming" ;;
    *pipeline-anime*)  NICHE="anime" ;;
    *pipeline-movies*) NICHE="movies" ;;
    *pipeline-sports*) NICHE="sports" ;;
    *)                 NICHE="all" ;;
esac

MESSAGE="Systemd unit $FAILING_UNIT failed at $(date -u +%Y-%m-%dT%H:%M:%SZ).
Investigate with: systemctl status $FAILING_UNIT && journalctl -u $FAILING_UNIT -n 50

Last 20 journal lines:
$CONTEXT"

if [[ -z "${DATABASE_URL:-}" ]]; then
    log "WARN: DATABASE_URL not set — alert NOT written (log only)"
    exit 0
fi

DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
export PGPASSWORD=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')

# psql interpolation — build the INSERT as a heredoc-into-variable so
# the bash `if !` short-circuit works. Dollar-quoting (\$msg\$..\$msg\$)
# handles the multi-line MESSAGE without escaping.
SQL_INSERT=$(cat <<SQL
INSERT INTO pipeline_alerts (
    niche_id, check_name, severity, message, created_at, resolved_at
) VALUES (
    '$NICHE', 'systemd_unit_failed', 'critical',
    \$msg\$$MESSAGE\$msg\$,
    NOW(), NULL
);
SQL
)

if ! echo "$SQL_INSERT" | psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q 2>&1 >> "$LOG_FILE"; then
    log "WARN: alert write to pipeline_alerts failed; original failure already logged"
    unset PGPASSWORD
    exit 0
fi

log "CRITICAL alert written for $FAILING_UNIT (niche=$NICHE)"
unset PGPASSWORD
exit 0
