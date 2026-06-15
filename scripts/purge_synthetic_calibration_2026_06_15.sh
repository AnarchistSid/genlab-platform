#!/usr/bin/env bash
# ============================================================================
# purge_synthetic_calibration_2026_06_15.sh — clear the 20 synthetic rows
# poisoning the AUTO #1 calibration data.
#
# AUTO #2 Day-1 step D1.1 per docs/runbooks/AUTO_2_ROLLOUT_2026-06-15.md.
#
# Why this exists (2026-06-15): a calibration test script ran 4 times on
# Jun 14 producing 20 rows (5 rows/run × 4 runs) with synthetic
# blueprint_ids '10001' through '10005'. All are niche='gaming',
# gate_approved=False, gate_confidence=0.5. They pollute the confusion
# matrix that drives the calibration-readiness signal — Mission Control's
# AutoApprovalCalibrationCard has been showing fake numbers.
#
# Identification: real blueprint_ids are UUIDs (36 chars, hyphen-delimited).
# The synthetic ones are short integer strings ('10001'..'10005'). The
# script uses ``LENGTH(blueprint_id) < 36`` as the canonical filter so a
# future synthetic-ID format that's not an exact match against the
# 10001-10005 numbers still gets caught.
#
# This script:
#   1. Counts + previews the synthetic rows (dry-run by default)
#   2. With --apply: backs up to JSON, DELETEs the rows, prints diff
#
# Backup goes to .tmp/backups/ — same path as cleanup_overscheduled.sh.
# Safe to re-run. After a clean purge the count goes to 0 and the script
# becomes a no-op.
#
# Honors CLAUDE.md cleanup_safety.md: this is delete-only on a non-
# scheduled-post table; explicit operator --apply equals force=True.
#
# Usage:
#   ./scripts/purge_synthetic_calibration_2026_06_15.sh           # dry-run
#   ./scripts/purge_synthetic_calibration_2026_06_15.sh --apply   # delete
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

# Resolve PG connection params (same pattern as cleanup_overscheduled.sh)
DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
export PGPASSWORD="$DB_PASS"

PSQL=(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q)

MODE="${1:-dry-run}"
case "$MODE" in
    --apply|dry-run) ;;
    *) echo "ERROR: unknown mode '$MODE' (use --apply or no arg for dry-run)"; exit 1 ;;
esac

# ── Phase 1 — preview + count ────────────────────────────────────────────────
SYNTHETIC_PREDICATE="LENGTH(blueprint_id) < 36"

echo "──────────────────────────────────────────────────────────────────────"
echo " Synthetic calibration rows (blueprint_id length < 36, non-UUID):"
echo "──────────────────────────────────────────────────────────────────────"
"${PSQL[@]}" -c "
SELECT blueprint_id, niche_id, gate_approved, gate_confidence,
       operator_action, decided_at
FROM auto_approval_calibration
WHERE $SYNTHETIC_PREDICATE
ORDER BY decided_at;
"
echo

COUNT=$("${PSQL[@]}" -t -A -c "
SELECT COUNT(*) FROM auto_approval_calibration WHERE $SYNTHETIC_PREDICATE;
")

if [[ "$COUNT" == "0" ]]; then
    echo "✓ No synthetic rows found — calibration table is clean."
    exit 0
fi

# Also report the legitimate rows that will SURVIVE the purge, so the
# operator can sanity-check the count delta.
REAL_COUNT=$("${PSQL[@]}" -t -A -c "
SELECT COUNT(*) FROM auto_approval_calibration WHERE NOT ($SYNTHETIC_PREDICATE);
")

echo "── Affected: $COUNT synthetic rows ──────────────────────────────────"
echo "── Surviving: $REAL_COUNT legitimate UUID-keyed rows ────────────────"

if [[ "$MODE" == "dry-run" ]]; then
    echo
    echo "DRY-RUN — no changes made. Re-run with --apply to DELETE the synthetic rows."
    exit 0
fi

# ── Phase 2 — backup ─────────────────────────────────────────────────────────
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${GENLAB}/.tmp/backups"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="${BACKUP_DIR}/synthetic_calibration_backup_${TIMESTAMP}.json"

echo
echo "── Backing up to: $BACKUP_FILE ──────────────────────────────────────"

"${PSQL[@]}" -t -A -c "
SELECT json_agg(row_to_json(r))
FROM auto_approval_calibration r
WHERE $SYNTHETIC_PREDICATE;
" > "$BACKUP_FILE"

echo "✓ Backup written ($(wc -c < "$BACKUP_FILE") bytes)"

# ── Phase 3 — delete ─────────────────────────────────────────────────────────
echo
echo "── Applying DELETE ──────────────────────────────────────────────────"
DELETED=$("${PSQL[@]}" -t -A -c "
WITH d AS (
    DELETE FROM auto_approval_calibration
    WHERE $SYNTHETIC_PREDICATE
    RETURNING 1
)
SELECT COUNT(*) FROM d;
")

echo "✓ Deleted $DELETED synthetic calibration row(s)"
echo
echo "AutoApprovalCalibrationCard on Mission Control will now show real"
echo "operator-vs-gate agreement. Sample counts will look smaller — that's"
echo "expected (you're seeing the truth instead of the synthetic inflation)."
echo
echo "Restore from backup if needed:"
echo "  cat $BACKUP_FILE  # JSON array of original rows"
