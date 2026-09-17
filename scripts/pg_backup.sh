#!/bin/bash
# pg_backup.sh — Daily PostgreSQL backup for GenLab
# Retains last 14 days of gzipped SQL dumps.
set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$GENLAB_ROOT/.backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M)
BACKUP_FILE="$BACKUP_DIR/genlab_${TIMESTAMP}.sql.gz"

# Source .env for DATABASE_URL / BACKUP_DATABASE_URL
if [[ -f "$GENLAB_ROOT/.env" ]]; then
    set -a; source "$GENLAB_ROOT/.env"; set +a
fi

# BACKUP_DATABASE_URL takes precedence over DATABASE_URL. The app role
# (genlab_app) has NO BYPASSRLS attribute — running pg_dump under it
# fails on any table with an active RLS policy ("query would be affected
# by row-level security policy for table X"). Backups MUST use a role
# that either owns all tables or has BYPASSRLS — the superuser `genlab`
# has both. Set BACKUP_DATABASE_URL in .env to point at the superuser;
# DATABASE_URL is kept as a fallback for dev environments where only
# one role exists.
DB_URL="${BACKUP_DATABASE_URL:-${DATABASE_URL:?BACKUP_DATABASE_URL or DATABASE_URL must be set}}"
DB_HOST=$(echo "$DB_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DB_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DB_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DB_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DB_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')

PGPASSWORD="$DB_PASS" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
echo "[$(date)] Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Retain last 14 days
# Keep-7-daily + 4-weekly rather than a 14-day rolling window: bounding the
# COUNT bounds the disk cost, and four weekly points reach back a month where
# 14 rolling days reach back a fortnight. Same policy as the visuals backup.
# shellcheck source=lib/backup_retention.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/backup_retention.sh"
_pruned=0
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    rm -f "$f" && _pruned=$((_pruned + 1))
done < <(retention_drop_list "$BACKUP_DIR" "genlab_*.sql.gz")
echo "  pruned ${_pruned} dump(s); kept $(ls -1 "$BACKUP_DIR"/genlab_*.sql.gz 2>/dev/null | wc -l), free: $(df -h "$BACKUP_DIR" | tail -1 | awk '{print $4}')"
echo "[$(date)] Old backups cleaned (>14 days)"
