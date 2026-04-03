#!/usr/bin/env bash
# daily_intel.sh — Blackbox Brief (AI Creators) daily pipeline v5.0
#
# Thin wrapper: source credentials, call unified pipeline, log output.
# Migrated from 600-line v4.0 (Sprint 66) to unified genlab-core pipeline.
# The old v4.0 script is backed up as daily_intel.sh.bak.sprint66
#
# Usage:
#   Manual:  ./runbooks/daily_intel.sh
#   Cron:    via com.genlab.daily-intel LaunchAgent
#
# This script is cron-safe: uses absolute paths, activates venv, logs output.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHANNEL_ROOT="$(dirname "$SCRIPT_DIR")"
GENLAB_ROOT="$(dirname "$CHANNEL_ROOT")"
LOG_DIR="${CHANNEL_ROOT}/.tmp/logs"
UV="${HOME}/.local/bin/uv"

# ── Wait for network (macOS boot can fire launchd before WiFi) ────
MAX_WAIT=60
WAITED=0
while ! ping -c1 -W2 8.8.8.8 &>/dev/null; do
    if [ $WAITED -ge $MAX_WAIT ]; then
        echo "ERROR: No network after ${MAX_WAIT}s — aborting"
        exit 1
    fi
    sleep 5
    WAITED=$((WAITED + 5))
done

# ── Source credentials: root → BB → channel overrides ─────────────
for envfile in "${GENLAB_ROOT}/.env" "${CHANNEL_ROOT}/.env"; do
    if [[ -f "$envfile" ]]; then
        set -a; source "$envfile"; set +a
    fi
done

# Ensure Postgres mode
export GENLAB_USE_POSTGRES=true

# ── Setup log directory ──────────────────────────────────────────
mkdir -p "$LOG_DIR"

RUN_ID="ai_creators_$(date -u +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/run_${RUN_ID}.log"

echo "=========================================" | tee "$LOG_FILE"
echo "Blackbox Brief — Daily Intel Pipeline v5.0" | tee -a "$LOG_FILE"
echo "Run ID: $RUN_ID" | tee -a "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "=========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# ── Run unified pipeline ─────────────────────────────────────────
cd "$CHANNEL_ROOT"
"$UV" run --package blackbox-brief python run_pipeline.py "$@" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "$LOG_FILE"
echo "Finished: $(date -u '+%Y-%m-%d %H:%M:%S UTC') | exit=$EXIT_CODE" | tee -a "$LOG_FILE"
exit $EXIT_CODE
