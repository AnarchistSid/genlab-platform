#!/bin/bash
# Rotate GenLab log files — keeps last 7 days, truncates large files.
# Run daily via com.genlab.cleanup-runs or manually.

set -euo pipefail

LOGS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.logs"
MAX_SIZE_MB=10
MAX_AGE_DAYS=7

if [ ! -d "$LOGS_DIR" ]; then
    echo "Logs directory not found: $LOGS_DIR"
    exit 0
fi

rotated=0
deleted=0

# Truncate files larger than MAX_SIZE_MB
for log in "$LOGS_DIR"/*.log; do
    [ -f "$log" ] || continue
    size_mb=$(du -m "$log" 2>/dev/null | awk '{print $1}')
    if [ "${size_mb:-0}" -gt "$MAX_SIZE_MB" ]; then
        # Keep last 1000 lines, archive the rest
        tail -1000 "$log" > "$log.tmp" && mv "$log.tmp" "$log"
        rotated=$((rotated + 1))
    fi
done

# Count old rotated logs before deleting
deleted=$(find "$LOGS_DIR" -name "*.log.*" -mtime +"$MAX_AGE_DAYS" 2>/dev/null | wc -l | tr -d ' ')
find "$LOGS_DIR" -name "*.log.*" -mtime +"$MAX_AGE_DAYS" -delete 2>/dev/null && true

echo "Log rotation: $rotated files truncated, $deleted old files cleaned"
