"""Tests for Focus Review endpoint — review-queue + health check.

Covers:
  - Empty queue returns valid response (not 500)
  - Fallback to recently published when no pending items
  - Health check returns correct schema
  - Niche filtering ("all" vs specific niche_id)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """Disable auth middleware so tests don't need credentials."""
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _mock_blueprint(record_id, title, status="VISUAL_READY", niche_id="gaming", action_taken=""):
    return {
        "id": record_id,
        "fields": {
            "hook": title,
            "status": status,
            "niche_id": niche_id,
            "priority_score": 0.8,
            "action_taken": action_taken,
        },
    }


class TestReviewQueueEmpty:
    """When backlog returns [], endpoint should return valid JSON (not 500)."""

    @patch("server.api.blueprints._get_client")
    def test_returns_empty_list(self, mock_get_client, client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.blueprints.all.return_value = []
        mock_client.get_blueprints_by_status.return_value = []

        resp = client.get("/api/v1/blueprints/review-queue")
        assert resp.status_code == 200

        data = resp.json["data"]
        assert data["data"] == []
        assert data["meta"]["total"] == 0
        assert data["meta"]["fallback"] is False

    @patch("server.api.blueprints._get_client")
    def test_returns_fallback_when_no_pending(self, mock_get_client, client):
        """When no pending items, returns last 5 published as fallback."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # No VISUAL_READY items
        mock_client.blueprints.all.return_value = []

        # But there are published items
        mock_client.get_blueprints_by_status.return_value = [
            _mock_blueprint(f"rec_{i}", f"Published Story {i}", status="PUBLISHED")
            for i in range(8)
        ]

        resp = client.get("/api/v1/blueprints/review-queue")
        assert resp.status_code == 200

        data = resp.json["data"]
        assert data["meta"]["fallback"] is True
        assert data["meta"]["total"] == 5  # capped at 5
        assert len(data["data"]) == 5


class TestReviewQueueNicheFilter:
    """Niche filtering: "all" shows everything, specific niche filters."""

    @patch("server.api.blueprints._get_client")
    def test_all_niches_shows_everything(self, mock_get_client, client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.blueprints.all.return_value = [
            _mock_blueprint("1", "AI News Story", niche_id="ai_creators"),
            _mock_blueprint("2", "Gaming Story", niche_id="gaming"),
        ]

        resp = client.get("/api/v1/blueprints/review-queue?niche_id=all")
        assert resp.status_code == 200

        data = resp.json["data"]
        assert data["meta"]["total"] == 2
        assert data["meta"]["niche_id"] == "all"

    @patch("server.api.blueprints._get_client")
    def test_gaming_niche_filters(self, mock_get_client, client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.blueprints.all.return_value = [
            _mock_blueprint("1", "AI News Story", niche_id="ai_creators"),
            _mock_blueprint("2", "Gaming Story", niche_id="gaming"),
        ]

        resp = client.get("/api/v1/blueprints/review-queue?niche_id=gaming")
        assert resp.status_code == 200

        data = resp.json["data"]
        assert data["meta"]["total"] == 1
        assert data["meta"]["niche_id"] == "gaming"

    @patch("server.api.blueprints._get_client")
    def test_default_niche_is_all(self, mock_get_client, client):
        """When no niche_id parameter, default should be 'all' (not 'ai_creators')."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.blueprints.all.return_value = [
            _mock_blueprint("1", "AI Story", niche_id="ai_creators"),
            _mock_blueprint("2", "Gaming Story", niche_id="gaming"),
        ]

        resp = client.get("/api/v1/blueprints/review-queue")
        assert resp.status_code == 200

        data = resp.json["data"]
        # Default is "all" — should return both
        assert data["meta"]["niche_id"] == "all"
        assert data["meta"]["total"] == 2


class TestHealthEndpoint:
    """GET /api/health/focus-review returns correct schema."""

    @patch("server.api.blueprints._get_client")
    def test_health_ok(self, mock_get_client, client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.blueprints.all.return_value = [
            _mock_blueprint("1", "Pending Item"),
            _mock_blueprint("2", "Another Item"),
        ]

        # Mock the underlying proxy for list_id
        inner_proxy = MagicMock()
        inner_proxy._list_id = "list-blueprints-abc123"
        mock_client.blueprints._proxy = inner_proxy

        resp = client.get("/api/health/focus-review")
        assert resp.status_code == 200

        data = resp.json["data"]
        assert data["status"] == "ok"
        assert data["pending_review_count"] == 2
        assert "last_checked" in data
        assert data["list_id"] == "list-blueprints-abc123"

    @patch("server.api.blueprints._get_client")
    def test_health_empty(self, mock_get_client, client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.blueprints.all.return_value = []

        inner_proxy = MagicMock()
        inner_proxy._list_id = "list-bp"
        mock_client.blueprints._proxy = inner_proxy

        resp = client.get("/api/health/focus-review")
        assert resp.status_code == 200

        data = resp.json["data"]
        assert data["status"] == "empty"
        assert data["pending_review_count"] == 0

    @patch("server.api.blueprints._get_client")
    def test_health_error(self, mock_get_client, client):
        mock_get_client.side_effect = Exception("Connection refused")

        resp = client.get("/api/health/focus-review")
        assert resp.status_code == 502

        data = resp.json
        # Error response: {"status": "error", "error": "...", "code": 502, ...}
        assert data["status"] == "error"
        assert "error" in data
