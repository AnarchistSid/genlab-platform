#!/usr/bin/env bash
# rollback.sh — Restore a deployed file to a previous version (R-02).
#
# Why this exists
# ---------------
# ``deploy.sh`` only ships forward; the audit (R-02) called out "no
# rollback". This script reconstructs an earlier version of a file from
# git history and ships THAT via the same checksum-verified path —
# never modifying the working tree.
#
# Usage
# -----
#   ./deploy/scripts/rollback.sh <git-ref> <path1> [<path2> ...]
#
#   git-ref : any rev-parseable ref (a commit SHA, ``HEAD~1``, a tag,
#             ``origin/main@{1.hour.ago}`` etc.)
#   pathN   : repo-root-relative path, same constraints as deploy.sh.
#
# Each file is extracted from ``<git-ref>:<path>``, dropped under
# ``/tmp/genlab-rollback-<run_id>/<path>``, then handed to ``deploy.sh``
# for transfer + checksum verify.
#
# Exit codes
# ----------
#   0 — all files rolled back AND checksum-verified
#   1 — usage error / pre-flight failure
#   2 — git extract failed for one of the paths
#   3 — deploy.sh failed (checksum mismatch or transport)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [ $# -lt 2 ]; then
  cat >&2 <<EOF
Usage: $0 <git-ref> <path1> [<path2> ...]

  git-ref : any rev-parseable ref (e.g. HEAD~1, a SHA, a tag).
  pathN   : repo-root-relative path of a file to roll back.

Each file is extracted from <git-ref>:<path>, then shipped via
deploy.sh which checksum-verifies the transfer end-to-end.

Example: roll the gaming pipeline config back to yesterday's commit
  $0 HEAD~5 CriticalRush/niches/gaming/config/sources.yaml
EOF
  exit 1
fi

git_ref="$1"
shift

# Validate the ref BEFORE creating any staging dirs.
if ! git -C "${REPO_ROOT}" rev-parse --verify "${git_ref}^{commit}" >/dev/null 2>&1; then
  echo "ERROR: '${git_ref}' is not a valid git ref in ${REPO_ROOT}" >&2
  exit 1
fi

run_id="$(date -u +%Y%m%d-%H%M%S)-$$"
stage_dir="/tmp/genlab-rollback-${run_id}"
mkdir -p "${stage_dir}"

echo "[rollback] run_id=${run_id} ref=${git_ref} files=$#"

# Extract each file from git history into the staging dir, preserving
# its repo-root-relative path so the layout matches what deploy.sh
# expects.
staged_paths=()
for rel in "$@"; do
  if [[ "$rel" = /* || "$rel" == *..* ]]; then
    echo "ERROR: '$rel' must be repo-root-relative and free of '..'." >&2
    rm -rf "${stage_dir}"
    exit 1
  fi
  if ! git -C "${REPO_ROOT}" cat-file -e "${git_ref}:${rel}" 2>/dev/null; then
    echo "ERROR: '${rel}' does not exist at ${git_ref}." >&2
    rm -rf "${stage_dir}"
    exit 2
  fi
  mkdir -p "${stage_dir}/$(dirname "${rel}")"
  if ! git -C "${REPO_ROOT}" show "${git_ref}:${rel}" > "${stage_dir}/${rel}"; then
    echo "ERROR: failed to extract '${rel}' from ${git_ref}." >&2
    rm -rf "${stage_dir}"
    exit 2
  fi
  staged_paths+=("${rel}")
done

echo "[rollback] extracted $# file(s) to ${stage_dir}"
echo "[rollback] handing off to deploy.sh for checksum-verified transfer…"

# Shell-out to deploy.sh, but with the staging dir as the repo root so
# the same path-validation + md5 round-trip apply unchanged. We export
# a temporary GENLAB_DEPLOY_REPO_ROOT, but deploy.sh derives its root
# from its own location — so the cleanest path is to invoke a small
# inline wrapper that calls deploy.sh in a way that resolves the staged
# files. Simplest: cp the staged files to their corresponding REPO_ROOT
# paths in a temp clone, then invoke deploy.sh.
#
# To avoid mutating the working tree (the staged ref may not match HEAD),
# we use a sparse approach: feed each staged file's REPO_ROOT-relative
# path to deploy.sh in a subshell whose ``$REPO_ROOT`` we override.

# deploy.sh reads REPO_ROOT from its own script-relative location. Use
# the staging dir as if it WERE the repo by copying deploy.sh into it.
mkdir -p "${stage_dir}/deploy/scripts"
cp "${REPO_ROOT}/deploy/scripts/deploy.sh" "${stage_dir}/deploy/scripts/deploy.sh"

if "${stage_dir}/deploy/scripts/deploy.sh" "${staged_paths[@]}"; then
  echo "[rollback] complete. cleanup staging dir: ${stage_dir}"
  rm -rf "${stage_dir}"
else
  rc=$?
  echo "ERROR: deploy.sh exited with code ${rc}. Staging dir preserved: ${stage_dir}" >&2
  exit 3
fi
