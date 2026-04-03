"""Tests for scheduler API endpoints."""
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
    """Disable auth middleware so tests don't need credentials."""
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["DRY_RUN"] = True
    app.config["LOCAL_MODE"] = True
    with app.test_client() as c:
        yield c


def _csrf(client):
    """Return headers dict with a valid CSRF token for POST requests."""
    return {
        "X-CSRF-Token": client.get("/api/csrf-token").get_json()["csrf_token"],
        "Content-Type": "application/json",
    }


class TestSchedulerStatus:
    def test_status_returns_jobs(self, client):
        resp = client.get("/api/v1/scheduler/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        jobs = body["data"]["jobs"]
        assert isinstance(jobs, list)
        job_ids = [j["id"] for j in jobs]
        assert "publish_tick" in job_ids
        assert "token_health" in job_ids
        assert "analytics" in job_ids
        assert "engagement_poll" in job_ids

    def test_status_job_has_required_keys(self, client):
        resp = client.get("/api/v1/scheduler/status")
        assert resp.status_code == 200
        jobs = resp.get_json()["data"]["jobs"]
        for job in jobs:
            assert "id" in job
            assert "state" in job
            assert "next_run_time" in job


class TestSchedulerPauseResume:
    def test_pause_job(self, client):
        resp = client.post(
            "/api/v1/scheduler/jobs/token_health/pause",
            headers=_csrf(client),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert "token_health" in body["message"]

    def test_resume_job(self, client):
        # Pause first, then resume
        client.post(
            "/api/v1/scheduler/jobs/analytics/pause",
            headers=_csrf(client),
        )
        resp = client.post(
            "/api/v1/scheduler/jobs/analytics/resume",
            headers=_csrf(client),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert "analytics" in body["message"]

    def test_pause_unknown_job_returns_400(self, client):
        resp = client.post(
            "/api/v1/scheduler/jobs/nonexistent_job_xyz/pause",
            headers=_csrf(client),
        )
        assert resp.status_code == 400

    def test_resume_unknown_job_returns_400(self, client):
        resp = client.post(
            "/api/v1/scheduler/jobs/nonexistent_job_xyz/resume",
            headers=_csrf(client),
        )
        assert resp.status_code == 400


class TestSchedulerTrigger:
    def test_trigger_known_job(self, client):
        resp = client.post(
            "/api/v1/scheduler/trigger/quota_monitor",
            headers=_csrf(client),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert "quota_monitor" in body["message"]

    def test_trigger_unknown_job_returns_404(self, client):
        resp = client.post(
            "/api/v1/scheduler/trigger/nonexistent_job_xyz",
            headers=_csrf(client),
        )
        assert resp.status_code == 404
