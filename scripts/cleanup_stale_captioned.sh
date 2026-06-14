#!/usr/bin/env bash
# ============================================================================
# cleanup_stale_captioned.sh — repoint blueprints away from stale `_captioned.mp4`
#
# Why this exists (2026-06-14): the operator disabled whisper_sync across all
# 5 niches in PR #177's visuals.yaml. New renders correctly skip producing
# _captioned.mp4. But blueprints rendered BEFORE the disable had their
# extra.visual_paths pointing at the captioned variant, and those captioned
# files were rendered with a regressed text_optimizer that produces
# overflow-canvas giant text (see [[session-2026-06-13-render-audit-findings]]).
#
# This script:
#   1. Counts affected blueprints (those still in VISUAL_READY/APPROVED/
#      SCHEDULED that reference _captioned.mp4)
#   2. Dry-run by default — shows what would change without modifying
#   3. With --apply: takes a JSON backup, runs the UPDATE, prints the diff
#
# Safe to re-run. After a clean run the count goes to 0 and the script
# becomes a no-op until a new wave of stale captioned-referencing
# blueprints appears (which shouldn't happen now PushToBacklog has the
# guard — but the script is also useful for historical cleanup and as
# documentation of the fix mechanism).
#
# Usage:
#   ./scripts/cleanup_stale_captioned.sh           # dry-run (default)
#   ./scripts/cleanup_stale_captioned.sh --apply   # execute the UPDATE
# ============================================================================
set -euo pipefail

GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Source .env for DATABASE_URL
if [[ -f "$GENLAB/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$GENLAB/.env"
    set +a
fi

[[ -n "${DATABASE_URL:-}" ]] || { echo "ERROR: DATABASE_URL not set"; exit 1; }

# Resolve PG connection params (same pattern as deploy.sh / pg_backup.sh)
DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
export PGPASSWORD="$DB_PASS"

PSQL=(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q -t -A)

MODE="${1:-dry-run}"
case "$MODE" in
    --apply|dry-run) ;;
    *) echo "ERROR: unknown mode '$MODE' (use --apply or no arg for dry-run)"; exit 1 ;;
esac

# Phase 1 — count + preview
COUNT=$("${PSQL[@]}" -c "
    SELECT COUNT(*) FROM blueprints
    WHERE status IN ('VISUAL_READY', 'APPROVED', 'SCHEDULED')
      AND extra->>'visual_paths' LIKE '%_captioned.mp4%';
")
echo "Active blueprints with stale _captioned.mp4 paths: $COUNT"

if [[ "$COUNT" == "0" ]]; then
    echo "Nothing to clean up. Exiting."
    exit 0
fi

echo ""
echo "--- Preview of changes ---"
"${PSQL[@]}" -A -c "
    SELECT
        substring(id::text, 1, 8) AS id_short,
        niche_id,
        substring(hook_text, 1, 60) AS hook,
        regexp_replace(extra->>'visual_paths', '_captioned\.mp4', '.mp4') AS new_paths
    FROM blueprints
    WHERE status IN ('VISUAL_READY', 'APPROVED', 'SCHEDULED')
      AND extra->>'visual_paths' LIKE '%_captioned.mp4%';
"

if [[ "$MODE" != "--apply" ]]; then
    echo ""
    echo "DRY-RUN — no changes made. Re-run with --apply to execute."
    exit 0
fi

# Phase 2 — backup + execute
BACKUP_DIR="$GENLAB/.tmp"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/captioned_swap_backup_$(date +%Y%m%d_%H%M%S).json"

echo ""
echo "Taking backup of affected rows → $BACKUP_FILE..."
"${PSQL[@]}" -c "
    SELECT json_agg(row_to_json(b)) FROM blueprints b
    WHERE status IN ('VISUAL_READY', 'APPROVED', 'SCHEDULED')
      AND extra->>'visual_paths' LIKE '%_captioned.mp4%';
" > "$BACKUP_FILE"
echo "Backup size: $(wc -c < "$BACKUP_FILE") bytes"

echo ""
echo "Executing UPDATE..."
UPDATED=$("${PSQL[@]}" -c "
    WITH updated AS (
        UPDATE blueprints
        SET extra = jsonb_set(
            extra,
            '{visual_paths}',
            to_jsonb(replace(extra->>'visual_paths', '_captioned.mp4', '.mp4'))
        )
        WHERE status IN ('VISUAL_READY', 'APPROVED', 'SCHEDULED')
          AND extra->>'visual_paths' LIKE '%_captioned.mp4%'
        RETURNING 1
    )
    SELECT COUNT(*) FROM updated;
")
echo "Rows updated: $UPDATED"

# Phase 3 — verify
REMAINING=$("${PSQL[@]}" -c "
    SELECT COUNT(*) FROM blueprints
    WHERE status IN ('VISUAL_READY', 'APPROVED', 'SCHEDULED')
      AND extra->>'visual_paths' LIKE '%_captioned.mp4%';
")
echo "Remaining stale references: $REMAINING (should be 0)"

if [[ "$REMAINING" != "0" ]]; then
    echo "WARN: $REMAINING blueprints still reference _captioned.mp4 — check status filter"
    exit 1
fi

echo ""
echo "✓ Cleanup complete."
echo "Backup preserved at $BACKUP_FILE for 30-day rollback safety net."
echo ""
echo "Optional file cleanup (NOT automated — needs operator authorization):"
echo "  find /opt/genlab /mnt/genlab-media -name '*_captioned.mp4' -mtime +1 \\"
echo "    -exec mv -t /opt/genlab/.tmp/quarantine_captioned/ {} +"
