#!/usr/bin/env bash
# The pin for CLEAN-02 §1: a run leaves exactly one worktree.
#
# Eight .bc-* directories were found in $HOME from runs whose cleanup() did not
# clean. They were empty by the time they were measured -- the checkouts had
# gone, the directories had not -- but the shape is the finding: the harness
# created things outside the repo and did not own their removal.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
before="$(git -C "$SRC" worktree list | wc -l | tr -d ' ')"
bash "$SRC/scripts/baseline_compare.sh" --collect-only >/dev/null 2>&1 || true
after="$(git -C "$SRC" worktree list | wc -l | tr -d ' ')"
# `ls` exits non-zero with no matches, and `pipefail` turns that into a
# silent early exit — the script reported rc=1 and printed nothing.
stray="$( { ls -Ad "$HOME"/.bc-* 2>/dev/null || true; } | wc -l | tr -d ' ')"
[ "$after" = "1" ] || { echo "FAIL: $after worktrees after the run (expected 1)"; git -C "$SRC" worktree list; exit 1; }
[ "$stray" = "0" ] || { echo "FAIL: $stray .bc-* directories in \$HOME"; exit 1; }
echo "ok: worktrees before=$before after=$after, \$HOME strays=$stray"
