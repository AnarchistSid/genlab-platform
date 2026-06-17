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
#   0 — all files transferred AND checksum + ownership verified
#       (HEAD-advance step is best-effort — non-zero result there
#       still exits 0 since the files ARE on prod and verified)
#   1 — usage error / pre-flight failure
#   2 — checksum mismatch (deploy aborted)
#   3 — rsync transport failure
#   4 — ownership mismatch on remote — file is not owned by
#       genlab:genlab after deploy (deploy aborted; would have broken
#       pipelines silently in the past — 2026-06-16 outage)
#
# Flags
# -----
#   --no-advance-head   — Skip the post-deploy ``git reset --mixed
#                         origin/main`` step on prod. Use when
#                         deploying a hot-fix from a branch that's
#                         NOT yet merged into main (advancing HEAD
#                         would falsely suggest the deploy is in
#                         main). Default: advance.
#
# The git-HEAD-advance step
# -------------------------
# 2026-06-17 audit found prod's git HEAD had been stuck at PR #237
# for weeks while ~60 newer PRs were merged into main. Every
# subsequent deploy added to the apparent ``git_drift`` count: files
# in the working tree no longer matched the stale HEAD even though
# they DID match current origin/main. The 108-file alert that
# surfaced on 2026-06-17 was 98 false positives (deploys matching
# origin/main) + 10 genuine local diffs.
#
# Fix: after a successful deploy + verify, on prod run
# ``git fetch origin && git reset --mixed origin/main``. This:
#   * Advances HEAD + index to origin/main (no ``--hard``, so
#     working tree is UNTOUCHED — operator edits + branch hot-fixes
#     are preserved as ``M`` in ``git status``)
#   * Files just rsync'd that match origin/main go from ``M`` to
#     clean (the 98-false-positive class disappears)
#   * Files genuinely diverging from origin/main stay ``M``,
#     surfacing the real drift signal cleanly
#
# Idempotent: re-running when HEAD already matches origin/main is
# a no-op.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_HOST="${GENLAB_DEPLOY_HOST:-46.224.237.56}"
DEPLOY_USER="${GENLAB_DEPLOY_USER:-root}"
REMOTE_BASE="/opt/genlab"
LOG_FILE="${REPO_ROOT}/deploy/.deploy.log"

# ── Flag parsing ─────────────────────────────────────────────────────
# Defaults: post-deploy HEAD advance is ON. Consume our recognized
# flags off the front of $@ so the rest is the file list.
ADVANCE_HEAD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-advance-head)
      ADVANCE_HEAD=0
      shift
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "ERROR: unknown flag '$1' (use --no-advance-head or --)" >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

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
  #
  # ``--chown=genlab:genlab`` ensures the file lands owned by the
  # genlab user (not root, the SSH user). Pipelines run as ``genlab``
  # via systemd and need to write back to files like uv.lock during
  # ``uv run`` — root-owned files break the pipeline with
  # "Permission denied" (regression observed 2026-06-16 ai_creators
  # 08:00 IST: uv.lock root-owned → entire pipeline aborted at
  # process startup before any work). The flag requires
  # ``rsync 3.1+`` on both ends; all current prod hosts have 3.2+.
  if ! rsync \
        --quiet \
        --checksum \
        --mkpath \
        --chown=genlab:genlab \
        --chmod=ugo=rwX,Du=rwx,Dgo=rx \
        -e "ssh -o StrictHostKeyChecking=no" \
        "${local_path}" \
        "${DEPLOY_USER}@${DEPLOY_HOST}:${remote_path}"; then
    echo "ERROR: rsync failed for ${rel}" >&2
    echo "  ${rel}  RSYNC_FAILED" >> "${LOG_FILE}"
    exit 3
  fi

  # Post-transfer verify: md5 catches transport corruption + the
  # ownership check catches the silent failure mode that broke every
  # pipeline on 2026-06-16 (uv.lock root-owned → pipeline aborts at
  # "uv run" with EACCES). Root-owned files DON'T mismatch the
  # checksum but the pipeline still can't write them, so the md5
  # gate alone never surfaced this class of bug.
  remote_check="$(ssh -o StrictHostKeyChecking=no \
    "${DEPLOY_USER}@${DEPLOY_HOST}" \
    "md5sum ${remote_path} | awk '{print \$1}'; stat -c '%U:%G' ${remote_path}")"
  remote_sum="$(echo "${remote_check}" | head -1)"
  remote_owner="$(echo "${remote_check}" | tail -1)"

  if [ "${local_sum}" != "${remote_sum}" ]; then
    echo "CHECKSUM MISMATCH for ${rel}:" >&2
    echo "  local : ${local_sum}" >&2
    echo "  remote: ${remote_sum}" >&2
    echo "  ${rel}  MISMATCH local=${local_sum} remote=${remote_sum}" >> "${LOG_FILE}"
    exit 2
  fi

  if [ "${remote_owner}" != "genlab:genlab" ]; then
    echo "OWNERSHIP MISMATCH for ${rel}:" >&2
    echo "  expected: genlab:genlab" >&2
    echo "  got     : ${remote_owner}" >&2
    echo "  ${rel}  BAD_OWNER got=${remote_owner}" >> "${LOG_FILE}"
    exit 4
  fi

  echo "  ${rel}  OK md5=${local_sum} owner=${remote_owner}" >> "${LOG_FILE}"
