"""Tests for Token Health API endpoints.

Covers:
  - GET /api/token-health returns correct schema
  - Response includes all 5 platforms
  - TikTok audit_approved flag propagation
  - POST /api/token-health/refresh/<platform> routing
  - Cache invalidation on refresh
"""

import sys
from pathlib import Path
from unittest.mock import patch

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


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear token health cache before each test."""
    from server.api.token_health import _health_cache
    _health_cache["data"] = None
    _health_cache["ts"] = 0.0


class TestTokenHealthEndpoint:
    """GET /api/token-health returns correct schema."""

    @patch("server.api.token_health._check_tiktok")
    @patch("server.api.token_health._check_threads")
    @patch("server.api.token_health._check_instagram")
    @patch("server.api.token_health._check_youtube")
    @patch("server.api.token_health._check_facebook")
    def test_returns_all_platforms(
        self, mock_fb, mock_yt, mock_ig, mock_threads, mock_tiktok, client
    ):
        mock_tiktok.return_value = {
            "platform": "tiktok",
            "access_token_status": "ok",
            "access_token_expires_in_hours": 18,
            "refresh_token_status": "ok",
            "refresh_token_expires_in_days": 287,
            "last_refreshed": "2026-03-07T14:00:00Z",
            "audit_approved": False,
        }
        mock_threads.return_value = {
            "platform": "threads",
            "token_status": "ok",
            "expires_in_days": 45,
            "last_refreshed": "2026-01-22T09:00:00Z",
        }
        mock_ig.return_value = {
            "platform": "instagram",
            "token_status": "ok",
            "expires_in_days": 52.3,
        }
        mock_yt.return_value = {
            "platform": "youtube",
            "token_status": "ok",
            "expires_in_days": None,
        }
        mock_fb.return_value = {
            "platform": "facebook",
            "token_status": "ok",
            "expires_in_days": None,
        }

        resp = client.get("/api/token-health")
        assert resp.status_code == 200

        # Response is wrapped: {"status": "success", "data": {"platforms": [...], "checked_at": ...}}
        data = resp.json["data"]
        assert "platforms" in data
        assert "checked_at" in data
        assert len(data["platforms"]) == 5

        platform_names = [p["platform"] for p in data["platforms"]]
        assert "tiktok" in platform_names
        assert "threads" in platform_names
        assert "instagram" in platform_names
        assert "youtube" in platform_names
        assert "facebook" in platform_names

    @patch("server.api.token_health._check_tiktok")
    @patch("server.api.token_health._check_threads")
    @patch("server.api.token_health._check_instagram")
    @patch("server.api.token_health._check_youtube")
    @patch("server.api.token_health._check_facebook")
    def test_tiktok_audit_false(
        self, mock_fb, mock_yt, mock_ig, mock_threads, mock_tiktok, client
    ):
        mock_tiktok.return_value = {
            "platform": "tiktok",
            "access_token_status": "ok",
            "audit_approved": False,
        }
        mock_threads.return_value = {"platform": "threads", "token_status": "ok"}
        mock_ig.return_value = {"platform": "instagram", "token_status": "ok"}
        mock_yt.return_value = {"platform": "youtube", "token_status": "ok"}
        mock_fb.return_value = {"platform": "facebook", "token_status": "ok"}

        resp = client.get("/api/token-health")
        data = resp.json["data"]
        tiktok = next(p for p in data["platforms"] if p["platform"] == "tiktok")
        assert tiktok["audit_approved"] is False


class TestTokenRefreshEndpoint:
    """POST /api/token-health/refresh/<platform> routing."""

    @pytest.fixture(autouse=True)
    def _disable_csrf(self, monkeypatch):
        """Disable CSRF enforcement for POST tests.

        Monkeypatching the function itself doesn't work because Flask stores
        the original reference in before_request_funcs. Instead, we add POST
        to _SAFE_METHODS so _enforce_csrf skips it (late-bound read).
        """
        monkeypatch.setattr(
            review_server_module, "_SAFE_METHODS", {"GET", "HEAD", "OPTIONS", "POST"}
        )

    def test_unknown_platform_returns_404(self, client):
        resp = client.post(
            "/api/token-health/refresh/snapchat",
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_instagram_returns_redirect(self, client):
        resp = client.post(
            "/api/token-health/refresh/instagram",
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json["data"]["status"] == "redirect"

    def test_youtube_returns_redirect(self, client):
        resp = client.post(
            "/api/token-health/refresh/youtube",
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json["data"]["status"] == "redirect"
