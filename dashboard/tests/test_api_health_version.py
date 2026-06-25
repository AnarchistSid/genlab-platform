"""Regression test for the post-2026-06-25-audit fix to /api/health.

Pre-fix: the health endpoint hardcoded `"version": "2.0.0"`. This
appeared on the dashboard Settings → System "Dashboard Version" card
regardless of what was actually deployed, making it impossible to tell
"fresh deploy" from "stale deploy."

Post-fix: the endpoint reads GENLAB_GIT_COMMIT and GENLAB_BUILD_TIME
from env (set by scripts/deploy.sh or CI), falling back to "unknown"
/ "" for local dev / pre-injection deployments.

Pins:
  * Endpoint returns env-provided version, not hardcoded "2.0.0"
  * Unset env → "unknown" (not crash, not empty string)
  * build_time field present in response (even if empty)
  * No regression of unauthenticated minimal response shape
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_unauthed_health_returns_env_version_not_hardcoded(client, monkeypatch):
    """REGRESSION: previously returned hardcoded '2.0.0'. Now reads
    GENLAB_GIT_COMMIT — if set, that's the value; if not, 'unknown'."""
    monkeypatch.setenv("GENLAB_GIT_COMMIT", "abc1234")
    monkeypatch.setenv("GENLAB_BUILD_TIME", "2026-06-26T12:00:00Z")
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["version"] == "abc1234"
    assert body["build_time"] == "2026-06-26T12:00:00Z"


def test_unauthed_health_unknown_when_env_unset(client, monkeypatch):
    """Defensive: env vars unset → 'unknown' (not crash, not '2.0.0',
    not empty). Operator sees 'unknown' and knows the deploy didn't
    inject version — actionable signal."""
    monkeypatch.delenv("GENLAB_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GENLAB_BUILD_TIME", raising=False)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["version"] == "unknown"
    assert body["build_time"] == ""


def test_unauthed_health_response_shape_preserved(client, monkeypatch):
    """Backward-compat: response keys preserve pre-fix shape so existing
    frontend consumers don't break."""
    monkeypatch.delenv("GENLAB_GIT_COMMIT", raising=False)
    resp = client.get("/api/health")
    body = resp.get_json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "environment" in body
    # New field added by fix — frontend tolerates absence per
    # settings.tsx conditional, but field SHOULD be present
    assert "build_time" in body


def test_app_version_helper_reads_env(monkeypatch):
    """Direct unit test of the helper function."""
    monkeypatch.setenv("GENLAB_GIT_COMMIT", "deadbeef")
    assert review_server_module._app_version() == "deadbeef"


def test_app_version_helper_returns_unknown_when_unset(monkeypatch):
    monkeypatch.delenv("GENLAB_GIT_COMMIT", raising=False)
    assert review_server_module._app_version() == "unknown"


def test_build_time_helper_reads_env(monkeypatch):
    monkeypatch.setenv("GENLAB_BUILD_TIME", "2026-06-26T00:00:00Z")
    assert review_server_module._build_time() == "2026-06-26T00:00:00Z"


def test_build_time_helper_returns_empty_when_unset(monkeypatch):
    monkeypatch.delenv("GENLAB_BUILD_TIME", raising=False)
    assert review_server_module._build_time() == ""
