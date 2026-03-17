"""Tests for genlab_core.tools.safe_push."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from genlab_core.tools.safe_push import (
    PROTECTED_BRANCHES,
    main,
)


def _make_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Helper to build a CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["git"], stdout=stdout, stderr=stderr, returncode=returncode,
    )


class TestMainBlocked:
    """Push to main/master is blocked without --force-main."""

    @patch("genlab_core.tools.safe_push._run_git")
    def test_push_to_main_blocked(self, mock_git):
        """Pushing to 'main' without --force-main exits with code 1."""
        mock_git.return_value = _make_completed(stdout="main")
        rc = main([])
        assert rc == 1

    @patch("genlab_core.tools.safe_push._run_git")
    def test_push_to_master_blocked(self, mock_git):
        """Pushing to 'master' without --force-main exits with code 1."""
        mock_git.return_value = _make_completed(stdout="master")
        rc = main([])
        assert rc == 1


class TestMainForceOverride:
    """Push to main/master proceeds with --force-main."""

    @patch("genlab_core.tools.safe_push.push_branch")
    @patch("genlab_core.tools.safe_push.get_diff_stat", return_value="")
    @patch("genlab_core.tools.safe_push.get_unpushed_commits", return_value="abc1234 some commit")
    @patch("genlab_core.tools.safe_push.has_uncommitted_changes", return_value=False)
    @patch("genlab_core.tools.safe_push.get_current_branch", return_value="main")
    def test_force_main_allows_push(self, mock_branch, mock_uc, mock_commits, mock_stat, mock_push):
        """--force-main allows pushing to main."""
        mock_push.return_value = _make_completed()
        rc = main(["--force-main"])
        assert rc == 0
        mock_push.assert_called_once_with("main")


class TestFeatureBranch:
    """Push to feature branch proceeds normally."""

    @patch("genlab_core.tools.safe_push.push_branch")
    @patch("genlab_core.tools.safe_push.get_diff_stat", return_value="1 file changed")
    @patch("genlab_core.tools.safe_push.get_unpushed_commits", return_value="abc1234 feat: add widget")
    @patch("genlab_core.tools.safe_push.has_uncommitted_changes", return_value=False)
    @patch("genlab_core.tools.safe_push.get_current_branch", return_value="feat/my-branch")
    def test_feature_branch_push(self, mock_branch, mock_uc, mock_commits, mock_stat, mock_push):
        """Feature branch push proceeds without --force-main."""
        mock_push.return_value = _make_completed()
        rc = main([])
        assert rc == 0
        mock_push.assert_called_once_with("feat/my-branch")

    @patch("genlab_core.tools.safe_push.push_branch")
    @patch("genlab_core.tools.safe_push.get_diff_stat", return_value="")
    @patch("genlab_core.tools.safe_push.get_unpushed_commits", return_value="abc1234 fix stuff")
    @patch("genlab_core.tools.safe_push.has_uncommitted_changes", return_value=False)
    @patch("genlab_core.tools.safe_push.get_current_branch", return_value="fix/bug-123")
    def test_push_failure_returns_nonzero(self, mock_branch, mock_uc, mock_commits, mock_stat, mock_push):
        """Non-zero git push returncode is propagated."""
        mock_push.return_value = _make_completed(returncode=128, stderr="remote rejected")
        rc = main([])
        assert rc == 128


class TestUncommittedChangesWarning:
    """Uncommitted changes trigger a warning but do not block."""

    @patch("genlab_core.tools.safe_push.push_branch")
    @patch("genlab_core.tools.safe_push.get_diff_stat", return_value="")
    @patch("genlab_core.tools.safe_push.get_unpushed_commits", return_value="abc1234 wip")
    @patch("genlab_core.tools.safe_push.has_uncommitted_changes", return_value=True)
    @patch("genlab_core.tools.safe_push.get_current_branch", return_value="feat/dirty")
    def test_uncommitted_changes_warns_but_proceeds(
        self, mock_branch, mock_uc, mock_commits, mock_stat, mock_push, capsys,
    ):
        """Uncommitted changes produce a warning on stderr but push proceeds."""
        mock_push.return_value = _make_completed()
        rc = main([])
        assert rc == 0
        captured = capsys.readouterr()
        assert "uncommitted changes" in captured.err.lower()
        mock_push.assert_called_once()


class TestNothingToPush:
    """No new commits exits early with 0."""

    @patch("genlab_core.tools.safe_push.has_uncommitted_changes", return_value=False)
    @patch("genlab_core.tools.safe_push.get_unpushed_commits", return_value="")
    @patch("genlab_core.tools.safe_push.get_current_branch", return_value="feat/empty")
    def test_no_commits_exits_zero(self, mock_branch, mock_commits, mock_uc):
        """When there are no unpushed commits, exit 0 without pushing."""
        rc = main([])
        assert rc == 0


class TestProtectedBranchSet:
    """Verify the protected branch constants."""

    def test_main_and_master_protected(self):
        assert "main" in PROTECTED_BRANCHES
        assert "master" in PROTECTED_BRANCHES
