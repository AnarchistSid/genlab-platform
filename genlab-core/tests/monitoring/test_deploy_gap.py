"""main running ahead of what is DEPLOYED is invisible to systemctl.

2026-09-17: prod took no deploy between 08-21 and 09-15 -- 25 days, 95 commits.
A loudness fix committed 09-12 did not exist in production until 09-15, and the
render pipeline failed audio validation 3-5 times a day in between on a bug that
was already fixed in `main`. Nothing alerted, because nothing was down.
"""

import subprocess
import time

import pytest
from genlab_core.monitoring.checks.infrastructure import check_deploy_gap

DEPLOYED = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
REMOTE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def prod(tmp_path, monkeypatch):
    monkeypatch.setenv("GENLAB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("GENLAB_DEPLOY_GAP_MAX_HOURS", raising=False)
    monkeypatch.delenv("GENLAB_DEPLOY_GAP_MAX_COMMITS", raising=False)
    (tmp_path / ".version.env").write_text(
        f"GENLAB_GIT_COMMIT={DEPLOYED}\nGENLAB_BUILD_TIME=2026-08-21T00:00:00Z\n")
    return tmp_path


def _git(monkeypatch, *, behind: int, age_hours: float, remote=REMOTE,
         fetch_rc: int = 0, count_rc: int = 0):
    def fake(cmd, **kw):
        args = cmd[3:] if len(cmd) > 3 else []
        out, rc = "", 0
        if args[:1] == ["fetch"]:
            rc = fetch_rc
        elif args[:1] == ["rev-parse"]:
            out = remote
        elif args[:1] == ["rev-list"]:
            out, rc = str(behind), count_rc
        elif args[:1] == ["log"]:
            out = str(int(time.time() - age_hours * 3600))
        return subprocess.CompletedProcess(cmd, rc, out, "")
    monkeypatch.setattr("subprocess.run", fake)


def test_quiet_when_deployed_sha_matches_origin(prod, monkeypatch):
    _git(monkeypatch, behind=0, age_hours=0, remote=DEPLOYED)
    assert check_deploy_gap() == []


def test_quiet_inside_both_thresholds(prod, monkeypatch):
    _git(monkeypatch, behind=3, age_hours=5)
    assert check_deploy_gap() == []


def test_alerts_on_commit_count_alone(prod, monkeypatch):
    _git(monkeypatch, behind=11, age_hours=1)
    a = check_deploy_gap()
    assert len(a) == 1 and a[0].check == "deploy_gap"
    assert a[0].details["commits_behind"] == 11


def test_alerts_on_age_alone(prod, monkeypatch):
    """A single commit sitting undeployed for days is the 09-12 loudness fix."""
    _git(monkeypatch, behind=1, age_hours=72)
    a = check_deploy_gap()
    assert len(a) == 1 and a[0].details["oldest_undeployed_hours"] == 72.0


def test_the_25_day_gap_is_critical_not_a_warning(prod, monkeypatch):
    _git(monkeypatch, behind=95, age_hours=25 * 24)
    a = check_deploy_gap()
    assert a[0].severity == "critical"
    assert "deploy.sh --apply" in a[0].auto_fix


def test_message_names_the_deployed_sha_so_it_is_actionable(prod, monkeypatch):
    _git(monkeypatch, behind=20, age_hours=100)
    assert DEPLOYED[:8] in check_deploy_gap()[0].message


def test_unreachable_remote_is_silent_not_a_false_alert(prod, monkeypatch):
    _git(monkeypatch, behind=99, age_hours=999, fetch_rc=1)
    assert check_deploy_gap() == []


def test_deployed_sha_absent_from_history_is_silent(prod, monkeypatch):
    """Force-push or shallow clone: rev-list fails. Do not invent a gap."""
    _git(monkeypatch, behind=0, age_hours=0, count_rc=128)
    assert check_deploy_gap() == []


def test_missing_version_env_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("GENLAB_PROJECT_ROOT", str(tmp_path))
    assert check_deploy_gap() == []


def test_thresholds_are_tunable_without_a_deploy(prod, monkeypatch):
    monkeypatch.setenv("GENLAB_DEPLOY_GAP_MAX_COMMITS", "2")
    _git(monkeypatch, behind=3, age_hours=1)
    assert len(check_deploy_gap()) == 1


def test_it_reads_version_env_not_the_working_trees_HEAD(prod, monkeypatch):
    """`git pull` advances HEAD without restarting one service; .version.env is
    stamped by deploy.sh and is the only honest record of what is RUNNING."""
    seen = []

    def fake(cmd, **kw):
        seen.append(cmd[3:] if len(cmd) > 3 else [])
        args = seen[-1]
        if args[:1] == ["rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, REMOTE, "")
        if args[:1] == ["rev-list"]:
            return subprocess.CompletedProcess(cmd, 0, "12", "")
        if args[:1] == ["log"]:
            return subprocess.CompletedProcess(cmd, 0, str(int(time.time())), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("subprocess.run", fake)
    check_deploy_gap()
    assert ["rev-parse", "origin/main"] in seen
    assert not any(a[:2] == ["rev-parse", "HEAD"] for a in seen)