done

echo "[deploy] all files verified."

# ── Post-deploy: advance prod's git HEAD to origin/main ──────────────
# Best-effort. Failure here doesn't reverse the deploy — the files
# are on prod and verified. We just log + warn so the next operator
# session can investigate.
if [ "${ADVANCE_HEAD}" -eq 1 ]; then
  echo "[deploy] advancing prod git HEAD to origin/main..."
  # Capture before-state for the audit log
  before_sha=$(ssh -o StrictHostKeyChecking=no \
    "${DEPLOY_USER}@${DEPLOY_HOST}" \
    "sudo -u genlab bash -c 'cd ${REMOTE_BASE} && git rev-parse HEAD 2>/dev/null'" \
    2>/dev/null || echo "unknown")

  # ``git fetch origin && git reset --mixed origin/main``:
  #   * --mixed (default) moves HEAD + index but NOT working tree.
  #     Operator hand-edits + branch-ahead-of-main files are
  #     preserved as ``M`` in ``git status`` (now real signal,
  #     not stale-HEAD noise).
  #   * Runs as the ``genlab`` user so the repo's permissions stay
  #     consistent (root running git would chown the index file).
  if ssh -o StrictHostKeyChecking=no \
       "${DEPLOY_USER}@${DEPLOY_HOST}" \
       "sudo -u genlab bash -c 'cd ${REMOTE_BASE} && git fetch origin --quiet 2>&1 && git reset --mixed origin/main --quiet 2>&1'" \
       >/dev/null 2>&1; then
    after_sha=$(ssh -o StrictHostKeyChecking=no \
      "${DEPLOY_USER}@${DEPLOY_HOST}" \
      "sudo -u genlab bash -c 'cd ${REMOTE_BASE} && git rev-parse HEAD 2>/dev/null'" \
      2>/dev/null || echo "unknown")
    if [ "${before_sha}" = "${after_sha}" ]; then
      echo "[deploy] HEAD unchanged (was already at origin/main: ${after_sha:0:8})"
    else
      echo "[deploy] HEAD advanced ${before_sha:0:8} → ${after_sha:0:8}"
    fi
    echo "  HEAD_ADVANCED before=${before_sha:0:8} after=${after_sha:0:8}" \
      >> "${LOG_FILE}"
  else
    # Failure to advance is non-fatal. The files are deployed and
    # verified; we just lose the git_drift visibility improvement.
    echo "[deploy] WARN: git HEAD advance failed (deploy itself succeeded)" >&2
    echo "  HEAD_ADVANCE_FAILED before=${before_sha:0:8}" >> "${LOG_FILE}"
  fi
else
  echo "[deploy] --no-advance-head: skipping git HEAD advance"
  echo "  HEAD_ADVANCE_SKIPPED" >> "${LOG_FILE}"
fi

echo "── ${run_id} done ──" >> "${LOG_FILE}"
