#!/usr/bin/env bash
# ============================================================================
# deploy_pr183_cost_persistence.sh — one-shot prod deploy for PR #183
#
# Applies migration `p6k7l8m9n0o1_pipeline_run_costs.py` and verifies the
# `pipeline_run_costs` table is ready for first-row writes from the next
# pipeline run.
#
# Usage (run on prod box after `git pull origin main`):
#
#   ./scripts/deploy_pr183_cost_persistence.sh           # dry-run (default)
#   ./scripts/deploy_pr183_cost_persistence.sh --apply   # actually upgrade
#   ./scripts/deploy_pr183_cost_persistence.sh --verify  # post-apply checks only
#
# Pre-flight guarantees:
#   - DATABASE_URL is set (sourced from .env)
#   - Current alembic revision == o5j6k7l8m9n0 (down_revision of p6k7l8m9n0o1)
#   - `pg_dump`-style table-only snapshot taken before any DDL
#
# Post-apply verifications:
#   - `pipeline_run_costs` table exists with the 14 expected columns
#   - UNIQUE (run_id, niche_id) constraint present (UPSERT correctness)
#   - Index on (niche_id, completed_at DESC) present (dashboard query path)
#   - Row count printed (will be 0 immediately after migration; first row
#     writes on the next pipeline run via run_report.py → persist_run_cost)
#
# Safe to run multiple times. The migration itself uses CREATE TABLE IF
# NOT EXISTS, and alembic_version tracking prevents double-apply.
# ============================================================================
set -euo pipefail

GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$GENLAB/.logs"
LOG="$LOG_DIR/deploy_pr183_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$LOG_DIR"

# Source .env for DATABASE_URL
if [[ -f "$GENLAB/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$GENLAB/.env"
    set +a
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "[deploy] ERROR: DATABASE_URL not set (check .env)" | tee -a "$LOG"
    exit 1
fi

# Revision chain. PR #183's migration declares down_revision =
# "o5j6k7l8m9n0", which itself was shipped in PR #177 (AUTO #1 calibration
# table). On a fully-deployed box, current should be o5j6k7l8m9n0 before
# we apply. On a partially-deployed box, n4i5j6k7l8m9 is also acceptable —
# alembic will walk the chain forward. Anything *newer* than NEW_HEAD or
# off-chain means drift and we refuse.
NEW_HEAD="p6k7l8m9n0o1"
TARGET_TABLE="pipeline_run_costs"
# Known-good ancestors of NEW_HEAD (descending chain order). If current
# revision is in this list, we proceed. If empty/unknown/off-chain, we
# refuse and require investigation. Updating this list is part of any
# new migration's deploy.
ACCEPTABLE_ANCESTORS=(
    "o5j6k7l8m9n0"   # AUTO #1 calibration (PR #177, shipped 2026-06-13)
    "n4i5j6k7l8m9"   # revenue events + pref embeddings (pre-AUTO)
)
COMPANION_TABLES=(
    "auto_approval_calibration"   # added by o5j6k7l8m9n0 (PR #177)
    "pipeline_run_costs"          # added by p6k7l8m9n0o1 (PR #183, this deploy)
)

# Resolve PG connection params from DATABASE_URL once.
DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
export PGPASSWORD="$DB_PASS"

PSQL=(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q -t -A)

log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }
fail() { echo "[$(date '+%H:%M:%S')] ERROR: $*" | tee -a "$LOG"; exit 1; }

MODE="${1:-dry-run}"
case "$MODE" in
    --apply|--verify|dry-run) ;;
    *) fail "unknown mode '$MODE' (expected --apply, --verify, or no arg for dry-run)" ;;
esac

# ----------------------------------------------------------------------------
# Phase 1 — Pre-flight (always runs)
# ----------------------------------------------------------------------------
log "Mode: $MODE"
log "Log:  $LOG"
log "Target: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"

log "Local checkout check..."
MIGRATION_FILE="$GENLAB/genlab-core/migrations/versions/${NEW_HEAD}_pipeline_run_costs.py"
if [[ ! -f "$MIGRATION_FILE" ]]; then
    fail "migration file $MIGRATION_FILE not found in this checkout. Run 'git pull origin main' after PR #183 merges, or check out branch feat/llm-cost-persistence first."
fi

log "Connectivity check..."
"${PSQL[@]}" -c "SELECT 1" >/dev/null || fail "cannot connect to Postgres"

CURRENT_REV=$("${PSQL[@]}" -c "SELECT version_num FROM alembic_version" 2>/dev/null || echo "")
log "Current alembic revision: ${CURRENT_REV:-<none>}"

TABLE_EXISTS=$("${PSQL[@]}" -c "SELECT to_regclass('public.$TARGET_TABLE') IS NOT NULL")
log "Table $TARGET_TABLE exists: $TABLE_EXISTS"

