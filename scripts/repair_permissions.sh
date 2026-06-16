#!/usr/bin/env bash
# ============================================================================
# repair_permissions.sh — Restore /opt/genlab ownership to the genlab user
#
# Why this exists
# ---------------
# The 2026-06-16 09:23 IST outage: every pipeline failed at startup because
# /opt/genlab/uv.lock + 1505 other files were owned by root:root (artefact of
# operator running `uv pip install ...` as root, or pre-PR-#259 deploys
# without `--chown`). Once a single write-hot file flips ownership, every
# `uv run` from the genlab user dies with EACCES and pipelines silently
# no-op.
#
# This script is the **single command** the operator runs to recover:
#
#     sudo /opt/genlab/scripts/repair_permissions.sh
#
# It is idempotent + safe to re-run + read-only when nothing needs fixing.
#
# What it does
# ------------
#   1. Reports the current state: count of root-owned files in /opt/genlab
#      excluding paths the genlab user has no business writing to (.git/,
#      node_modules/ which are operator-installed by intent).
#   2. With --dry-run: just reports; doesn't touch any file (default).
#   3. With --apply: chowns -R every file outside the excluded paths
#      back to genlab:genlab. Also fixes typical perms drift on the
#      .venv + uv.lock.
#   4. Records the run in /opt/genlab/.logs/repair_permissions.log.
#
# Output format mirrors the auto2_day8_readiness style — each step gets
# a [ ✓ ] or [ ✗ ] line.
#
# Exit codes
# ----------
#   0 — nothing to fix OR --apply succeeded (in either case post-state is clean)
#   1 — usage error
#   2 — --dry-run with drift found (forces operator to re-run with --apply)
#   3 — --apply failed (chown returned non-zero on some path)
# ============================================================================
set -euo pipefail

MODE="dry-run"
ROOT_DIR="/opt/genlab"
OWNER="genlab:genlab"
LOG_FILE="/opt/genlab/.logs/repair_permissions.log"

# Paths we intentionally don't touch:
# - .git/ — operator-installed; may legitimately have root-owned hook scripts
# - node_modules/ — operator-installed via root npm install
# - dashboard/frontend/node_modules/ — same
# These never need genlab to write to them; pipelines don't touch them.
EXCLUDE_PATHS=(
    "$ROOT_DIR/.git"
    "$ROOT_DIR/node_modules"
    "$ROOT_DIR/dashboard/frontend/node_modules"
)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) MODE="dry-run"; shift ;;
        --apply)   MODE="apply"; shift ;;
        --root-dir) ROOT_DIR="$2"; LOG_FILE="${ROOT_DIR}/.logs/repair_permissions.log"; shift 2 ;;
        --owner)   OWNER="$2"; shift 2 ;;
        -h|--help)
            sed -n '/^# Usage\|^# What it does\|^# Exit codes/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
done

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

# Pre-create the log file owned by the target user — running as root
# would otherwise create it root-owned and the next scan would flag
# the log file itself as drift.
if [[ ! -f "$LOG_FILE" ]]; then
    touch "$LOG_FILE" 2>/dev/null || true
fi
chown "$OWNER" "$LOG_FILE" 2>/dev/null || true

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

# Build the `find ... -not -path ...` exclusion expression. Each
# exclusion adds two terms: `-not -path PATH` AND `-not -path PATH/*`.
build_find_excludes() {
    local args=()
    for p in "${EXCLUDE_PATHS[@]}"; do
        args+=(-not -path "$p" -not -path "$p/*")
    done
    printf '%s\0' "${args[@]}"
}

# Run find with the standard exclusions; bash's arg-passing via
# null-separated array survives paths with spaces.
find_root_owned() {
    local args=()
    while IFS= read -r -d '' arg; do
        args+=("$arg")
    done < <(build_find_excludes)
    # Find files NOT owned by the target user
    find "$ROOT_DIR" \( -uid 0 -o -gid 0 \) "${args[@]}" 2>/dev/null
}

log "═══════════════════════════════════════════════════════════════════════"
log "  repair_permissions.sh — mode=$MODE root=$ROOT_DIR owner=$OWNER"
log "  invoked by $(whoami)@$(hostname)"
log "═══════════════════════════════════════════════════════════════════════"

# ── Step 1: detect drift ─────────────────────────────────────────────
log
log "── Step 1: scan for root-owned files (excluding .git, node_modules) ──"
DRIFT_FILE=$(mktemp -t repair_drift_XXXXXX)
trap 'rm -f "$DRIFT_FILE"' EXIT
find_root_owned > "$DRIFT_FILE" 2>/dev/null
DRIFT_COUNT=$(wc -l < "$DRIFT_FILE" | tr -d ' ')

if [[ "$DRIFT_COUNT" -eq 0 ]]; then
    log "[ ✓ ] no drift found — every file under $ROOT_DIR is owned by $OWNER"
    log "Nothing to do. Exiting clean."
    exit 0
fi

log "[ ✗ ] $DRIFT_COUNT file(s) owned by root:root or root group — sample:"
head -10 "$DRIFT_FILE" | sed 's/^/      /' | tee -a "$LOG_FILE"
if [[ "$DRIFT_COUNT" -gt 10 ]]; then
    log "      ... and $((DRIFT_COUNT - 10)) more"
fi

# ── Step 2: dry-run report or apply ─────────────────────────────────
if [[ "$MODE" == "dry-run" ]]; then
    log
    log "── Step 2 (dry-run): not modifying anything ──"
    log "Re-run with --apply to fix: $0 --apply"
    exit 2
fi

log
log "── Step 2 (apply): chown -R $OWNER on $DRIFT_COUNT file(s) ──"
FAILED=0
while IFS= read -r path; do
    if ! chown -h "$OWNER" "$path" 2>/dev/null; then
        log "[ ✗ ] chown failed: $path"
        FAILED=$((FAILED + 1))
    fi
done < "$DRIFT_FILE"

if [[ "$FAILED" -gt 0 ]]; then
    log "[ ✗ ] $FAILED chown failures out of $DRIFT_COUNT — operator must investigate"
    exit 3
fi

# ── Step 3: verify post-state is clean ────────────────────────────────
log
log "── Step 3: re-scan to confirm drift cleared ──"
find_root_owned > "$DRIFT_FILE"
POST_COUNT=$(wc -l < "$DRIFT_FILE" | tr -d ' ')

if [[ "$POST_COUNT" -eq 0 ]]; then
    log "[ ✓ ] post-state clean — all $DRIFT_COUNT file(s) now owned by $OWNER"
else
    log "[ ✗ ] post-state still has $POST_COUNT drifted file(s) — possible TOCTOU"
    exit 3
fi

log
log "repair complete."
exit 0
