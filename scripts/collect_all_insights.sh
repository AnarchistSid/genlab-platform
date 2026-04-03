#!/usr/bin/env bash
# ============================================================================
# collect_all_insights.sh — Run fetch-insights for all niches × all windows
#
# Replaces 20 individual LaunchAgent plists with a single script.
# Usage: ./scripts/collect_all_insights.sh [--window 6|24|48|168]
#        Without --window, runs all 4 windows for all 5 niches.
# ============================================================================
set -euo pipefail

GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="$HOME/.local/bin/uv"
NICHES=(ai_creators gaming sports movies anime)
WINDOWS=(6 24 48 168)
LOG_DIR="$GENLAB/.logs"

# Parse optional --window flag
SELECTED_WINDOW=""
if [[ "${1:-}" == "--window" ]] && [[ -n "${2:-}" ]]; then
    SELECTED_WINDOW="$2"
    WINDOWS=("$SELECTED_WINDOW")
fi

mkdir -p "$LOG_DIR"

# Source .env
if [[ -f "$GENLAB/.env" ]]; then
    set -a
    source "$GENLAB/.env"
    set +a
fi

export BACKLOG_CONFIG_PATH="$GENLAB/genlab-core/config/lists_config.yaml"
export GENLAB_PROJECT_ROOT="$GENLAB"

# Wait for network (max 60s) — prevents DNS failures when Mac wakes from sleep
for i in $(seq 1 12); do
    curl -sf --max-time 3 https://www.google.com > /dev/null 2>&1 && break
    sleep 5
done

TOTAL=0
FAILED=0

for niche in "${NICHES[@]}"; do
    for window in "${WINDOWS[@]}"; do
        echo "[$(date '+%H:%M:%S')] Collecting ${window}h insights for $niche..."
        if "$UV" run --package genlab-core python -m genlab_core.scripts.run_fetch_insights \
            --niche-id "$niche" --window "$window" \
            >> "$LOG_DIR/fetch_insights_${niche}_${window}h.log" 2>&1; then
            echo "  OK"
        else
            echo "  FAILED (see $LOG_DIR/fetch_insights_${niche}_${window}h.log)"
            FAILED=$((FAILED + 1))
        fi
        TOTAL=$((TOTAL + 1))
    done
done

echo ""
echo "Done: $((TOTAL - FAILED))/$TOTAL succeeded"
[[ $FAILED -eq 0 ]] && exit 0 || exit 1