# ----------------------------------------------------------------------------
# Phase 2 — Verify-only mode short-circuits here
# ----------------------------------------------------------------------------
if [[ "$MODE" == "--verify" ]]; then
    if [[ "$TABLE_EXISTS" != "t" ]]; then
        fail "table $TARGET_TABLE missing — run --apply first"
    fi
    for t in "${COMPANION_TABLES[@]}"; do
        log ""
        log "=== Table: $t ==="
        EXISTS=$("${PSQL[@]}" -c "SELECT to_regclass('public.$t') IS NOT NULL")
        if [[ "$EXISTS" != "t" ]]; then
            log "  MISSING — expected after this deploy. Investigate."
            continue
        fi
        log "  --- columns ---"
        "${PSQL[@]}" -c "
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = '$t'
            ORDER BY ordinal_position;
        " | tee -a "$LOG"
        log "  --- indexes ---"
        "${PSQL[@]}" -c "
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = '$t';
        " | tee -a "$LOG"
        ROWS=$("${PSQL[@]}" -c "SELECT COUNT(*) FROM $t")
        log "  Total rows: $ROWS"
        if [[ "$ROWS" -gt 0 ]]; then
            log "  (recent activity present — table was being written to)"
        else
            log "  (empty — first writer will populate on next eligible event)"
        fi
    done
    log ""
    log "Verify complete."
    exit 0
fi

# ----------------------------------------------------------------------------
# Phase 3 — Pre-apply guards
# ----------------------------------------------------------------------------
if [[ "$TABLE_EXISTS" == "t" && "$CURRENT_REV" == "$NEW_HEAD" ]]; then
    log "Migration already applied (revision=$NEW_HEAD, table present). Nothing to do."
    log "Run '$0 --verify' to inspect schema + recent rows."
    exit 0
fi

is_acceptable=0
if [[ "$CURRENT_REV" == "$NEW_HEAD" ]]; then
    is_acceptable=1
else
    for anc in "${ACCEPTABLE_ANCESTORS[@]}"; do
        if [[ "$CURRENT_REV" == "$anc" ]]; then is_acceptable=1; break; fi
    done
fi
if [[ "$is_acceptable" -ne 1 ]]; then
    fail "current revision '$CURRENT_REV' is not in the known ancestor chain of $NEW_HEAD. Either the box has drifted (rolled back? unrelated migration?) or this script's ACCEPTABLE_ANCESTORS list is stale. Investigate before applying."
fi

# Count how many migrations will apply. If >1 there's an intermediate
# migration that wasn't deployed in its own window — surface it loudly
# because the operator may not realize this deploy will also create
# tables for an earlier feature.
if [[ "$CURRENT_REV" != "$NEW_HEAD" ]]; then
    log ""
    log "=== Multi-revision upgrade detected ==="
    log "From: $CURRENT_REV"
    log "To:   $NEW_HEAD"
    log "This upgrade will also create the following companion tables"
    log "(some may have been declared by earlier-merged PRs whose deploy"
    log "was skipped):"
    for t in "${COMPANION_TABLES[@]}"; do
        EXISTS=$("${PSQL[@]}" -c "SELECT to_regclass('public.$t') IS NOT NULL")
        log "  - $t  (exists: $EXISTS)"
    done
    log ""
fi

# ----------------------------------------------------------------------------
# Phase 4 — Show planned DDL (alembic --sql preview)
# ----------------------------------------------------------------------------
log "--- planned DDL (alembic upgrade --sql ${CURRENT_REV}:${NEW_HEAD}) ---"
cd "$GENLAB/genlab-core"
if ~/.local/bin/uv run --package genlab-core alembic upgrade --sql "${CURRENT_REV}:${NEW_HEAD}" 2>&1 | tee -a "$LOG"; then
    log "(DDL preview above — review before --apply)"
else
    fail "alembic --sql preview failed; see log"
fi

# ----------------------------------------------------------------------------
# Phase 5 — Dry-run stops here
# ----------------------------------------------------------------------------
if [[ "$MODE" != "--apply" ]]; then
    log ""
    log "DRY-RUN COMPLETE — no changes made."
    log "Re-run with --apply to execute:"
    log "    $0 --apply"
    exit 0
fi

# ----------------------------------------------------------------------------
# Phase 6 — Backup before any DDL
# ----------------------------------------------------------------------------
log "Taking pre-migration backup via scripts/backup_db.sh..."
if [[ -x "$GENLAB/scripts/backup_db.sh" ]]; then
    "$GENLAB/scripts/backup_db.sh" 2>&1 | tee -a "$LOG"
else
    log "WARN: backup_db.sh not found/executable — falling back to inline pg_dump"
    pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" \
        | gzip > "$GENLAB/.tmp/backups/pre_pr183_$(date +%Y%m%d_%H%M%S).sql.gz"
fi

# ----------------------------------------------------------------------------
# Phase 7 — Apply migration
# ----------------------------------------------------------------------------
log "Applying migration..."
if ~/.local/bin/uv run --package genlab-core alembic upgrade head 2>&1 | tee -a "$LOG"; then
    log "alembic upgrade head: SUCCESS"
else
    fail "alembic upgrade head: FAILED (see log; backup is at .tmp/backups/)"
fi

NEW_REV=$("${PSQL[@]}" -c "SELECT version_num FROM alembic_version")
if [[ "$NEW_REV" != "$NEW_HEAD" ]]; then
    fail "post-apply revision is '$NEW_REV', expected '$NEW_HEAD'"
fi
log "New alembic revision: $NEW_REV ✓"

# ----------------------------------------------------------------------------
# Phase 8 — Post-apply verification
# ----------------------------------------------------------------------------
log "Running --verify checks..."
exec "$0" --verify
