#!/usr/bin/env bash
# ============================================================================
# cleanup_overscheduled_2026_06_15.sh — clear stranded scheduled_for on
# blueprints that the scheduler over-packed before PR #220's fix.
#
# Why this exists (2026-06-15): the dashboard scheduler's
# _next_available_slot enforced per-(date,time,niche) collisions but NOT
# per-(date,niche) cap. When optimal_time_learner returned 3 learned
# slots + yaml union → 4 candidate slots/day, the scheduler packed 4
# posts into one day. DailyCapEnforcer silently skipped 3 of 4 at
# publish-time with `[daily_cap] daily cap reached`, leaving their
# scheduled_for values pointing at slots that never fired.
#
# Operator screenshot showed 4 of 5 FrameDrift (niche=anime) Visual
# Ready posts scheduled for Jun 15 IST. The earliest (11:30 IST) will
# publish; the 12:00 / 12:30 / 15:30 ones are stranded.
#
# This script:
#   1. Groups VISUAL_READY+approved blueprints by (niche, IST date) and
#      ranks by scheduled time (earliest = rn=1)
#   2. Dry-run by default — prints the rn > 1 rows that would be cleared
#   3. With --apply: takes a JSON backup, clears scheduled_for AND
#      action_taken on the violators (returns them to the review queue
#      so the operator can re-approve them; PR #220's fixed scheduler
#      will then pick correct next-available slots)
#
# Why clear action_taken too: leaving action_taken=approved with
# scheduled_for=NULL puts the post in limbo — the dashboard's approval
# flow is what calls _next_available_slot. Clearing both returns the
# post to a clean "review pending" state.
#
# Honors CLAUDE.md cleanup_safety.md: explicit user confirmation
# ("proceed") is the equivalent of force=True. Backup before mutate.
#
# Safe to re-run. After a clean run the count goes to 0 and the script
# becomes a no-op. PR #220's scheduler fix prevents new violations.
#
# Usage:
#   ./scripts/cleanup_overscheduled_2026_06_15.sh          # dry-run
#   ./scripts/cleanup_overscheduled_2026_06_15.sh --apply  # execute
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

# Resolve PG connection params (same pattern as cleanup_stale_captioned.sh)
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

# ── Phase 1 — count + preview ────────────────────────────────────────────────
# Per-(niche, IST-date) cap is 1 today (multi_publish.enabled: false by
# default per genlab-core/config/platform_caps.yaml). Earliest in each
# bucket survives; the rest are violators.
#
# Looking forward 30 days to catch any niche the operator approved in
# bulk before the fix. After the fix only the historical Jun-15
# FrameDrift mess should match.
# Inline SQL kept in a single shell variable to avoid duplicating the
# WITH-ranked CTE across preview/count/backup/update. Heredoc-inside-$()
# was triggering bash parser confusion on the SQL parens — using a
# plain double-quoted assignment instead.
#
# Behavior summary:
# - Include PUBLISHED rows in the ranking so a post that already
#   consumed today's cap still wins its bucket; only VISUAL_READY rows
#   with rn > 1 (the actual stragglers) are modified by the UPDATE.
# - Without PUBLISHED in the partition, a post fired earlier in the day
#   flips out of VISUAL_READY, the next VISUAL_READY in the bucket gets
#   promoted to rn=1, but its publish would still skip on cap.
RANKED_CTE="WITH ranked AS (
    SELECT b.id, b.niche_id, b.scheduled_for, b.status,
        substring(coalesce(b.hook_text, '') from 1 for 60) AS hook_preview,
        ROW_NUMBER() OVER (
            PARTITION BY b.niche_id,
                date_trunc('day', b.scheduled_for AT TIME ZONE 'Asia/Kolkata')
            ORDER BY b.scheduled_for ASC
        ) AS rn
    FROM blueprints b
    WHERE b.status IN ('VISUAL_READY', 'PUBLISHED')
      AND (b.action_taken = 'approved' OR b.status = 'PUBLISHED')
      AND b.scheduled_for IS NOT NULL
      AND b.scheduled_for >= now() - interval '12 hours'
      AND b.scheduled_for <  now() + interval '30 days'
)"

echo "──────────────────────────────────────────────────────────────────────"
echo " Overscheduled blueprints (rn>1 in their (niche, IST-date) bucket):"
echo "──────────────────────────────────────────────────────────────────────"
"${PSQL[@]}" -c "$RANKED_CTE
SELECT id, niche_id, scheduled_for, hook_preview, rn
FROM ranked
WHERE rn > 1 AND status = 'VISUAL_READY'
ORDER BY niche_id, scheduled_for;"
echo

COUNT=$("${PSQL[@]}" -t -A -c "$RANKED_CTE
SELECT COUNT(*) FROM ranked WHERE rn > 1 AND status = 'VISUAL_READY';
")

if [[ "$COUNT" == "0" ]]; then
    echo "✓ No overscheduled blueprints — nothing to clean up."
    exit 0
fi

echo "── Affected count: $COUNT ────────────────────────────────────────────"

if [[ "$MODE" == "dry-run" ]]; then
    echo
    echo "DRY-RUN — no changes made. Re-run with --apply to clear these rows."
    exit 0
fi

# ── Phase 2 — backup ─────────────────────────────────────────────────────────
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${GENLAB}/.tmp/backups"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="${BACKUP_DIR}/overscheduled_backup_${TIMESTAMP}.json"

echo
echo "── Backing up affected rows to: $BACKUP_FILE ─────────────────────────"

# Backup ONLY what the UPDATE will actually touch — VISUAL_READY
# stragglers, not the PUBLISHED rows we included for cap-counting.
# Pull the full row by re-joining against blueprints b — ranked CTE
# only carries the id-set we care about.
"${PSQL[@]}" -t -A -c "$RANKED_CTE
SELECT json_agg(row_to_json(b))
FROM blueprints b
WHERE b.id IN (
    SELECT id FROM ranked WHERE rn > 1 AND status = 'VISUAL_READY'
);
" > "$BACKUP_FILE"

echo "✓ Backup written ($(wc -c < "$BACKUP_FILE") bytes)"

# ── Phase 3 — apply ──────────────────────────────────────────────────────────
echo
echo "── Applying UPDATE ───────────────────────────────────────────────────"
# Returns count of rows touched. RETURNING idiom gives us atomicity +
# a count we can echo to the operator.
# The status='VISUAL_READY' guard on the UPDATE is critical: ranked
# includes PUBLISHED rows so they consume their bucket's cap slot, but
# we must NEVER blank scheduled_for on a successfully-published post
# (that would lose publish-time scheduling history).
TOUCHED=$("${PSQL[@]}" -t -A -c "$RANKED_CTE,
updated AS (
    UPDATE blueprints
    SET scheduled_for = NULL,
        action_taken  = NULL
    WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
      AND status = 'VISUAL_READY'
    RETURNING id
)
SELECT COUNT(*) FROM updated;
")

echo "✓ Cleared scheduled_for + action_taken on $TOUCHED blueprint(s)"
echo
echo "These posts are now back in the review queue. Re-approve them via the"
echo "dashboard — PR #220's fixed scheduler will pick correct next-available"
echo "slots that respect the per-day cap."
echo
echo "Restore from backup if needed:"
echo "  cat $BACKUP_FILE  # JSON array of original rows"
