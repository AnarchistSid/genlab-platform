"""Safe git push wrapper with branch protection.

Prevents accidental pushes to main/master. Shows diff stats before pushing.
Always uses --set-upstream for new branches.

Usage:
    python -m genlab_core.tools.safe_push
    python -m genlab_core.tools.safe_push --force-main  # explicit override
"""
from __future__ import annotations

import argparse
import subprocess
import sys

PROTECTED_BRANCHES = {"main", "master"}


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=check,
    )


def get_current_branch() -> str:
    """Return the name of the current git branch."""
    result = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip()


def has_uncommitted_changes() -> bool:
    """Return True if there are uncommitted changes in the working tree."""
    result = _run_git("status", "--porcelain")
    return bool(result.stdout.strip())


def get_unpushed_commits(branch: str) -> str:
    """Return oneline log of commits not yet pushed to origin."""
    # Try upstream first, fall back to origin/<branch>
    result = _run_git(
        "log", "--oneline", f"origin/{branch}..HEAD", check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return "(new branch — all local commits will be pushed)"


def get_diff_stat(branch: str) -> str:
    """Return diff --stat for unpushed changes."""
    result = _run_git("diff", "--stat", f"origin/{branch}..HEAD", check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    return "(new branch — diff stat unavailable until first push)"


def push_branch(branch: str) -> subprocess.CompletedProcess[str]:
    """Push the branch to origin with --set-upstream."""
    return _run_git("push", "-u", "origin", branch, check=False)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the safe push CLI."""
    parser = argparse.ArgumentParser(
        description="Safe git push with branch protection.",
    )
    parser.add_argument(
        "--force-main",
        action="store_true",
        help="Allow pushing to main/master (explicit override).",
    )
    args = parser.parse_args(argv)

    # 1. Get current branch
    try:
        branch = get_current_branch()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: Not a git repository or git is not installed.", file=sys.stderr)
        return 1

    # 2. Check protected branch
    if branch in PROTECTED_BRANCHES and not args.force_main:
        print(
            f"BLOCKED: Refusing to push to protected branch '{branch}'.\n"
            f"Use --force-main to override.",
            file=sys.stderr,
        )
        return 1

    # 3. Warn about uncommitted changes
    if has_uncommitted_changes():
        print("WARNING: You have uncommitted changes.", file=sys.stderr)

    # 4. Show what would be pushed
    commits = get_unpushed_commits(branch)
    if commits:
        print(f"Commits to push on '{branch}':")
        print(commits)
    else:
        print(f"No new commits to push on '{branch}'.")
        return 0

    stat = get_diff_stat(branch)
    if stat:
        print(f"\nDiff stat:\n{stat}")

    # 5. Push
    print(f"\nPushing '{branch}' to origin...")
    result = push_branch(branch)
    if result.returncode != 0:
        print(f"Push failed:\n{result.stderr}", file=sys.stderr)
        return result.returncode

    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        # git push often writes progress to stderr even on success
        print(result.stderr.strip())

    print("Push successful.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
