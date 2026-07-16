#!/usr/bin/env bash
# ============================================================================
# db_maintenance.sh — PostgreSQL maintenance tasks
#
# Runs VACUUM ANALYZE on all GenLab tables to reclaim space and update
# query planner statistics. Should run daily via launchd or cron.
#
# Usage: ./scripts/db_maintenance.sh
# ============================================================================
set -euo pipefail

GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$GENLAB/.logs/db_maintenance.log"
DB_URL="${DATABASE_URL:-postgresql://genlab:genlab_dev@localhost:5432/genlab}"

mkdir -p "$(dirname "$LOG")"

# Source .env for DATABASE_URL
if [[ -f "$GENLAB/.env" ]]; then
    set -a
    source "$GENLAB/.env"
    set +a
fi

DB_URL="${DATABASE_URL:-postgresql://genlab:genlab_dev@localhost:5432/genlab}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting DB maintenance" | tee -a "$LOG"

# Extract connection params from URL
DB_HOST=$(echo "$DB_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DB_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DB_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DB_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DB_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')

export PGPASSWORD="$DB_PASS"

TABLES=(
    blueprints stories assets publishing_analytics analytics
    content_memory bandit_arms pending_engagement pending_feedback
    templates sources monetisationprogress content_pool
)

# 2026-07-17: content_pool TTL delete. The deep-cuts audit found
# 47,092 expired rows (expires_at < NOW()) that were never deleted;
# autovacuum couldn't reclaim the space because the rows were still
# live tuples. Delete FIRST, then let the VACUUM ANALYZE loop below
# pick up content_pool now that it's in TABLES. Guarded by a column
# probe so a schema migration that renames the column doesn't silently
# turn this into a full-table delete.
if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -tAc "SELECT 1 FROM information_schema.columns WHERE table_name='content_pool' AND column_name='expires_at';" 2>/dev/null | grep -q 1; then
    EXPIRED=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -tAc "DELETE FROM content_pool WHERE expires_at IS NOT NULL AND expires_at < NOW() RETURNING 1;" 2>>"$LOG" | wc -l | tr -d ' ')
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] content_pool TTL delete: ${EXPIRED} rows" | tee -a "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] content_pool.expires_at column missing — skipping TTL delete" | tee -a "$LOG"
fi

VACUUMED=0
FAILED=0

for table in "${TABLES[@]}"; do
    if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -c "VACUUM ANALYZE $table;" >> "$LOG" 2>&1; then
        VACUUMED=$((VACUUMED + 1))
    else
        echo "  FAILED: $table" | tee -a "$LOG"
        FAILED=$((FAILED + 1))
    fi
done

# Check table sizes
echo "" | tee -a "$LOG"
echo "Table sizes:" | tee -a "$LOG"
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
SELECT
    relname AS table_name,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
    n_live_tup AS rows
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
" 2>/dev/null | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Done: $VACUUMED/$((VACUUMED + FAILED)) tables vacuumed" | tee -a "$LOG"

unset PGPASSWORD
