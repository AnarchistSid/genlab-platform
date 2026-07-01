#!/usr/bin/env bash
# ============================================================================
# prune_gh_runner_cache.sh — fast targeted prune of gh-runner cache dirs.
#
# Purpose: replaces the ACTIONS_RUNNER_HOOK_JOB_COMPLETED approach after
# 2026-07-01 verification confirmed the installed runner (v2.335.1) does
# NOT implement the hook feature despite documenting it. Instead of
# a per-job hook we run a lightweight 5-minute timer that only touches
# .cache — fast enough that even a full burst commit window can't fill
# the disk between fires.
#
# What this DOES (fast path — no docker prune, no journal vacuum):
#   * /home/gh-runner/.cache/uv    — Python deps cache
#   * /home/gh-runner/.cache/pip   — pip cache
#   * /home/gh-runner/.cache/pnpm  — Node deps cache
#   * /home/gh-runner/actions-runner/_work/_temp/* older than 15 min
#
# What the hourly disk_cleanup.sh handles instead:
#   * docker system prune (heavier — hourly is enough for images)
#   * /var/log rotated cleanup
#   * apt cache
#   * journal vacuum
#   * _work/_tool (Python installs — expensive to rebuild, keep longer)
#
# Runs as root — /home/gh-runner is owned by gh-runner so root can access.
# Idempotent. Best-effort per section.
# ============================================================================
set -u

log() { echo "[gh-cache-prune $(date -u +%H:%M:%SZ)] $*"; }

free_gb_before=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')

# 1) .cache subdirs (only if they exist to avoid noisy "no such dir")
for dir in /home/gh-runner/.cache/uv /home/gh-runner/.cache/pip /home/gh-runner/.cache/pnpm; do
    if [[ -d "$dir" ]]; then
        size_kb=$(du -sk "$dir" 2>/dev/null | cut -f1)
        # Only prune when > 100 MB to avoid churn on already-clean caches
        if [[ "${size_kb:-0}" -gt 102400 ]]; then
            log "prune $dir (${size_kb} KB)"
            find "$dir" -mindepth 1 -delete 2>/dev/null || true
        fi
    fi
done

# 2) Old per-job tempdirs (>15 min → job is definitely done)
if [[ -d /home/gh-runner/actions-runner/_work/_temp ]]; then
    find /home/gh-runner/actions-runner/_work/_temp -mindepth 1 -maxdepth 1 -mmin +15 \
        -exec rm -rf {} + 2>/dev/null || true
fi

free_gb_after=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
if [[ "$free_gb_after" != "$free_gb_before" ]]; then
    log "freed $((free_gb_after - free_gb_before)) GB (${free_gb_before}→${free_gb_after} GB free)"
fi

exit 0
