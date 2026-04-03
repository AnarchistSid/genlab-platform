#!/bin/bash
# GenLab database backup — creates timestamped pg_dump in .tmp/backups/
set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$GENLAB_ROOT/.tmp/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/genlab_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[backup] Starting database backup..."
pg_dump genlab | gzip > "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[backup] Done: $BACKUP_FILE ($SIZE)"

# Keep only last 7 backups
cd "$BACKUP_DIR"
ls -t genlab_*.sql.gz 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null || true
echo "[backup] Retention: kept last 7 backups"
