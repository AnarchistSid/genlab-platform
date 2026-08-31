#!/usr/bin/env bash
#
# scripts/disk_cleanup.sh — periodic SYSTEM-level disk cleanup
#
# Scope (what this script touches):
#   * apt cache
#   * journalctl archived journals (older than 2 days)
#   * rotated /var/log files (older than 14 days)
#   * /home/gh-runner/.cache/uv (GH Actions self-hosted runner)
#   * /home/gh-runner/actions-runner/_work/_tool (runner tool cache)
#   * /home/gh-runner/actions-runner/_work/_temp older than 60 min
#     (per-job tempdirs from completed CI jobs)
#   * /opt/genlab/.cache/uv (genlab's own uv cache — unbounded growth
#     from every `uv sync`; freed 2.9 GB when first pruned 2026-07-15)
#   * docker system prune (unused images, stopped containers,
#     dangling volumes — never running data)
#   * /tmp files older than 7 days
#
# OUT OF SCOPE (deliberately):
#   * /opt/genlab/.tmp/runs/* — managed by
#     genlab-quota-monitor.service (continuous, with publish-pending
#     protection via disk_quota._is_published + pending_publish_run_ids)
#   * /opt/genlab/.venv — the production python env
#   * any docker volume in use by genlab-postgres / genlab-redis
#   * /opt/genlab/.backups — 14-day retention self-managed by
#     pg_backup.sh + backup_visual_assets.sh
#
# History:
#   2026-06-29 v1 — INCLUDED .tmp/runs prune at -mtime +3, which
#     deleted media for VISUAL_READY blueprints (cleanup_safety
#     violation: CLAUDE.md is explicit that "Approved posts' media
#     is NEVER deleted by cleanup"). 65 blueprints with scheduled
#     posts lost their renders; operator force-deleted them.
#   2026-06-29 v2 — current. .tmp/runs prune REMOVED. Pure
#     system-level cleanup. genlab data left to its own daemons.
#   2026-07-15 v3 — added /opt/genlab/.cache/uv prune. Genlab's own
#     uv cache had grown to 3.2 GB across a year of `uv sync` calls.
#     `uv cache prune --ci` freed 2.9 GB on first run. Prod disk was
#     oscillating 80-93% used, primarily driven by this unbounded
#     cache. Adding the prune here makes disk_cleanup.sh actually
#     reclaim the biggest genlab-user-owned unmanaged consumer.
#
# Runs as root (touches /home/gh-runner, /var, docker socket — each
# owned by different users). Idempotent. Each section best-effort;
# script always exits 0 so single-section failure never paints the
# timer red (failures logged WARNING for journal review).

set -u

