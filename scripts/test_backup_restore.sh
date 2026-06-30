#!/bin/bash
# test_backup_restore.sh — Weekly dry-run validation of the most-recent
# PostgreSQL backup.
#
# Why this exists
# ---------------
# On 2026-06-29, the COPY-extract workaround for restoring deleted
# blueprints was discovered DURING the crisis (a full pg_restore failed
# on a PG-version mismatch between the backup file and the live
# container). This script validates the workaround works BEFORE the
# next crisis: it takes the most-recent .backups/genlab_*.sql.gz,
# extracts the blueprints COPY block, loads it into a TEMP TABLE inside
# the live DB (zero side-effects on real data), counts rows, and exits
# 0/1 accordingly.
#
# The systemd timer (genlab-backup-test.timer) wraps this script with
# OnFailure= alerting so a broken backup file surfaces as an alert
# row in pipeline_alerts — not as a "discovered DURING crisis" moment.
#
# Mode
# ----
# Pass --self-test as the first arg to run the script against a
# synthetic gzipped SQL fixture instead of /opt/genlab/.backups.
# Used by the CI / local dev path that doesn't have the real backup
# directory. Exits 0 if the fixture parses + counts > 0, 1 otherwise.
#
# Exit codes
# ----------
# 0 = backup is valid, rows extracted > 0
# 1 = no backup found, extraction failed, or zero-row table
# 2 = unexpected error
#
# See also
# --------
# docs/runbooks/DISASTER_RECOVERY.md — codified procedure
# scripts/pg_backup.sh — the daily backup-producer side
# deploy/systemd-phase2/genlab-backup-test.{timer,service}

set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${GENLAB_BACKUP_DIR:-/opt/genlab/.backups}"
SELF_TEST=0
SELF_TEST_FIXTURE=""

# --self-test takes an optional path to a fixture; if absent, we
# generate a tiny synthetic gzipped SQL file in tmp and use it.
if [[ "${1:-}" == "--self-test" ]]; then
    SELF_TEST=1
    SELF_TEST_FIXTURE="${2:-}"
fi

cleanup() {
    rm -f /tmp/_genlab_bp_restore_test.copy /tmp/_genlab_bp_restore_test.fixture.sql.gz 2>/dev/null || true
}
trap cleanup EXIT

echo "[backup-test] $(date -u +%FT%TZ) — starting dry-run validation"

# ──────────────────────────────────────────────────────────────────
# 1. Resolve the backup file
# ──────────────────────────────────────────────────────────────────
if [[ "$SELF_TEST" -eq 1 ]]; then
    if [[ -z "$SELF_TEST_FIXTURE" ]]; then
        # Build a tiny synthetic fixture that mirrors the real format:
        # a COPY block with two rows + the \. terminator. Mirrors what
        # pg_dump emits for public.blueprints.
        SELF_TEST_FIXTURE=/tmp/_genlab_bp_restore_test.fixture.sql.gz
        {
            echo "--"
            echo "-- PostgreSQL synthetic fixture"
            echo "--"
            echo "COPY public.blueprints (id, niche_id) FROM stdin;"
            echo -e "11111111-1111-1111-1111-111111111111\tgaming"
            echo -e "22222222-2222-2222-2222-222222222222\tsports"
            echo "\\."
        } | gzip > "$SELF_TEST_FIXTURE"
    fi
    BACKUP_FILE="$SELF_TEST_FIXTURE"
    echo "[backup-test] self-test mode: using fixture $BACKUP_FILE"
else
    if [[ ! -d "$BACKUP_DIR" ]]; then
        echo "[backup-test] ERROR: backup dir not found: $BACKUP_DIR" >&2
        exit 1
    fi
    # Most-recent .sql.gz by mtime. `ls -t` is portable across GNU
    # (Linux prod) and BSD (macOS dev) — `find -printf` is GNU-only
    # and silently exits non-zero on BSD without any error message.
    # `ls -t` lists newest first; we pick the first one.
    # `set +o pipefail` locally so the head|ls SIGPIPE under set -e
    # doesn't abort the script before we can emit a sensible error.
    set +o pipefail
    # shellcheck disable=SC2012  # ls -t is the portable mtime-sort here
    BACKUP_FILE=$(ls -t "$BACKUP_DIR"/genlab_*.sql.gz 2>/dev/null | head -n1)
    set -o pipefail
    if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
        echo "[backup-test] ERROR: no genlab_*.sql.gz found in $BACKUP_DIR" >&2
        exit 1
    fi
    echo "[backup-test] using most-recent backup: $BACKUP_FILE"
