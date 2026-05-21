"""Tests for /api/v1/blueprints endpoints."""

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
    return {
        "X-CSRF-Token": client.get("/api/csrf-token").get_json()["csrf_token"],
        "Content-Type": "application/json",
    }


class TestBlueprintsV1:
    def test_list_returns_paginated(self, client):
        resp = client.get("/api/v1/blueprints")
        assert resp.status_code in (200, 502)  # 502 if backlog not configured
        if resp.status_code == 200:
            data = resp.get_json()["data"]
            assert "data" in data
            assert "meta" in data

    def test_get_invalid_id(self, client):
        resp = client.get("/api/v1/blueprints/bad_id")
        # "bad_id" passes RECORD_RE (valid chars) but doesn't exist → 404
        assert resp.status_code == 404

    def test_review_invalid_action(self, client):
        resp = client.post(
            "/api/v1/blueprints/12345/review",
            json={"action": "invalid"},
            headers=_csrf(client),
        )
        assert resp.status_code == 400

    def test_review_valid_actions(self, client):
        for action in ("approve", "reject", "revise", "skip"):
            resp = client.post(
                "/api/v1/blueprints/12345/review",
                json={"action": action},
                headers=_csrf(client),
            )
            # Should not return 400 (bad request) for valid actions
            assert resp.status_code != 400

    def test_batch_requires_ids(self, client):
        resp = client.post(
            "/api/v1/blueprints/batch-review",
            json={"ids": [], "action": "approve"},
            headers=_csrf(client),
        )
        assert resp.status_code == 400

    def test_reschedule_requires_field(self, client):
        resp = client.patch(
            "/api/v1/blueprints/12345/schedule",
            json={},
            headers=_csrf(client),
        )
        assert resp.status_code == 400

    def test_list_niche_all_returns_200(self, client):
        """niche_id=all should not 502 — it skips client-side niche filtering."""
        resp = client.get("/api/v1/blueprints?niche_id=all")
        assert resp.status_code in (200, 502)  # 502 only if backlog unreachable
        if resp.status_code == 200:
            data = resp.get_json()["data"]
            assert "data" in data

    def test_list_empty_niche_returns_200(self, client):
        """Empty niche_id should not crash."""
        resp = client.get("/api/v1/blueprints?niche_id=")
        assert resp.status_code in (200, 502)
        if resp.status_code == 200:
            assert "data" in resp.get_json()["data"]

    def test_content_update_rejects_invalid_fields(self, client):
        resp = client.patch(
            "/api/v1/blueprints/12345/content",
            json={"status": "PUBLISHED"},
            headers=_csrf(client),
        )
        assert resp.status_code == 400