log() {
    echo "[disk-cleanup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

# df -B1G is more reliable than -BM for parsing
free_gb_before=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
log "starting cleanup (disk before: ${free_gb_before} GB free)"

# 1) Apt cache
log "cleaning apt cache..."
apt-get clean 2>&1 | sed 's/^/  /' || log "WARNING: apt clean failed"

# 2) Old systemd journals (keep 2 days)
log "vacuuming journalctl to 2 days..."
journalctl --vacuum-time=2d 2>&1 | tail -2 | sed 's/^/  /' || \
    log "WARNING: journalctl vacuum failed"

# 3) Old log files in /var/log (rotated, >14 days)
log "removing rotated logs older than 14 days..."
find /var/log -name "*.gz" -mtime +14 -delete 2>/dev/null || true
find /var/log -name "*.1" -mtime +14 -delete 2>/dev/null || true
find /var/log/genlab -name "*.log.*" -mtime +7 -delete 2>/dev/null || true

# 3b) Orphaned yt-dlp partials on the media volume
#
# 2026-08-31 incident: /mnt/genlab-media hit 100% (744K free of 49G) and took
# down the anime, gaming and sports pipelines with "No space left on device".
# 36.3 GB of it was 25 abandoned yt-dlp partials under channel-tmp/CriticalRush
# — a single 14 GB .part file, a 4.8 GB .temp.mp4, and dozens at 1-2 GB. The
# oldest was 18 days old.
#
# yt-dlp leaves .part / .temp / .ytdl behind whenever a download is interrupted
# (timeout, OOM, service restart). Nothing removed them: the existing cleanup
# covers /opt/genlab/.tmp/runs, and these live on a different volume entirely.
#
# Guarded on age so an in-flight download is never touched: a partial still
# being written was modified within the last few minutes, and 120 minutes is
# far longer than any single download takes.
MEDIA_ROOT="/mnt/genlab-media/channel-tmp"
if [ -d "$MEDIA_ROOT" ]; then
    ORPHANS=$(find "$MEDIA_ROOT" -type f \
        \( -name "*.part" -o -name "*.temp.mp4" -o -name "*.ytdl" \) \
        -mmin +120 2>/dev/null | wc -l)
    if [ "$ORPHANS" -gt 0 ]; then
        FREED=$(find "$MEDIA_ROOT" -type f \
            \( -name "*.part" -o -name "*.temp.mp4" -o -name "*.ytdl" \) \
            -mmin +120 -printf "%s\n" 2>/dev/null | awk '{s+=$1} END {printf "%.1f", s/1024/1024/1024}')
        log "Pruning $ORPHANS orphaned download partial(s) (~${FREED} GB) from $MEDIA_ROOT"
        find "$MEDIA_ROOT" -type f \
            \( -name "*.part" -o -name "*.temp.mp4" -o -name "*.ytdl" \) \
            -mmin +120 -delete 2>/dev/null || log "WARNING: partial prune incomplete"
    fi
fi

# 4) GH Actions runner — cache + tool cache + old per-job _temp dirs
# All under /home/gh-runner/, NEVER /opt/genlab/. Any cache subdir here
# is fine to nuke — CI jobs re-fetch on next sync.
#
# 2026-07-01: prod PG crashed today when /home/gh-runner/.cache grew
# to 7.3 GB (uv + pip + pnpm etc.) and filled /. The prior version only
# pruned .cache/uv — expanded here to the whole .cache directory since
# CI runners self-repair caches on next invocation.
if [ -d /home/gh-runner/.cache ]; then
    cache_size_kb=$(du -sk /home/gh-runner/.cache 2>/dev/null | cut -f1)
    log "clearing gh-runner .cache (${cache_size_kb} KB before)..."
    find /home/gh-runner/.cache -mindepth 1 -delete 2>/dev/null || \
        log "WARNING: gh-runner .cache prune partial"
fi
if [ -d /home/gh-runner/.local/share/uv ]; then
    log "clearing gh-runner uv state..."
    find /home/gh-runner/.local/share/uv -mindepth 1 -delete 2>/dev/null || true
fi
if [ -d /home/gh-runner/actions-runner/_work/_tool ]; then
    log "removing runner tool cache..."
    rm -rf /home/gh-runner/actions-runner/_work/_tool 2>/dev/null || true
fi
# Per-job tempdirs from completed jobs (>60 min). Never the live one.
find /home/gh-runner/actions-runner/_work -maxdepth 2 -type d -name "_temp" \
    -mmin +60 -exec rm -rf {} + 2>/dev/null || true

# Runner _diag logs — 2026-07-17 addition. Accumulates unbounded
# (~2 files per CI job × ~50 jobs/day → 545 MB / 1214 files >7 days
# observed at audit time). The runner emits fresh diagnostics per
# invocation; anything older than 7 days is post-mortem material
# only. Keeping recent for the "last few jobs failed, why" case.
if [ -d /home/gh-runner/actions-runner/_diag ]; then
    diag_deleted=$(find /home/gh-runner/actions-runner/_diag -type f -mtime +7 2>/dev/null | wc -l)
    find /home/gh-runner/actions-runner/_diag -type f -mtime +7 -delete 2>/dev/null || true
    log "  removed ${diag_deleted} runner _diag files older than 7 days"
fi

# 5) Docker prune — containers, images, build cache, volumes
# `--volumes` only removes UNUSED volumes (genlab-postgres + genlab-redis
# volumes are in use → untouched). `-a` includes all unused images.
log "docker system prune..."
docker system prune -af --volumes 2>&1 | tail -2 | sed 's/^/  /' || \
    log "WARNING: docker prune failed (docker daemon down?)"

# 5b) Genlab's own uv cache — 2026-07-15 addition.
# Every `uv sync` on deploy adds wheels; nothing prunes them. The
# --ci flag removes intermediate build artefacts + entries not in the
# CURRENT lockfile. Next uv sync re-fetches only what's actually
# needed, which is bounded (~150 MB fresh state) rather than 3+ GB
# steady state. Runs as genlab so the ownership + XDG_CACHE_HOME
# resolves to /opt/genlab/.cache/uv/ (root's uv cache would be a
# different dir, unused by prod).
log "pruning /opt/genlab/.cache/uv (genlab-owned)..."
if command -v uv >/dev/null 2>&1; then
    sudo -u genlab bash -c "cd /opt/genlab && uv cache prune --ci 2>&1" | tail -3 | sed 's/^/  /' || \
        log "WARNING: uv cache prune failed"
else
    log "  uv not on PATH — skipping (installed via /usr/local/bin/uv typically)"
fi

# 6) /tmp — anything older than 7 days that isn't a socket/lock
log "removing /tmp files older than 7 days..."
find /tmp -maxdepth 2 -type f -mtime +7 -delete 2>/dev/null || true
find /tmp -maxdepth 2 -type d -empty -mtime +7 -delete 2>/dev/null || true

# NOTE: /opt/genlab/.tmp/runs is NOT touched. genlab-quota-monitor.service
# (running continuously, restart=always) handles eviction with proper
# publish-pending protection via disk_quota._is_published +
# _get_pending_publish_run_ids. Replicating that logic here would be
# both a maintenance burden + a re-introduction-of-bug risk. Defer.

free_gb_after=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
freed_gb=$((free_gb_after - free_gb_before))
log "complete — disk after: ${free_gb_after} GB free (freed: ${freed_gb} GB this run)"

# Soft warning if still tight after cleanup
if [ "$free_gb_after" -lt 2 ]; then
    log "WARNING: only ${free_gb_after} GB free after cleanup — consider VPS disk upgrade or investigate /opt/genlab/.tmp growth"
fi

exit 0
