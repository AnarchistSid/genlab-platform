#!/usr/bin/env bash
# ============================================================================
# gh_runner_post_job.sh — GitHub Actions self-hosted runner post-job hook.
#
# Runs AFTER every CI job on the self-hosted runner. Wired via
# ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/opt/genlab/scripts/gh_runner_post_job.sh
# in /home/gh-runner/actions-runner/.env (the runner reads this at process
# start; a change requires restart of the runner service).
#
# Why this exists (2026-07-01): the disk_cleanup.timer runs hourly, but a
# high-velocity commit window (10+ pushes in 2h during tonight's Strategist
# ship) had CI producing cache faster than the timer could nuke. This hook
# targets ONLY the cache dirs of the job that JUST FINISHED — millisecond
# operation, no docker prune, no journal vacuum. Complements the
# timer-based cleanup rather than replacing it.
#
# What this DOES nuke:
#   * /home/gh-runner/.cache/uv     — uv cache from THIS job
#   * /home/gh-runner/.cache/pip    — pip cache from THIS job
#   * /home/gh-runner/actions-runner/_work/_temp/*  — job tempdirs
#
# What this does NOT touch:
#   * /home/gh-runner/actions-runner/_work/<repo>/  — checked-out code;
#     may be reused by the next job on same repo
#   * /home/gh-runner/actions-runner/_work/_tool    — tool caches
#     (Python installs etc.); expensive to rebuild
#   * Docker anything — full docker prune is the timer's job
#
# Runner-hook contract:
#   * Runs as the gh-runner user (NOT root)
#   * MUST exit 0 — a failing hook is logged but doesn't fail the job.
#     Any command failure is contained via best-effort semantics.
#   * Stdout/stderr surface in the runner log at /home/gh-runner/actions-runner/_diag/
#
# Wire (on prod, set at runner install time):
#   * /home/gh-runner/actions-runner/.env
#     ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/opt/genlab/scripts/gh_runner_post_job.sh
#   * Runner service restart required after appending the line.
#   * Verify: `grep ACTIONS_RUNNER_HOOK /home/gh-runner/actions-runner/.env`
# ============================================================================

set -u  # Fail on unset vars; DON'T set -e (best-effort per section)

log() { echo "[gh-runner-post-job $(date -u +%H:%M:%SZ)] $*"; }

log "start; job=${GITHUB_JOB:-<unknown>} workflow=${GITHUB_WORKFLOW:-<unknown>}"

# 1) uv cache — the biggest post-job accumulator (Python deps).
if [[ -d /home/gh-runner/.cache/uv ]]; then
    size_kb=$(du -sk /home/gh-runner/.cache/uv 2>/dev/null | cut -f1)
    log "purging .cache/uv (${size_kb}K)..."
    find /home/gh-runner/.cache/uv -mindepth 1 -delete 2>/dev/null || \
        log "WARNING: uv cache prune partial"
fi

# 2) pip cache — same story for jobs that use plain pip
if [[ -d /home/gh-runner/.cache/pip ]]; then
    size_kb=$(du -sk /home/gh-runner/.cache/pip 2>/dev/null | cut -f1)
    log "purging .cache/pip (${size_kb}K)..."
    find /home/gh-runner/.cache/pip -mindepth 1 -delete 2>/dev/null || \
        log "WARNING: pip cache prune partial"
fi

# 3) Per-job temp dirs — the runner creates these but sometimes doesn't
# clean up completely, especially on job cancellation.
if [[ -d /home/gh-runner/actions-runner/_work/_temp ]]; then
    log "purging job _temp dirs..."
    find /home/gh-runner/actions-runner/_work/_temp -mindepth 1 -maxdepth 1 \
        -exec rm -rf {} + 2>/dev/null || true
fi

# 4) Disk state report — surfaces in the runner log so future us can
# see if the hook is keeping up.
free_gb=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
log "complete; disk free: ${free_gb} GB"

# Always exit 0 — a failed cleanup must never mark the job red.
exit 0
