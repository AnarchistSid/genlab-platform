#!/bin/bash
# pg_backup.sh — Daily PostgreSQL backup for GenLab
# Retains last 14 days of gzipped SQL dumps.
set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$GENLAB_ROOT/.backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M)
BACKUP_FILE="$BACKUP_DIR/genlab_${TIMESTAMP}.sql.gz"

# Source .env for DATABASE_URL
if [[ -f "$GENLAB_ROOT/.env" ]]; then
    set -a; source "$GENLAB_ROOT/.env"; set +a
fi

# A-0025 fix: no hardcoded fallback DSN. Fail loud if DATABASE_URL is not set — a
# missing env var used to fall through to a hardcoded prod credential, which is how
# the literal ended up in this file's history in the first place (Audit A A-0055
# post-BFG re-introduction pattern; CLAUDE.md rule #30 class-of-bug).
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "[$(date)] ERROR: DATABASE_URL is not set (no fallback — see .env)" >&2
    exit 1
fi
DB_URL="$DATABASE_URL"
DB_HOST=$(echo "$DB_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DB_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DB_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DB_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DB_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')

PGPASSWORD="$DB_PASS" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
echo "[$(date)] Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Retain last 14 days
find "$BACKUP_DIR" -name "genlab_*.sql.gz" -mtime +14 -delete
echo "[$(date)] Old backups cleaned (>14 days)"
