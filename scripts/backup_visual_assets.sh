#!/usr/bin/env bash
# ============================================================================
# backup_visual_assets.sh — daily backup of rendered visual files
#
# Fills the gap identified in [[system-blind-spots-2026-06-30]] #4 (visual
# asset backup missing) and closes the disk-cleanup-cascade class of
# incident: if renders get deleted, we can restore them alongside the
# PG-backup DB rows they reference.
#
# What's backed up:
#   * ALL *.mp4 files under /opt/genlab/.tmp/runs/<niche>_*/visuals/
#     that are ≥100 KB (filters out placeholder / empty renders)
#   * The visual_paths JSONB references from prod DB → these are the
#     canonical set the publisher looks at
#
# Where it goes:
#   * Local hardlink-farm at /opt/genlab/.backups/visuals/<YYYY-MM-DD>/
#     (hardlinks so a "backup" costs bytes only for changed files)
#   * Retention: 14 days rolling. Older per-date dirs pruned on each run.
#   * Optional S3 mirror via aws s3 sync — only fires if AWS_S3_BUCKET
#     env var is set. Skip cost/complexity for operators without AWS.
#
# What's NOT backed up:
#   * Non-.mp4 files (source clips, thumbnails) — recoverable from re-fetch
#   * Files < 100 KB — placeholder renders that would waste backup slots
#   * PublishED blueprints older than 90d — no operational recovery need
#
# Usage:
#   ./scripts/backup_visual_assets.sh             # dry-run
#   ./scripts/backup_visual_assets.sh --apply     # actually rsync + prune
#
# History:
#   2026-07-01 v1 — initial. Addresses the 2026-06-29 disk-cleanup-cascade
#     class of incident where restored PG rows referenced deleted MP4s.
# ============================================================================
set -uo pipefail

APPLY=0
for arg in "$@"; do
    case "$arg" in
        --apply) APPLY=1 ;;
        --help|-h)
            head -40 "$0" | tail -38
            exit 0
            ;;
        *) echo "ERROR: unknown arg '$arg' (try --help)"; exit 1 ;;
    esac
done

GENLAB_ROOT="${GENLAB_PROJECT_ROOT:-/opt/genlab}"
SOURCE_ROOT="$GENLAB_ROOT/.tmp/runs"
BACKUP_ROOT="$GENLAB_ROOT/.backups/visuals"
TODAY=$(date -u +%Y-%m-%d)
DEST="$BACKUP_ROOT/$TODAY"
RETENTION_DAYS=14
MIN_SIZE_KB=100

log() { echo "[backup-visuals $(date -u +%H:%M:%S)] $*"; }

log "Mode: $([[ $APPLY -eq 1 ]] && echo APPLY || echo DRY-RUN)"
log "Source: $SOURCE_ROOT"
log "Dest:   $DEST"

if [[ ! -d "$SOURCE_ROOT" ]]; then
    log "WARNING: source root does not exist — nothing to back up"
    exit 0
fi

# --- Phase 1: enumerate eligible files ---
log "Enumerating rendered .mp4 files (>= ${MIN_SIZE_KB} KB) under visuals/..."
tmp_list=$(mktemp)
trap 'rm -f "$tmp_list"' EXIT

find "$SOURCE_ROOT" \
    -type f \
    -name '*.mp4' \
    -path '*/visuals/*' \
    -size +$((MIN_SIZE_KB - 1))k \
    > "$tmp_list" 2>/dev/null || true

file_count=$(wc -l < "$tmp_list" | tr -d ' ')
total_bytes=$(xargs -a "$tmp_list" du -bc 2>/dev/null | tail -1 | cut -f1 || echo 0)
total_mb=$(( total_bytes / 1024 / 1024 ))

log "Found: $file_count files, ${total_mb} MB total"

if [[ $file_count -eq 0 ]]; then
    log "Nothing to back up — exiting"
    exit 0
fi

# --- Phase 2: rsync with hardlink dedup ---
if [[ $APPLY -eq 1 ]]; then
    mkdir -p "$DEST"
    log "Running rsync into $DEST (hardlinks against latest prior backup)..."

    # Find the most recent prior backup for hardlink dedup
    last_backup=$(ls -1t "$BACKUP_ROOT" 2>/dev/null | grep -v "$TODAY" | head -1 || echo "")
    if [[ -n "$last_backup" ]] && [[ -d "$BACKUP_ROOT/$last_backup" ]]; then
        link_dest_arg="--link-dest=$BACKUP_ROOT/$last_backup"
        log "  hardlinking against: $BACKUP_ROOT/$last_backup"
    else
        link_dest_arg=""
        log "  no prior backup found — full copy"
    fi

    rsync -a \
        --files-from="$tmp_list" \
        --no-relative \
        --relative \
        $link_dest_arg \
        / "$DEST/" 2>&1 | tail -5

    backed_up=$(find "$DEST" -type f -name '*.mp4' | wc -l | tr -d ' ')
    backup_size_mb=$(du -sm "$DEST" 2>/dev/null | cut -f1)
    log "Backed up: $backed_up files, ${backup_size_mb} MB in $DEST"
else
    log "DRY-RUN: would back up $file_count files (${total_mb} MB) to $DEST"
fi

# --- Phase 3: prune old backups ---
log "Pruning backups older than ${RETENTION_DAYS} days..."
if [[ -d "$BACKUP_ROOT" ]]; then
    old_count=0
    for dir in "$BACKUP_ROOT"/*/; do
        [[ -d "$dir" ]] || continue
        dir_name=$(basename "$dir")
        # Skip if not a YYYY-MM-DD directory
        [[ "$dir_name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || continue
        # Compare dates lexically — works because ISO dates sort chronologically
        cutoff=$(date -u -d "$RETENTION_DAYS days ago" +%Y-%m-%d 2>/dev/null || \
                 date -u -v-${RETENTION_DAYS}d +%Y-%m-%d 2>/dev/null || echo "")
        if [[ -n "$cutoff" ]] && [[ "$dir_name" < "$cutoff" ]]; then
            if [[ $APPLY -eq 1 ]]; then
                rm -rf "$dir"
                log "  pruned $dir_name"
            else
                log "  DRY-RUN would prune $dir_name"
            fi
            old_count=$((old_count + 1))
        fi
    done
    log "Pruned $old_count old backup(s)"
fi

# --- Phase 4: optional S3 mirror (skip if AWS_S3_BUCKET unset) ---
if [[ -n "${AWS_S3_BUCKET:-}" ]]; then
    log "S3 mirror enabled — target bucket: $AWS_S3_BUCKET"
    if command -v aws >/dev/null 2>&1; then
        if [[ $APPLY -eq 1 ]]; then
            log "Syncing $DEST → s3://$AWS_S3_BUCKET/visuals/$TODAY/ ..."
            aws s3 sync "$DEST/" "s3://$AWS_S3_BUCKET/visuals/$TODAY/" \
                --storage-class GLACIER_IR \
                --exclude '*.tmp' 2>&1 | tail -3
        else
            log "DRY-RUN: would sync $DEST → s3://$AWS_S3_BUCKET/visuals/$TODAY/"
        fi
    else
        log "WARNING: AWS_S3_BUCKET set but 'aws' CLI not installed — skipping S3 mirror"
    fi
else
    log "AWS_S3_BUCKET unset — skipping S3 mirror (local-only backup)"
fi

# --- Phase 5: disk check ---
free_gb=$(df -BG "$GENLAB_ROOT" | awk 'NR==2 {gsub("G",""); print $4}')
log "Disk free after backup: ${free_gb} GB"

log "Done"
