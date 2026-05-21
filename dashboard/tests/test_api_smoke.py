"""Smoke tests for all dashboard API routes that lack dedicated test files.

Covers: analytics, overview, niches, stories, config_routes, schedule,
platform_posts, youtube_quota, legal.

Each test mocks external dependencies (BacklogClient, YouTubeQuotaTracker)
and verifies the route returns a successful HTTP status code.
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
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── Helpers ─────────────────────────────────────────────────


def _mock_backlog_client():
    """Build a BacklogClient mock with common proxy attributes."""
    mock_client = MagicMock()
    mock_proxy = MagicMock()
    mock_proxy.all.return_value = []
    mock_proxy.get.return_value = {"id": "1", "fields": {"title": "Test"}}

    mock_client.analytics = mock_proxy
    mock_client.publishing_analytics = mock_proxy
    mock_client.blueprints = mock_proxy
    mock_client.stories = mock_proxy
    mock_client.templates = mock_proxy
    mock_client.audience_snapshots = mock_proxy
    mock_client.pending_engagement = mock_proxy
    return mock_client


# ── Legal (public, no auth) ─────────────────────────────────


class TestLegalPages:
    def test_privacy_returns_html(self, client):
        resp = client.get("/legal/privacy")
        assert resp.status_code == 200
        assert b"Privacy Policy" in resp.data

    def test_deletion_returns_html(self, client):
        resp = client.get("/legal/deletion")
        assert resp.status_code == 200
        assert b"Data Deletion" in resp.data


# ── Niches ───────────────────────────────────────────────────


class TestNichesAPI:
    def test_list_niches_returns_200(self, client):
        resp = client.get("/api/v1/niches")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        # Should return at least the 5 known niches
        assert len(data["data"]) >= 5

    def test_niches_contain_required_fields(self, client):
        resp = client.get("/api/v1/niches")
        niches = resp.get_json()["data"]
        for niche in niches:
            assert "id" in niche
            assert "display_name" in niche
            assert "accent_hex" in niche


# ── Config Routes ────────────────────────────────────────────


class TestConfigAPI:
    def test_source_filters_returns_200(self, client):
        resp = client.get("/api/v1/config/source-filters")
        assert resp.status_code == 200

    def test_platforms_returns_200(self, client):
        resp = client.get("/api/v1/config/platforms")
        assert resp.status_code == 200

    def test_sources_without_niche_returns_200_or_404(self, client):
        resp = client.get("/api/v1/config/sources")
        assert resp.status_code in (200, 404)

    def test_schedule_slots_returns_200_or_404(self, client):
        resp = client.get("/api/v1/config/schedule-slots")
        assert resp.status_code in (200, 404)

    def test_scoring_returns_200_or_404(self, client):
        resp = client.get("/api/v1/config/scoring")
        assert resp.status_code in (200, 404)

    def test_templates_with_mock(self, client):
        mock_client = _mock_backlog_client()
        with (
            patch("server.api.config_routes._load_yaml", return_value=None),
            patch("server.core.graph_sync.get_sync_client", return_value=mock_client),
        ):
            resp = client.get("/api/v1/config/templates")
        assert resp.status_code == 200

    def test_get_notifications_returns_200(self, client):
        resp = client.get("/api/v1/settings/notifications")
        assert resp.status_code == 200
        data = resp.get_json()["data"]["data"]
        assert "enabled_types" in data

    def _get_csrf(self, client):
        """Fetch CSRF token from the dedicated endpoint."""
        resp = client.get("/api/csrf-token")
        return resp.get_json().get("csrf_token", "")

    def test_save_notifications_accepts_valid_prefs(self, client, tmp_path):
        import server.api.config_routes as cfg_mod

        csrf = self._get_csrf(client)
        with patch.object(cfg_mod, "_NOTIF_PREFS_FILE", tmp_path / "prefs.json"):
            resp = client.post(
                "/api/v1/settings/notifications",
                json={"email_digest": "daily", "_csrf_token": csrf},
                content_type="application/json",
            )
        assert resp.status_code == 200

    def test_save_notifications_rejects_bad_webhook(self, client, tmp_path):
        import server.api.config_routes as cfg_mod

        csrf = self._get_csrf(client)
        with patch.object(cfg_mod, "_NOTIF_PREFS_FILE", tmp_path / "prefs.json"):
            resp = client.post(
                "/api/v1/settings/notifications",
                json={"slack_webhook_url": "http://evil.com/hook", "_csrf_token": csrf},
                content_type="application/json",
            )
        assert resp.status_code == 400


# ── Stories ──────────────────────────────────────────────────


class TestStoriesAPI:
    @patch("server.api.stories._get_client")
    def test_list_stories_returns_200(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        resp = client.get("/api/v1/stories")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "meta" in data

    @patch("server.api.stories._get_client")
    def test_list_stories_pagination(self, mock_get, client):
        mock_client = _mock_backlog_client()
        mock_client.stories.all.return_value = [
            {"id": str(i), "fields": {"title": f"Story {i}"}} for i in range(10)
        ]
        mock_get.return_value = mock_client
        resp = client.get("/api/v1/stories?page=1&per_page=3")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["meta"]["per_page"] == 3
        assert len(data["data"]) == 3

    @patch("server.api.stories._get_client")
    def test_get_story_returns_200(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        resp = client.get("/api/v1/stories/123")
        assert resp.status_code == 200

    def test_get_story_rejects_invalid_id(self, client):
        # "abc" passes RECORD_RE (valid word chars) but doesn't exist → 404
        resp = client.get("/api/v1/stories/abc")
        assert resp.status_code == 404

    @patch("server.api.stories._get_client")
    def test_list_stories_invalid_page_returns_400(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        resp = client.get("/api/v1/stories?page=abc")
        assert resp.status_code == 400


# ── Overview ─────────────────────────────────────────────────


class TestOverviewAPI:
    @patch("server.api.overview._get_client")
    @patch("server.api.overview.RUNS_DIR", Path("/tmp/nonexistent_runs"))
    def test_cross_niche_overview_returns_200(self, mock_get, client):
        import server.api.overview as ov_mod

        # Clear cache
        ov_mod._overview_cache["data"] = None
        ov_mod._overview_cache["ts"] = 0.0

        mock_client = _mock_backlog_client()
        mock_get.return_value = mock_client

        resp = client.get("/api/v1/cross-niche/overview")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "global" in data
        assert "niches" in data
        assert "schedule_today" in data

    @patch("server.api.overview._get_client")
    @patch("server.api.overview.RUNS_DIR", Path("/tmp/nonexistent_runs"))
    def test_overview_uses_cache(self, mock_get, client):
        import server.api.overview as ov_mod

        ov_mod._overview_cache["data"] = None
        ov_mod._overview_cache["ts"] = 0.0

        mock_client = _mock_backlog_client()
        mock_get.return_value = mock_client

        resp1 = client.get("/api/v1/cross-niche/overview")
        resp2 = client.get("/api/v1/cross-niche/overview")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Second call should hit cache, so _get_client called only once
        assert mock_get.call_count == 1

    @patch("server.api.overview._get_client")
    @patch("server.api.overview.RUNS_DIR", Path("/tmp/nonexistent_runs"))
    def test_overview_handles_client_failure(self, mock_get, client):
        import server.api.overview as ov_mod

        ov_mod._overview_cache["data"] = None
        ov_mod._overview_cache["ts"] = 0.0

        mock_get.side_effect = Exception("connection refused")
        resp = client.get("/api/v1/cross-niche/overview")
        # Should still return 200 (overview degrades gracefully with empty data)
        # or 502 if _build_overview raises
        assert resp.status_code in (200, 502)


# ── Schedule ─────────────────────────────────────────────────


class TestScheduleAPI:
    def _get_csrf(self, client):
        return client.get("/api/csrf-token").get_json().get("csrf_token", "")

    @patch("server.api.schedule._get_client")
    def test_get_schedule_returns_200(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        resp = client.get("/api/v1/schedule?from=2026-03-13&to=2026-03-15")
        assert resp.status_code == 200
        data = resp.get_json()["data"]["data"]
        assert len(data) == 3  # 3 days

    @patch("server.api.schedule._get_client")
    def test_schedule_coverage_returns_200(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        resp = client.get("/api/v1/schedule/coverage?from=2026-03-13&to=2026-03-14")
        assert resp.status_code == 200

    def test_schedule_rejects_invalid_date(self, client):
        resp = client.get("/api/v1/schedule?from=not-a-date")
        assert resp.status_code == 400

    @patch("server.api.schedule._get_client")
    def test_reorder_rejects_missing_fields(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        csrf = self._get_csrf(client)
        resp = client.patch(
            "/api/v1/schedule/reorder",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("server.api.schedule._get_client")
    def test_reorder_rejects_invalid_blueprint_id(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        csrf = self._get_csrf(client)
        # Use chars that fail RECORD_RE (special chars, not word chars)
        resp = client.patch(
            "/api/v1/schedule/reorder",
            json={"blueprint_id": "abc!@#", "to_slot": "12:00", "_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("server.api.schedule._get_client")
    def test_reorder_rejects_invalid_slot_format(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        csrf = self._get_csrf(client)
        resp = client.patch(
            "/api/v1/schedule/reorder",
            json={"blueprint_id": "123", "to_slot": "25:99", "_csrf_token": csrf},
            content_type="application/json",
        )
        # 25:99 matches HH:MM regex but may not be in valid_slots
        assert resp.status_code == 400


# ── Platform Posts ───────────────────────────────────────────


class TestPlatformPostsAPI:
    @patch("server.api.platform_posts._get_client")
    def test_tiktok_posts_returns_200(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        resp = client.get("/api/v1/platforms/tiktok/posts")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "meta" in data
        assert "audit_approved" in data["meta"]

    @patch("server.api.platform_posts._get_client")
    def test_threads_posts_returns_200(self, mock_get, client):
        mock_get.return_value = _mock_backlog_client()
        resp = client.get("/api/v1/platforms/threads/posts")
        assert resp.status_code == 200

    @patch("server.api.platform_posts._get_client")
    def test_tiktok_graceful_on_failure(self, mock_get, client):
        mock_client = _mock_backlog_client()
        mock_client.publishing_analytics.all.side_effect = Exception("timeout")
        mock_get.return_value = mock_client
        resp = client.get("/api/v1/platforms/tiktok/posts")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["data"] == []


# ── YouTube Quota ────────────────────────────────────────────


class TestYouTubeQuotaAPI:
    @patch("genlab_core.monitoring.youtube_quota.YouTubeQuotaTracker")
    def test_youtube_quota_returns_200(self, MockTracker, client):
        instance = MockTracker.return_value
        instance.status.return_value = {
            "used": 3200,
            "remaining": 6800,
            "pct_used": 0.32,
            "upload_count": 2,
            "reset_date": "2026-03-14",
        }
        resp = client.get("/api/v1/monitoring/youtube-quota")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["used"] == 3200

    @patch("genlab_core.monitoring.youtube_quota.YouTubeQuotaTracker")
    def test_youtube_quota_returns_500_on_error(self, MockTracker, client):
        MockTracker.side_effect = RuntimeError("tracker broken")
        resp = client.get("/api/v1/monitoring/youtube-quota")
        assert resp.status_code == 500


# ── Analytics (smoke — just verify routes don't 500) ─────────


class TestAnalyticsAPI:
    """Smoke tests for the 14 analytics endpoints.

    Each test mocks the BacklogClient so no real SharePoint calls are made.
    We only verify: (a) route resolves, (b) returns 200, (c) JSON is valid.
    """

    @pytest.fixture(autouse=True)
    def _mock_analytics_client(self):
        mock_client = _mock_backlog_client()
        with (
            patch("server.api.analytics._get_client", return_value=mock_client),
            patch("server.api.analytics._cached_analytics_all", return_value=[]),
            patch("server.api.analytics._cached_pub_analytics_all", return_value=[]),
        ):
            # Clear analytics module caches
            import server.api.analytics as a_mod

            a_mod._overview_cache["data"] = None
            a_mod._overview_cache["ts"] = 0.0
            a_mod._table_cache.clear()
            yield

    def test_analytics_overview(self, client):
        resp = client.get("/api/v1/analytics/overview?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_publishing(self, client):
        resp = client.get("/api/v1/analytics/publishing?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_content(self, client):
        resp = client.get("/api/v1/analytics/content?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_pipeline(self, client):
        resp = client.get("/api/v1/analytics/pipeline")
        assert resp.status_code == 200

    def test_analytics_funnel(self, client):
        resp = client.get("/api/v1/analytics/funnel?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_engagement(self, client):
        resp = client.get("/api/v1/analytics/engagement?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_engagement_summary(self, client):
        resp = client.get("/api/v1/analytics/engagement/summary?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_heatmap(self, client):
        resp = client.get("/api/v1/analytics/heatmap?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_content_performance(self, client):
        resp = client.get("/api/v1/analytics/content-performance?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_trends(self, client):
        resp = client.get("/api/v1/analytics/trends?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_audience(self, client):
        resp = client.get("/api/v1/analytics/audience?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_virality_breakdown(self, client):
        resp = client.get("/api/v1/analytics/virality-breakdown?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_monetization(self, client):
        resp = client.get("/api/v1/analytics/monetization?niche_id=gaming")
        assert resp.status_code == 200

    def test_analytics_demographics(self, client):
        resp = client.get("/api/v1/analytics/demographics?niche_id=gaming")
        assert resp.status_code == 200
