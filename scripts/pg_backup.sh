#!/bin/bash
# pg_backup.sh — Daily PostgreSQL backup for GenLab
# Retains last 14 days of gzipped SQL dumps.
set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$GENLAB_ROOT/.backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M)
BACKUP_FILE="$BACKUP_DIR/genlab_${TIMESTAMP}.sql.gz"

/Applications/Postgres.app/Contents/Versions/17/bin/pg_dump genlab | gzip > "$BACKUP_FILE"
echo "[$(date)] Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Retain last 14 days
find "$BACKUP_DIR" -name "genlab_*.sql.gz" -mtime +14 -delete
echo "[$(date)] Old backups cleaned (>14 days)"
