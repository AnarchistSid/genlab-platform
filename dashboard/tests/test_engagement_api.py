"""Tests for engagement status + YouTube quota dashboard endpoints."""

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


class TestEngagementStatus:
    @patch("server.api.engagement._get_client")
    def test_returns_200_with_niche_breakdown(self, mock_get, client):
        mock_client = MagicMock()
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = [
            {"id": "1", "fields": {"status": "replied", "niche_id": "ai_creators"}},
            {"id": "2", "fields": {"status": "pending", "niche_id": "ai_creators"}},
            {"id": "3", "fields": {"status": "failed", "niche_id": "ai_creators"}},
        ]
        mock_client.pending_engagement = mock_proxy
        mock_get.return_value = mock_client

        resp = client.get("/api/v1/engagement/status")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "by_niche" in data
        assert "totals" in data
        assert data["by_niche"]["ai_creators"]["replied"] == 1
        assert data["by_niche"]["ai_creators"]["failed"] == 1

    @patch("server.api.engagement._get_client")
    def test_handles_missing_proxy(self, mock_get, client):
        mock_client = MagicMock(spec=[])
        mock_client.pending_engagement = None
        mock_get.return_value = mock_client

        resp = client.get("/api/v1/engagement/status")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        # All zeros when proxy is None
        assert data["totals"]["pending"] == 0

    @patch("server.api.engagement._get_client")
    def test_handles_backlog_failure(self, mock_get, client):
        mock_get.side_effect = Exception("connection refused")
        resp = client.get("/api/v1/engagement/status")
        assert resp.status_code == 502

    @patch("server.api.engagement._get_client")
    def test_totals_sum_across_niches(self, mock_get, client):
        mock_client = MagicMock()
        mock_proxy = MagicMock()
        # Return 2 replied for each niche
        mock_proxy.all.return_value = [
            {"id": "1", "fields": {"status": "replied"}},
            {"id": "2", "fields": {"status": "replied"}},
        ]
        mock_client.pending_engagement = mock_proxy
        mock_get.return_value = mock_client

        resp = client.get("/api/v1/engagement/status")
        data = resp.get_json()["data"]
        # 5 niches × 2 replied = 10 total
        assert data["totals"]["replied"] == 10


class TestEngagementQueueStub:
    """E2: Stub endpoint for /api/v1/engagement/queue."""

    def test_returns_200_with_empty_data(self, client):
        resp = client.get("/api/v1/engagement/queue")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["pending"] == []
        assert data["total"] == 0


class TestPublishingScheduleStub:
    """E2: Stub endpoint for /api/v1/publishing/schedule."""

    def test_returns_200_with_redirect(self, client):
        resp = client.get("/api/v1/publishing/schedule")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["redirect"] == "/api/v1/schedule"
        assert data["status"] == "use_schedule_endpoint"


class TestYouTubeQuota:
    @patch("genlab_core.monitoring.youtube_quota.YouTubeQuotaTracker")
    def test_returns_200_with_quota_status(self, MockTracker, client):
        instance = MockTracker.return_value
        instance.status.return_value = {
            "used": 3200,
            "remaining": 6800,
            "pct_used": 0.32,
            "upload_count": 2,
            "reset_date": "2026-03-11",
        }

        resp = client.get("/api/v1/monitoring/youtube-quota")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["used"] == 3200
        assert data["upload_count"] == 2

    @patch("genlab_core.monitoring.youtube_quota.YouTubeQuotaTracker")
    def test_returns_500_on_error(self, MockTracker, client):
        MockTracker.side_effect = RuntimeError("tracker broken")
        resp = client.get("/api/v1/monitoring/youtube-quota")
        assert resp.status_code == 500
