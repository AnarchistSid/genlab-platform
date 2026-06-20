"""Tests for runway monitor API endpoint."""

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
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestRunwayEndpoint:
    @patch("server.api.runway._get_client")
    def test_runway_returns_200_with_all_niches(self, mock_get, client):
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            {"id": str(i), "fields": {"action_taken": "approved"}} for i in range(5)
        ]
        mock_get.return_value = mock_client

        resp = client.get("/api/v1/runway")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "ai_creators" in data
        assert "gaming" in data
        assert "sports" in data
        assert "movies" in data
        assert "anime" in data

    @patch("server.api.runway._get_client")
    def test_runway_critical_when_low_approved(self, mock_get, client):
        # PR #396: the runway endpoint now groups VISUAL_READY records
        # by ``niche_id`` in Python (one fetch for ALL niches instead
        # of per-niche). Tests must therefore set ``niche_id`` on each
        # record — pre-PR the mock returned the same records to every
        # per-niche call regardless of arg, so the field was ignorable.
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            {"id": "1", "fields": {"action_taken": "approved", "niche_id": "ai_creators"}},
        ]
        mock_get.return_value = mock_client

        resp = client.get("/api/v1/runway")
        data = resp.get_json()["data"]
        # 1 approved item = 1 day runway = critical
        assert data["ai_creators"]["status"] == "critical"
        assert data["ai_creators"]["runway_days"] == 1

    @patch("server.api.runway._get_client")
    def test_runway_ok_when_enough_approved(self, mock_get, client):
        # See note in test_runway_critical_when_low_approved about
        # the PR #396 niche_id grouping change.
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            {
                "id": str(i),
                "fields": {"action_taken": "approved", "niche_id": "ai_creators"},
            }
            for i in range(10)
        ]
        mock_get.return_value = mock_client

        resp = client.get("/api/v1/runway")
        data = resp.get_json()["data"]
        assert data["ai_creators"]["status"] == "ok"
        assert data["ai_creators"]["runway_days"] == 10

    @patch("server.api.runway._get_client")
    def test_runway_handles_backlog_failure(self, mock_get, client):
        mock_get.side_effect = Exception("connection refused")
        resp = client.get("/api/v1/runway")
        assert resp.status_code == 502
