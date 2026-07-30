#!/usr/bin/env bash
# ============================================================================
# verify_backup_pruning.sh — independent "did-it-fire?" check for prune scripts
#
# Closes Audit A A-0083 (line-exists-doesn't-fire meta-finding). Purpose:
# verify that the retention prunes in pg_backup.sh + backup_visual_assets.sh
# actually delete files, NOT just that the -mtime -delete lines exist in the
# scripts. Audit Session-10 found:
#   - pg_backup.sh:29     `-mtime +14 -delete` present, 20 files retained (15 days old)
#   - backup_visual_assets.sh:121  `date < cutoff` present, 421 files pruneable
#
# This sentinel runs INDEPENDENTLY of the prune scripts — a shared bug in the
# prune (cwd resolution / early-exit / permission) can't hide it because this
# script computes the check from scratch.
#
# Usage (systemd timer runs it daily at 08:00 UTC — after both prune schedules):
#   bash scripts/verify_backup_pruning.sh
#
# Exit codes:
#   0  = both backup dirs within retention
#   1  = at least one has stale files (systemd OnFailure alert fires)
#   2  = neither backup dir exists (unexpected — probably a deploy issue)
#
# Set STALE_TOLERANCE_DB / STALE_TOLERANCE_VISUAL env vars to allow a small
# grace window (e.g. STALE_TOLERANCE_DB=1 lets a single in-flight file survive
# briefly). Default: 0 (strict).
# ============================================================================
set -uo pipefail

GENLAB_ROOT="${GENLAB_PROJECT_ROOT:-/opt/genlab}"
BACKUP_ROOT="$GENLAB_ROOT/.backups"
VISUAL_ROOT="$BACKUP_ROOT/visuals"

STALE_TOLERANCE_DB="${STALE_TOLERANCE_DB:-0}"
STALE_TOLERANCE_VISUAL="${STALE_TOLERANCE_VISUAL:-0}"

# Retention windows — must match the pruning scripts' documented rules.
# pg_backup.sh comment says "Retains last 14 days"; backup_visual_assets.sh
# uses RETENTION_DAYS=14 by default. Keep this in sync if either script changes.
DB_RETENTION_DAYS=14
VISUAL_RETENTION_DAYS=14

exit_code=0

# --- DB backup check ---
if [[ ! -d "$BACKUP_ROOT" ]]; then
    echo "ERROR: DB backup dir does not exist: $BACKUP_ROOT" >&2
    exit 2
fi
db_stale=$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name 'genlab_*.sql.gz' \
    -mtime +${DB_RETENTION_DAYS} 2>/dev/null | wc -l | tr -d ' ')

if [[ "$db_stale" -gt "$STALE_TOLERANCE_DB" ]]; then
    echo "STALE: $db_stale DB backup file(s) older than ${DB_RETENTION_DAYS} days in $BACKUP_ROOT" >&2
    echo "       (pg_backup.sh prune not firing — see A-0026 / A-0083 in .audit/)" >&2
    exit_code=1
fi

# --- Visual backup check ---
if [[ ! -d "$VISUAL_ROOT" ]]; then
    echo "ERROR: visual backup dir does not exist: $VISUAL_ROOT" >&2
    exit 2
fi
visual_stale=$(find "$VISUAL_ROOT" -type f \
    -mtime +${VISUAL_RETENTION_DAYS} 2>/dev/null | wc -l | tr -d ' ')

if [[ "$visual_stale" -gt "$STALE_TOLERANCE_VISUAL" ]]; then
    echo "STALE: $visual_stale visual file(s) older than ${VISUAL_RETENTION_DAYS} days in $VISUAL_ROOT" >&2
    echo "       (backup_visual_assets.sh prune not firing — see A-0062 / A-0083 in .audit/)" >&2
    exit_code=1
fi

if [[ "$exit_code" -eq 0 ]]; then
    echo "OK: db_stale=$db_stale (limit $STALE_TOLERANCE_DB), visual_stale=$visual_stale (limit $STALE_TOLERANCE_VISUAL)"
fi

exit "$exit_code"
