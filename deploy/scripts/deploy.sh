#!/usr/bin/env bash
# deploy.sh — Safe scp-replacement for GenLab production (R-02).
#
# Why this exists
# ---------------
# The 2026-05-18 "Cluster A" incident published a Blackbox Brief reel with
# SpliceReel branding because multiple ``niche.yaml`` files were scp'd to
# a flat /tmp/genlab_deploy4/ directory; each overwrote the previous, then
# a wildcard cp painted SR's config over BB's destination.
#
# This wrapper enforces three properties the bare ``scp`` cannot:
#
#   1. **Explicit src→dst per file** — no flat staging, no wildcard cp.
#      You pass paths as relative-to-repo-root and we send them to the
#      EXACT same path on the remote box. No reinterpretation.
#
#   2. **Post-transfer checksum verify** — every file gets an md5 round-
#      trip immediately after send. Mismatch aborts and exits non-zero
#      before any service restart is triggered.
#
#   3. **Audit log** — each invocation appends to ``deploy/.deploy.log``
#      (gitignored) so when something goes sideways you know exactly
#      which files were touched, when, and the checksums.
#
# Usage
# -----
#   ./deploy/scripts/deploy.sh path/to/file1 path/to/file2 ...
#
# Each path must:
#   * be relative to the repo root (this script's ``../..`` is used as base)
#   * exist locally and resolve to a regular file (no directories — rsync
#     a directory by listing its files explicitly)
#   * land at the SAME path under ``/opt/genlab/`` on the remote box
#
# Env vars
# --------
#   GENLAB_DEPLOY_HOST   — target host (default: 46.224.237.56)
#   GENLAB_DEPLOY_USER   — ssh user (default: root)
#
# Exit codes
# ----------
#   0 — all files transferred AND checksum-verified
#   1 — usage error / pre-flight failure
#   2 — checksum mismatch (deploy aborted)
#   3 — rsync transport failure

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${GENLAB_DEPLOY_HOST:-46.224.237.56}"
DEPLOY_USER="${GENLAB_DEPLOY_USER:-root}"
REMOTE_BASE="/opt/genlab"
LOG_FILE="${REPO_ROOT}/deploy/.deploy.log"

# ── Pre-flight ────────────────────────────────────────────────────────
#
# Argument validation runs BEFORE binary-discovery so usage/safety
# errors surface even on hosts missing md5sum/rsync. The transport-
# binary checks come right after, before any network egress.

if [ $# -eq 0 ]; then
  cat >&2 <<EOF
Usage: $0 <path1> [<path2> ...]

Each path is relative to the repo root and is sent to the SAME path
under ${REMOTE_BASE}/ on ${DEPLOY_USER}@${DEPLOY_HOST}.

See deploy/DEPLOY.md for the post-deploy verify + service restart
playbook.
EOF
  exit 1
fi

# Validate every requested file BEFORE transferring any of them.
for rel in "$@"; do
  if [[ "$rel" = /* ]]; then
    echo "ERROR: '$rel' is an absolute path. Use repo-root-relative paths only." >&2
    exit 1
  fi
  if [[ "$rel" == *..* ]]; then
    echo "ERROR: '$rel' contains '..' — path traversal is forbidden." >&2
    exit 1
  fi
  if [ ! -f "${REPO_ROOT}/${rel}" ]; then
    echo "ERROR: ${REPO_ROOT}/${rel} is not a regular file." >&2
    exit 1
  fi
done

# Detect the right md5 binary (BSD ``md5 -q`` on macOS, GNU ``md5sum``
# on Linux). Deferred until after argument-validation so usage errors
# fire even on hosts without an md5 binary.
if command -v md5sum >/dev/null 2>&1; then
  local_md5() { md5sum "$1" | awk '{print $1}'; }
elif command -v md5 >/dev/null 2>&1; then
  local_md5() { md5 -q "$1"; }
else
  echo "ERROR: neither md5sum nor md5 found on this host" >&2
  exit 1
fi

# ── Transfer + verify ──────────────────────────────────────────────────

run_id="$(date -u +%Y%m%d-%H%M%S)-$$"
echo "[deploy] run_id=${run_id} host=${DEPLOY_HOST} files=$#"

# Append a header to the audit log.
{
  echo "── ${run_id} ──"
  echo "host=${DEPLOY_USER}@${DEPLOY_HOST}"
  echo "user=$(whoami)@$(hostname)"
  echo "git_sha=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo 'unknown')"
} >> "${LOG_FILE}"

for rel in "$@"; do
  local_path="${REPO_ROOT}/${rel}"
  remote_path="${REMOTE_BASE}/${rel}"
  local_sum="$(local_md5 "${local_path}")"

  echo "[deploy] ${rel}  local_md5=${local_sum}"

  # rsync over ssh, creating intermediate directories as needed.
  # ``--mkpath`` is rsync 3.2+; fall back to a manual ssh mkdir on older
  # rsync versions if needed.
  if ! rsync \
        --quiet \
        --checksum \
        --mkpath \
        -e "ssh -o StrictHostKeyChecking=no" \
        "${local_path}" \
        "${DEPLOY_USER}@${DEPLOY_HOST}:${remote_path}"; then
    echo "ERROR: rsync failed for ${rel}" >&2
    echo "  ${rel}  RSYNC_FAILED" >> "${LOG_FILE}"
    exit 3
  fi

  # Post-transfer checksum verify — the audit's "rsync+checksum verify"
  # recommendation, made non-skippable.
  remote_sum="$(ssh -o StrictHostKeyChecking=no \
    "${DEPLOY_USER}@${DEPLOY_HOST}" \
    "md5sum ${remote_path} | awk '{print \$1}'")"

  if [ "${local_sum}" != "${remote_sum}" ]; then
    echo "CHECKSUM MISMATCH for ${rel}:" >&2
    echo "  local : ${local_sum}" >&2
    echo "  remote: ${remote_sum}" >&2
    echo "  ${rel}  MISMATCH local=${local_sum} remote=${remote_sum}" >> "${LOG_FILE}"
    exit 2
  fi

  echo "  ${rel}  OK md5=${local_sum}" >> "${LOG_FILE}"
done

echo "[deploy] all files verified."
echo "── ${run_id} done ──" >> "${LOG_FILE}"
