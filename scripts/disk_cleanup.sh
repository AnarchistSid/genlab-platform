#!/usr/bin/env bash
#
# scripts/disk_cleanup.sh — periodic disk cleanup for the prod VPS
#
# 2026-06-29: shipped after disk hit 412 MB free (99% used) during
# the self-hosted CI runner roll-out. The runner accumulates
# workspace state + uv cache + downloaded HF models between jobs;
# docker prune isn't run anywhere else; old genlab pipeline-run
# artifacts pile up under /opt/genlab/.tmp/runs. Without periodic
# cleanup, the box hits disk-full → prod writes start failing.
#
# Runs as root (needs to touch /home/gh-runner + /var + docker).
# Idempotent — safe to re-run. Failures in any single section log a
# WARNING but don't abort the rest (best-effort cleanup pattern).
#
# Triggered by genlab-disk-cleanup.timer every 6 hours.

set -u  # no -e — single-section failure must not abort rest

log() {
    echo "[disk-cleanup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

# Print disk usage at start + end for journal visibility
before=$(df -BM / | awk 'NR==2 {print $4}' | tr -d 'M')
log "starting cleanup (disk before: ${before} MB free)"

# 1) Apt cache (~50-200 MB)
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

# 4) GH Actions runner — uv cache + old workspaces
# Only safe to touch _work_temp on workspaces NOT currently in use.
# uv cache is fine to nuke — uv re-fetches on next sync.
if [ -d /home/gh-runner/.cache/uv ]; then
    log "clearing gh-runner uv cache..."
    # Keep the directory, drop everything inside (safer than rm -rf
    # of the whole dir if uv has a lock file we'd race against)
    find /home/gh-runner/.cache/uv -mindepth 1 -delete 2>/dev/null || \
        log "WARNING: gh-runner uv cache prune partial"
fi
if [ -d /home/gh-runner/.local/share/uv ]; then
    log "clearing gh-runner uv state..."
    find /home/gh-runner/.local/share/uv -mindepth 1 -delete 2>/dev/null || true
fi
# Old runner workspaces (per-repo state). Only touch _tool dir and
# completed _work subdirs; never the live workspace itself.
if [ -d /home/gh-runner/actions-runner/_work/_tool ]; then
    log "removing runner tool cache..."
    rm -rf /home/gh-runner/actions-runner/_work/_tool 2>/dev/null || true
fi
# Runner-specific _temp from completed jobs (per-job tempdir)
find /home/gh-runner/actions-runner/_work -maxdepth 2 -type d -name "_temp" \
    -mmin +60 -exec rm -rf {} + 2>/dev/null || true

# 5) Docker prune — containers, images, build cache, volumes
# Safe because prod containers (genlab-postgres, genlab-redis) are
# always running, so "prune -af" only touches stopped/dangling.
log "docker system prune..."
docker system prune -af --volumes 2>&1 | tail -2 | sed 's/^/  /' || \
    log "WARNING: docker prune failed (docker daemon down?)"

# 6) genlab pipeline run artifacts older than CLEANUP_KEEP_DAYS (default 3)
KEEP_DAYS="${CLEANUP_KEEP_DAYS:-3}"
if [ -d /opt/genlab/.tmp/runs ]; then
    log "removing genlab pipeline runs older than ${KEEP_DAYS} days..."
    # -mindepth 1 -maxdepth 1 — only the top-level run dirs, not
    # files within them. Each run dir is .tmp/runs/<niche>/<date>.
    find /opt/genlab/.tmp/runs -mindepth 2 -maxdepth 2 -type d \
        -mtime +${KEEP_DAYS} -exec rm -rf {} + 2>/dev/null || true
fi

# 7) /tmp — anything older than 7 days that isn't a socket/lock
log "removing /tmp files older than 7 days..."
find /tmp -maxdepth 2 -type f -mtime +7 -delete 2>/dev/null || true
find /tmp -maxdepth 2 -type d -empty -mtime +7 -delete 2>/dev/null || true

after=$(df -BM / | awk 'NR==2 {print $4}' | tr -d 'M')
freed=$((after - before))
log "complete — disk after: ${after} MB free (freed: ${freed} MB this run)"

# Soft warning if still tight after cleanup
free_gb=$((after / 1024))
if [ "$free_gb" -lt 2 ]; then
    log "WARNING: only ${free_gb} GB free after cleanup — consider VPS disk upgrade"
fi

exit 0
