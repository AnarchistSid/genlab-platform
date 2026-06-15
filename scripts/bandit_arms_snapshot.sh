#!/usr/bin/env bash
# ============================================================================
# bandit_arms_snapshot.sh — Daily snapshot of the bandit_arms table
#
# Why this exists
# ---------------
# The bandit_arms table is the live posterior state of LinUCB +
# Thompson Sampling. It changes every time the learning loop runs.
# When debugging unexpected publish outcomes ("why did the gate pick
# style:X today when style:Y was winning yesterday?") we currently
# have to RCA from publishing_analytics + arm_loader logs and infer
# the prior state. A daily CSV snapshot lets us diff (df_today -
# df_yesterday) in one line.
#
# AUTO #2 runbook S6 — Day 2a.
#
# Output
# ------
#   /mnt/genlab-media/snapshots/bandit_arms_YYYYMMDD.csv
#
# Rotation
# --------
#   Keeps last 30 days. Older snapshots auto-deleted by `find`.
#   Rotation runs IN THIS SCRIPT (not a separate timer) so a missed
#   day doesn't grow the snapshot dir indefinitely — the next
#   successful run prunes.
#
# Usage
# -----
#   ./scripts/bandit_arms_snapshot.sh
#
# Exit codes
# ----------
#   0 — snapshot written, rotation succeeded (or no rotation needed)
#   1 — pre-flight failure (no DATABASE_URL, snapshot dir not
#       writable, psql missing)
#   2 — snapshot COPY failed (DB error)
#   3 — rotation failed (snapshot written, but old files NOT pruned —
#       non-fatal; logged so monitoring can alert)
# ============================================================================
set -euo pipefail

GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$GENLAB/.logs/bandit_arms_snapshot.log"
SNAPSHOT_DIR="${BANDIT_SNAPSHOT_DIR:-/mnt/genlab-media/snapshots}"
RETENTION_DAYS="${BANDIT_SNAPSHOT_RETENTION_DAYS:-30}"

mkdir -p "$(dirname "$LOG")"

# Source .env for DATABASE_URL — same pattern as db_maintenance.sh
if [[ -f "$GENLAB/.env" ]]; then
    set -a
    source "$GENLAB/.env"
    set +a
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

# ── Pre-flight ─────────────────────────────────────────────────────
if [[ -z "${DATABASE_URL:-}" ]]; then
    log "FATAL: DATABASE_URL not set"
    exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
    log "FATAL: psql not on PATH"
    exit 1
fi
if ! mkdir -p "$SNAPSHOT_DIR" 2>>"$LOG"; then
    log "FATAL: cannot create $SNAPSHOT_DIR"
    exit 1
fi
if [[ ! -w "$SNAPSHOT_DIR" ]]; then
    log "FATAL: $SNAPSHOT_DIR not writable"
    exit 1
fi

# Extract connection params from DATABASE_URL
DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')

export PGPASSWORD="$DB_PASS"

DATE_TAG=$(date -u +%Y%m%d)
OUT="$SNAPSHOT_DIR/bandit_arms_${DATE_TAG}.csv"

# ── Snapshot ──────────────────────────────────────────────────────
log "Writing snapshot to $OUT"

# COPY runs server-side — efficient for large tables. -X disables psqlrc
# to keep the output deterministic.
if ! psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -q \
    -c "\COPY (SELECT * FROM bandit_arms ORDER BY niche_id, arm_id) TO STDOUT WITH CSV HEADER" \
    > "$OUT" 2>>"$LOG"; then
    log "FATAL: COPY failed; check log"
    # Clean up partial file so the next run doesn't see a corrupted snapshot
    rm -f "$OUT"
    unset PGPASSWORD
    exit 2
fi

ROW_COUNT=$(($(wc -l < "$OUT") - 1))  # subtract header
SIZE_BYTES=$(stat -c %s "$OUT" 2>/dev/null || stat -f %z "$OUT")
log "Snapshot complete: $ROW_COUNT rows, $SIZE_BYTES bytes"

# ── Rotation ──────────────────────────────────────────────────────
# Delete snapshots older than RETENTION_DAYS. Match only the canonical
# filename pattern so we never delete unrelated files in the dir.
#
# Previous version piped to `grep -c "bandit_arms_"` to count pruned
# files. That returned exit 1 when there were 0 matches (the common
# case on day 1 and every day before retention fills) which made the
# script return exit 3 (rotation soft-fail) on every healthy run.
# Switched to a direct find capture so the exit-3 path now fires
# only when `find` itself fails (permissions / filesystem error).
PRUNED=0
ROTATION_EXIT=0

PRUNED_LIST=$(
    find "$SNAPSHOT_DIR" -maxdepth 1 -type f -name "bandit_arms_*.csv" \
        -mtime +"$RETENTION_DAYS" -print -delete 2>>"$LOG"
) || ROTATION_EXIT=$?

if [[ "$ROTATION_EXIT" -ne 0 ]]; then
    ROTATION_EXIT=3
    log "WARN: rotation failed (find exit non-zero); old snapshots NOT pruned (non-fatal)"
else
    if [[ -n "$PRUNED_LIST" ]]; then
        # Append the deleted paths to the log for audit trail
        echo "$PRUNED_LIST" >> "$LOG"
        PRUNED=$(echo "$PRUNED_LIST" | wc -l | tr -d ' ')
    fi
fi

if [[ "$PRUNED" -gt 0 ]]; then
    log "Pruned $PRUNED snapshot(s) older than $RETENTION_DAYS days"
fi

unset PGPASSWORD
log "Done (rotation_exit=$ROTATION_EXIT)"

# Surface rotation failure to systemd as a soft fail (snapshot did succeed)
exit "$ROTATION_EXIT"
