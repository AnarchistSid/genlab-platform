#!/usr/bin/env bash
# Unified cleanup — all channels + shared .tmp/runs
# SAFETY: NEVER deletes directories that contain video files referenced by
# VISUAL_READY, PUBLISHED, or scheduled blueprints.
set -euo pipefail

GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="$HOME/.local/bin/uv"
LOG="$GENLAB/.logs/cleanup_all.log"

echo "=== Cleanup started $(date -u '+%Y-%m-%d %H:%M UTC') ===" >> "$LOG"

# ── Step 1: Build a set of all video file paths referenced by active blueprints ──
# This is the safety check that prevents deleting scheduled content.
PROTECTED_PATHS=$("$UV" run --project "$GENLAB" python3 -c "
import psycopg, json, os
from pathlib import Path
os.environ.setdefault('DATABASE_URL', 'dbname=genlab')
conn = psycopg.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SELECT set_config('app.niche_id', '', true)\")
cur.execute(\"\"\"
    SELECT extra->>'visual_paths' as vpaths FROM blueprints
    WHERE status = 'VISUAL_READY'
      AND extra->>'visual_paths' IS NOT NULL
      AND scheduled_for >= CURRENT_DATE
\"\"\")
protected = set()
for (vpaths_raw,) in cur.fetchall():
    if not vpaths_raw: continue
    try:
        paths = json.loads(vpaths_raw) if vpaths_raw.startswith('[') else [vpaths_raw]
    except: paths = [vpaths_raw]
    for p in paths:
        if p:
            p = p.strip('\"')
            # Protect the entire run directory containing this file
            parts = Path(p).parts
            for i, part in enumerate(parts):
                if part == 'runs' and i + 1 < len(parts):
                    run_dir = str(Path(*parts[:i+2]))
                    protected.add(run_dir)
                    break
conn.close()
for p in sorted(protected):
    print(p)
" 2>/dev/null) || PROTECTED_PATHS=""

echo "  Protected run dirs: $(echo "$PROTECTED_PATHS" | grep -c . || echo 0)" >> "$LOG"

# ── Step 2: Clean shared .tmp/runs (keep last 3 per niche, protect active blueprints) ──
SHARED_TMP="$GENLAB/.tmp/runs"
if [ -d "$SHARED_TMP" ]; then
    cleaned=0
    protected_count=0

    # Helper: check if a directory is in the protected set
    is_dir_protected() {
        local dir="$1"
        while IFS= read -r pdir; do
            [ -z "$pdir" ] && continue
            if [ "$dir" = "$pdir" ] || echo "$dir" | grep -qF "$pdir" 2>/dev/null; then
                return 0  # protected
            fi
        done <<< "$PROTECTED_PATHS"
        return 1  # not protected
    }

    # Clean per-niche runs (keep last 5, not 3 — safer margin)
    for niche in ai_creators gaming sports movies anime; do
        dirs=$(ls -dt "$SHARED_TMP"/${niche}_* 2>/dev/null || true)
        count=$(echo "$dirs" | grep -c . 2>/dev/null) || count=0
        if [ "$count" -gt 5 ]; then
            echo "$dirs" | tail -n +6 | while read dir; do
                [ -z "$dir" ] && continue
                if is_dir_protected "$dir"; then
                    echo "  PROTECTED: $(basename $dir)" >> "$LOG"
                    protected_count=$((protected_count + 1))
                else
                    rm -rf "$dir"
                    echo "  Removed: $(basename $dir)" >> "$LOG"
                    cleaned=$((cleaned + 1))
                fi
            done
        fi
    done

    # Clean multi_* and other prefixed dirs (keep last 3, protect active)
    for prefix in multi; do
        dirs=$(ls -dt "$SHARED_TMP"/${prefix}_* 2>/dev/null || true)
        count=$(echo "$dirs" | grep -c . 2>/dev/null) || count=0
        if [ "$count" -gt 3 ]; then
            echo "$dirs" | tail -n +4 | while read dir; do
                [ -z "$dir" ] && continue
                if is_dir_protected "$dir"; then
                    echo "  PROTECTED: $(basename $dir)" >> "$LOG"
                else
                    rm -rf "$dir"
                    echo "  Removed: $(basename $dir)" >> "$LOG"
                    cleaned=$((cleaned + 1))
                fi
            done
        fi
    done

    # Clean rerender subdirs older than 14 days (but protect active)
    find "$SHARED_TMP/rerender" -maxdepth 1 -type d -mtime +14 2>/dev/null | while read dir; do
        if is_dir_protected "$dir"; then
            echo "  PROTECTED rerender: $(basename $dir)" >> "$LOG"
        else
            rm -rf "$dir"
            echo "  Removed rerender: $(basename $dir)" >> "$LOG"
        fi
    done

    echo "  Shared .tmp/runs: cleaned=$cleaned, protected=$protected_count" >> "$LOG"
fi

# ── Step 3: BB cleanup (has its own cleanup script) ──
if [ -f "$GENLAB/BlackboxBrief/scripts/cleanup_runs.py" ]; then
    "$UV" run --project "$GENLAB/BlackboxBrief" python "$GENLAB/BlackboxBrief/scripts/cleanup_runs.py" >> "$LOG" 2>&1 || true
    echo "  BB cleanup done" >> "$LOG"
fi

# ── Step 4: CR cleanup ──
if [ -f "$GENLAB/CriticalRush/scripts/cleanup_tmp.sh" ]; then
    bash "$GENLAB/CriticalRush/scripts/cleanup_tmp.sh" >> "$LOG" 2>&1 || true
    echo "  CR cleanup done" >> "$LOG"
fi

# ── Step 5: Lean channel cleanup — prune .tmp/runs older than 7 days ──
for ch in ClutchWire SpliceReel FrameDrift; do
    tmp_dir="$GENLAB/$ch/.tmp/runs"
    if [ -d "$tmp_dir" ]; then
        old=0
        for run_dir in $(find "$tmp_dir" -maxdepth 1 -type d -mtime +7 2>/dev/null); do
            # Check against protected paths
            is_protected=false
            while IFS= read -r pdir; do
                if echo "$run_dir" | grep -q "$pdir" 2>/dev/null; then
                    is_protected=true
                    break
                fi
            done <<< "$PROTECTED_PATHS"

            if [ "$is_protected" = true ]; then
                echo "  $ch: PROTECTED $run_dir" >> "$LOG"
            else
                rm -rf "$run_dir"
                old=$((old + 1))
            fi
        done
        echo "  $ch: pruned $old old run dirs" >> "$LOG"
    fi
done

# ── Step 6: CDN cleanup — purge files older than 48h ──
find "$GENLAB/.media/cdn" -name "*.mp4" -mtime +2 -delete 2>/dev/null
echo "  CDN: pruned old files" >> "$LOG"

# ── Step 7: Log rotation ──
bash "$GENLAB/scripts/rotate_logs.sh" >> "$LOG" 2>&1 || true

echo "=== Cleanup finished $(date -u '+%Y-%m-%d %H:%M UTC') ===" >> "$LOG"
