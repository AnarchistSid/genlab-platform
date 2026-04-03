#!/bin/bash
# CriticalRush .tmp cleanup — runs daily via LaunchAgent
# Retention: 7 days for video/audio, 14 days for images
# At 1 post/day (~200-400 MB), 7-day window ≈ 1.5-3 GB — well within limits.
# Longer window allows retry of failed publishes without re-rendering.

set -euo pipefail

CR_TMP="/Users/anarchistsid/GenLab/CriticalRush/.tmp"
LOG="/Users/anarchistsid/GenLab/CriticalRush/logs/cleanup.log"

mkdir -p "$(dirname "$LOG")"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting CR .tmp cleanup" >> "$LOG"
BEFORE=$(du -sh "$CR_TMP" 2>/dev/null | cut -f1 || echo "0")

# Delete rendered video files older than 7 days (was 24h)
find "$CR_TMP" -type f \( -name "*.mp4" -o -name "*.mov" -o -name "*.webm" \) \
  -mtime +7 -delete 2>/dev/null

# Delete audio files older than 7 days (was 24h)
find "$CR_TMP" -type f \( -name "*.mp3" -o -name "*.wav" -o -name "*.aac" \) \
  -mtime +7 -delete 2>/dev/null

# Delete thumbnail and image files older than 14 days (was 48h)
find "$CR_TMP" -type f \( -name "*.jpg" -o -name "*.jpeg" -o -name "*.png" \) \
  -mtime +14 -delete 2>/dev/null

# Delete empty directories
find "$CR_TMP" -type d -empty -delete 2>/dev/null

AFTER=$(du -sh "$CR_TMP" 2>/dev/null | cut -f1 || echo "0")
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Cleanup complete. $BEFORE → $AFTER" >> "$LOG"
