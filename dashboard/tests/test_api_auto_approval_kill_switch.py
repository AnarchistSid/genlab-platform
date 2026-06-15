"""Pins for /api/v1/auto-approval/kill-switch (D3.10).

File-backed global kill switch the worker also reads. The dashboard
endpoint and the worker MUST stay in lockstep about what "active"
means — the env var and file are both honoured.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def _csrf(client):
    """Header bundle to pass CSRF middleware on POST/PATCH/DELETE."""
    return {
        "X-CSRF-Token": client.get("/api/csrf-token").get_json()["csrf_token"],
        "Content-Type": "application/json",
    }


@pytest.fixture
def kill_file(tmp_path, monkeypatch):
    """Point the kill-switch file to a per-test temp path."""
    path = tmp_path / "kill"
    monkeypatch.setenv("GENLAB_AUTO_APPROVE_KILL_FILE", str(path))
    # Always start from a clean state — env var unset.
    monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
    return path


class TestGetKillSwitch:
    def test_returns_none_when_neither_set(self, client, kill_file):
        r = client.get("/api/v1/auto-approval/kill-switch")
        assert r.status_code == 200
        data = r.get_json()["data"]
        assert data["active"] is False
        assert data["source"] == "none"
        assert data["env_var_set"] is False

    def test_detects_file_only(self, client, kill_file):
        kill_file.write_text("test\n")
        r = client.get("/api/v1/auto-approval/kill-switch")
        data = r.get_json()["data"]
        assert data["active"] is True
        assert data["source"] == "file"
        assert data["env_var_set"] is False

    def test_detects_env_only(self, client, kill_file, monkeypatch):
        monkeypatch.setenv("GENLAB_AUTO_APPROVE_DISABLED", "1")
        r = client.get("/api/v1/auto-approval/kill-switch")
        data = r.get_json()["data"]
        assert data["active"] is True
        assert data["source"] == "env"
        assert data["env_var_set"] is True

    def test_detects_both(self, client, kill_file, monkeypatch):
        kill_file.write_text("test\n")
        monkeypatch.setenv("GENLAB_AUTO_APPROVE_DISABLED", "1")
        r = client.get("/api/v1/auto-approval/kill-switch")
        data = r.get_json()["data"]
        assert data["active"] is True
        assert data["source"] == "both"

    def test_env_falsey_values_treated_as_unset(self, client, kill_file, monkeypatch):
        """Empty / 0 / false / False are NOT active (matches worker behaviour)."""
        for falsy in ["", "0", "false", "False"]:
            monkeypatch.setenv("GENLAB_AUTO_APPROVE_DISABLED", falsy)
            r = client.get("/api/v1/auto-approval/kill-switch")
            data = r.get_json()["data"]
            assert data["env_var_set"] is False, f"falsy {falsy!r} was treated as set"


class TestSetKillSwitch:
    def test_post_active_true_creates_file(self, client, kill_file):
        r = client.post(
            "/api/v1/auto-approval/kill-switch",
            json={"active": True},
            headers=_csrf(client),
        )
        assert r.status_code == 200
        assert kill_file.exists()
        # The body content is operator-facing — must mention how to clear.
        assert "re-enable" in kill_file.read_text() or "active=false" in kill_file.read_text()
        data = r.get_json()["data"]
        assert data["active"] is True
        assert data["source"] == "file"

    def test_post_active_false_removes_file(self, client, kill_file):
        kill_file.write_text("pre-existing\n")
        r = client.post(
            "/api/v1/auto-approval/kill-switch",
            json={"active": False},
            headers=_csrf(client),
        )
        assert r.status_code == 200
        assert not kill_file.exists()
        data = r.get_json()["data"]
        assert data["active"] is False

    def test_post_active_false_idempotent_when_already_off(self, client, kill_file):
        """No file exists → POST active=false should not error."""
        r = client.post(
            "/api/v1/auto-approval/kill-switch",
            json={"active": False},
            headers=_csrf(client),
        )
        assert r.status_code == 200
        assert r.get_json()["data"]["active"] is False

    def test_post_creates_parent_dirs(self, client, tmp_path, monkeypatch):
        """The .runtime dir doesn't exist yet on a fresh deploy."""
        deep_path = tmp_path / "nested" / "subdir" / "kill"
        monkeypatch.setenv("GENLAB_AUTO_APPROVE_KILL_FILE", str(deep_path))
        monkeypatch.delenv("GENLAB_AUTO_APPROVE_DISABLED", raising=False)
        r = client.post(
            "/api/v1/auto-approval/kill-switch",
            json={"active": True},
            headers=_csrf(client),
        )
        assert r.status_code == 200
        assert deep_path.exists()

    def test_post_rejects_non_bool_active(self, client, kill_file):
        r = client.post(
            "/api/v1/auto-approval/kill-switch",
            json={"active": "true"},
            headers=_csrf(client),
        )
        assert r.status_code == 400

    def test_post_rejects_missing_active(self, client, kill_file):
        r = client.post(
            "/api/v1/auto-approval/kill-switch",
            json={},
            headers=_csrf(client),
        )
        assert r.status_code == 400

    def test_env_var_set_cannot_be_cleared_by_post(self, client, kill_file, monkeypatch):
        """Posting active=false leaves env var alone — dashboard can't clear shell-set env."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVE_DISABLED", "1")
        r = client.post(
            "/api/v1/auto-approval/kill-switch",
            json={"active": False},
            headers=_csrf(client),
        )
        data = r.get_json()["data"]
        # Even though file was removed, env var keeps it active
        assert data["active"] is True
        assert data["source"] == "env"
        assert data["env_var_set"] is True


class TestWorkerDashboardLockstep:
    """Critical contract: dashboard's _read_kill_switch_state and
    worker's _kill_switch_active MUST agree on every code path.
    This pins the contract — any future divergence fails here.
    """

    def test_worker_sees_what_dashboard_writes(self, kill_file, monkeypatch):
        from server.api.auto_approval import _read_kill_switch_state
        from genlab_core.scheduling.auto_approver import _kill_switch_active

        # Initially both report off
        assert _read_kill_switch_state()["active"] is False
        assert _kill_switch_active() == (False, "none")

        # Dashboard creates the file
        kill_file.write_text("test\n")
        assert _read_kill_switch_state()["active"] is True
        assert _kill_switch_active() == (True, "file")

        # Worker should still see file-only when env is empty
        kill_file.unlink()
        monkeypatch.setenv("GENLAB_AUTO_APPROVE_DISABLED", "1")
        assert _read_kill_switch_state()["source"] == "env"
        assert _kill_switch_active() == (True, "env")

        # Both set
        kill_file.write_text("test\n")
        assert _read_kill_switch_state()["source"] == "both"
        assert _kill_switch_active() == (True, "both")