fi

# Age sanity: warn if the most-recent backup is older than 36h
# (the daily timer runs at 06:30 IST = ~01:00 UTC; >36h means a
# missed run). The warning does NOT change exit code — that is the
# job of a different alert path.
if [[ "$SELF_TEST" -eq 0 ]]; then
    AGE_SEC=$(( $(date +%s) - $(stat -c %Y "$BACKUP_FILE" 2>/dev/null || stat -f %m "$BACKUP_FILE") ))
    if (( AGE_SEC > 129600 )); then
        echo "[backup-test] WARN: most-recent backup is ${AGE_SEC}s old (>36h); pg_backup.timer may have failed"
    fi
fi

# ──────────────────────────────────────────────────────────────────
# 2. Extract the blueprints COPY block — uses the AWK pattern from
#    docs/runbooks/DISASTER_RECOVERY.md proven on 2026-06-29.
# ──────────────────────────────────────────────────────────────────
EXTRACT=/tmp/_genlab_bp_restore_test.copy
# Use `gzip -dc` for portability: GNU zcat handles .gz, but Apple zcat
# (BSD) only handles .Z and rejects .gz files with a confusing error.
# `gzip -dc` is the same binary on both platforms.
if ! gzip -dc "$BACKUP_FILE" \
    | awk '/^COPY public.blueprints/{flag=1; next} /^\\\.$/{flag=0} flag' \
    > "$EXTRACT"; then
    echo "[backup-test] ERROR: COPY-extract failed for $BACKUP_FILE" >&2
    exit 1
fi

ROW_COUNT=$(wc -l < "$EXTRACT" | tr -d ' ')
echo "[backup-test] extracted $ROW_COUNT data rows from public.blueprints"

if [[ "$ROW_COUNT" -eq 0 ]]; then
    echo "[backup-test] ERROR: backup contains 0 blueprints rows — invalid or empty backup" >&2
    exit 1
fi

# ──────────────────────────────────────────────────────────────────
# 3. (Real-mode only) Load into a TEMP TABLE inside the live DB and
#    count. TEMP TABLE means zero side-effects on persisted data —
#    it lives only for the session.
# ──────────────────────────────────────────────────────────────────
if [[ "$SELF_TEST" -eq 1 ]]; then
    echo "[backup-test] self-test PASS: extracted $ROW_COUNT rows from fixture"
    exit 0
fi

# Source .env for DATABASE_URL if not already in environment
if [[ -z "${DATABASE_URL:-}" && -f "$GENLAB_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$GENLAB_ROOT/.env"
    set +a
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "[backup-test] ERROR: DATABASE_URL not set — cannot validate against live DB" >&2
    exit 1
fi

# psql in a single transaction; TEMP TABLE auto-drops at COMMIT.
# Using \copy (client-side) so the file path is resolved on the
# invoking host, not inside the postgres container.
RESTORE_COUNT=$(psql "$DATABASE_URL" -t -A -v ON_ERROR_STOP=1 <<SQL
BEGIN;
CREATE TEMP TABLE _backup_restore_test (LIKE public.blueprints INCLUDING ALL);
\copy _backup_restore_test FROM '$EXTRACT';
SELECT count(*) FROM _backup_restore_test;
ROLLBACK;
SQL
)

RESTORE_COUNT=$(echo "$RESTORE_COUNT" | tail -n1 | tr -d ' ')
echo "[backup-test] loaded $RESTORE_COUNT rows into TEMP table (rolled back)"

if [[ -z "$RESTORE_COUNT" || "$RESTORE_COUNT" -le 0 ]]; then
    echo "[backup-test] ERROR: TEMP-table load returned 0 rows" >&2
    exit 1
fi

if [[ "$RESTORE_COUNT" != "$ROW_COUNT" ]]; then
    echo "[backup-test] WARN: extracted=$ROW_COUNT but loaded=$RESTORE_COUNT (parse drift?)" >&2
    # Not a failure — the load succeeded, just emit warning
fi

echo "[backup-test] PASS: $RESTORE_COUNT blueprints recoverable from $BACKUP_FILE"
exit 0
