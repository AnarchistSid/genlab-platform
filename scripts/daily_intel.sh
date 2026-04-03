#!/usr/bin/env bash
# Unified daily_intel.sh — runs any channel's pipeline
# Called from channel runbooks/ via symlink or from LaunchAgent
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHANNEL_ROOT="$(dirname "$SCRIPT_DIR")"
CHANNEL_NAME="$(basename "$CHANNEL_ROOT")"
GENLAB_ROOT="$(dirname "$CHANNEL_ROOT")"
UV="${HOME}/.local/bin/uv"

# Derive package name from channel directory
PACKAGE_MAP="BlackboxBrief:blackbox-brief CriticalRush:criticalrush ClutchWire:clutchwire SpliceReel:splicereel FrameDrift:framedrift"
PACKAGE=""
for entry in $PACKAGE_MAP; do
    key="${entry%%:*}"
    val="${entry##*:}"
    [ "$CHANNEL_NAME" = "$key" ] && PACKAGE="$val" && break
done
[ -z "$PACKAGE" ] && echo "Unknown channel: $CHANNEL_NAME" && exit 1

# Source credentials: root → BB (shared) → channel
for envfile in "${GENLAB_ROOT}/.env" "${GENLAB_ROOT}/BlackboxBrief/.env" "${CHANNEL_ROOT}/.env"; do
    [ -f "$envfile" ] && { set -a; source "$envfile"; set +a; }
done

# Log setup
LOG_DIR="${GENLAB_ROOT}/.tmp/logs/${PACKAGE}"
mkdir -p "$LOG_DIR"
RUN_ID="${PACKAGE}_$(date -u +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/${RUN_ID}.log"

echo "=== ${CHANNEL_NAME} daily_intel — ${RUN_ID} ===" | tee "$LOG_FILE"
echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')" | tee -a "$LOG_FILE"

cd "$CHANNEL_ROOT"
"$UV" run --package "$PACKAGE" python run_pipeline.py "$@" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}

echo "Finished: $(date -u '+%Y-%m-%d %H:%M:%S UTC') | exit=$EXIT_CODE" | tee -a "$LOG_FILE"
exit $EXIT_CODE
