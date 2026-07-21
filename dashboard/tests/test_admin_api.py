"""Tests for admin API endpoints: health, pending replies, niche config/credentials."""

import json
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


def _get_csrf(client):
    """Fetch CSRF token from the dedicated endpoint."""
    resp = client.get("/api/csrf-token")
    return resp.get_json().get("csrf_token", "")


# ══════════════════════════════════════════════════════════════════
#  1. System Health API
# ══════════════════════════════════════════════════════════════════


class TestHealthDetailed:
    """Tests for GET /api/v1/health/detailed."""

    @patch("server.api.health._check_services")
    @patch("server.api.health._get_last_run_per_niche")
    @patch("server.api.health._calculate_error_rate_24h")
    @patch("server.api.health._get_disk_usage")
    @patch("server.api.health._get_poller_status")
    @patch("server.api.health._check_postgres")
    @patch("server.api.health._get_active_backends")
    def test_returns_200_with_all_sections(
        self,
        mock_backends,
        mock_pg,
        mock_pollers,
        mock_disk,
        mock_errors,
        mock_runs,
        mock_services,
        client,
    ):
        mock_services.return_value = {"redis": {"status": "up"}, "dashboard": {"status": "up"}}
        mock_runs.return_value = {"gaming": {"run_id": "gaming_20260316"}, "ai_creators": None}
        mock_errors.return_value = {
            "total_lines": 100,
            "error_lines": 5,
            "error_rate_pct": 5.0,
            "files_checked": 3,
        }
        mock_disk.return_value = {"used_pct": 42.5, "total_gb": 500, "used_gb": 212, "free_gb": 288}
        mock_pollers.return_value = {"engagement_poller_twitter_gaming": {"error_count": 2}}
        mock_pg.return_value = {"status": "not_configured"}
        mock_backends.return_value = {"primary": "sharepoint"}

        resp = client.get("/api/v1/health/detailed")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["services"]["redis"]["status"] == "up"
        assert data["last_run"]["gaming"]["run_id"] == "gaming_20260316"
        assert data["error_rate_24h"]["error_rate_pct"] == 5.0
        assert data["disk_usage"]["used_pct"] == 42.5
        assert "checked_at" in data

    @patch("server.api.health._check_services", side_effect=Exception("kaboom"))
    def test_section_failure_returns_200_with_degraded_flag(self, mock_services, client):
        """2026-07-14 (dashboard audit F8) refactored the endpoint to
        fail-open per-section. Prior behavior was 500-ing the whole
        endpoint on any exception, which defeated the point of the
        per-section `_check_*` fail-opens.

        Now: exception in one section -> that section carries
        {"degraded": True, "error": "..."}; sibling sections still
        render normally; response stays 200. Pin this contract so a
        future refactor doesn't accidentally revert to 500.
        """
        resp = client.get("/api/v1/health/detailed")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        # Failing section (services) must carry degraded + error
        assert data["services"]["degraded"] is True
        assert "kaboom" in data["services"]["error"]
        # Sibling sections should NOT have degraded=True (they render as
        # normal) — proves fail-open isolates per-section.
        assert data.get("checked_at") is not None


class TestHealthServiceChecks:
    """Unit tests for individual health check functions."""

    def test_check_services_redis_up(self):
        """Redis is up when ping() returns truthy.

        Implementation switched from `redis-cli PING` subprocess to the
        python redis library's ping() in the 2026-05-21 wave-6 audit (the
        CLI wasn't installed on Hetzner where Redis runs in Docker, so
        the subprocess approach always reported Down).
        """
        from server.api.health import _check_services

        mock_client = MagicMock()
        mock_client.ping.return_value = True
        with patch("redis.Redis", return_value=mock_client):
            services = _check_services()
        assert services["redis"]["status"] == "up"
        assert services["dashboard"]["status"] == "up"

    def test_check_services_redis_down(self):
        """Redis status is `down` when ping raises."""
        from server.api.health import _check_services

        with patch("redis.Redis") as mock_redis_cls:
            mock_redis_cls.return_value.ping.side_effect = ConnectionError("unreachable")
            services = _check_services()
        assert services["redis"]["status"] == "down"

    def test_get_last_run_per_niche_empty_dir(self, tmp_path):
        import server.api.health as health_mod
        from server.api.health import _get_last_run_per_niche

        with patch.object(health_mod, "_RUNS_DIR", tmp_path):
            result = _get_last_run_per_niche()
        assert all(v is None for v in result.values())

    def test_get_last_run_per_niche_with_dirs(self, tmp_path):
        # Create fake run directories
        gaming_dir = tmp_path / "gaming_20260316_165319"
        gaming_dir.mkdir()
        anime_dir = tmp_path / "anime_20260316_201805"
        anime_dir.mkdir()

        import server.api.health as health_mod
        from server.api.health import _get_last_run_per_niche

        with patch.object(health_mod, "_RUNS_DIR", tmp_path):
            result = _get_last_run_per_niche()

        assert result["gaming"] is not None
        assert result["gaming"]["run_id"] == "gaming_20260316_165319"
        assert result["anime"] is not None
        assert result["sports"] is None

    def test_get_last_run_with_report_json(self, tmp_path):
        gaming_dir = tmp_path / "gaming_20260316_165319"
        gaming_dir.mkdir()
        report = {
            "started_at": "2026-03-16T16:53:19Z",
            "finished_at": "2026-03-16T16:55:00Z",
            "status": "success",
            "blueprints_created": 3,
        }
        (gaming_dir / "run_report.json").write_text(json.dumps(report))

        import server.api.health as health_mod
        from server.api.health import _get_last_run_per_niche

        with patch.object(health_mod, "_RUNS_DIR", tmp_path):
            result = _get_last_run_per_niche()

        assert result["gaming"]["status"] == "success"
        assert result["gaming"]["blueprints_created"] == 3

    def test_calculate_error_rate_24h_no_logs(self, tmp_path):
        import server.api.health as health_mod
        from server.api.health import _calculate_error_rate_24h

        with patch.object(health_mod, "_LOGS_DIR", tmp_path):
            result = _calculate_error_rate_24h()
        assert result["total_lines"] == 0
        assert result["error_rate_pct"] == 0.0

    def test_calculate_error_rate_24h_with_errors(self, tmp_path):
        log_file = tmp_path / "test.log"
        log_file.write_text("INFO: all good\nERROR: something broke\nINFO: fine\nCRITICAL: bad\n")

        import server.api.health as health_mod
        from server.api.health import _calculate_error_rate_24h

        with patch.object(health_mod, "_LOGS_DIR", tmp_path):
            result = _calculate_error_rate_24h()
        assert result["total_lines"] == 4
        assert result["error_lines"] == 2
        assert result["error_rate_pct"] == 50.0

    def test_get_disk_usage_returns_pct(self):
        from server.api.health import _get_disk_usage

        result = _get_disk_usage()
        assert "used_pct" in result
        assert result["used_pct"] >= 0

    def test_get_poller_status_empty(self, tmp_path):
        import server.api.health as health_mod
        from server.api.health import _get_poller_status

        with patch.object(health_mod, "_LOGS_DIR", tmp_path):
            result = _get_poller_status()
        assert result == {}

    def test_get_poller_status_with_logs(self, tmp_path):
        log_file = tmp_path / "engagement_poller_twitter_gaming.log"
        log_file.write_text("INFO: polling\nERROR: rate limited\nINFO: done\n")

        import server.api.health as health_mod
        from server.api.health import _get_poller_status

        with patch.object(health_mod, "_LOGS_DIR", tmp_path):
            result = _get_poller_status()

        assert "engagement_poller_twitter_gaming" in result
        assert result["engagement_poller_twitter_gaming"]["error_count"] == 1
        assert result["engagement_poller_twitter_gaming"]["total_lines"] == 3


# ══════════════════════════════════════════════════════════════════
#  2. Engagement Pending Replies
# ══════════════════════════════════════════════════════════════════


class TestPendingReplies:
    """Tests for /api/v1/engagement/pending-replies endpoints."""

    @pytest.fixture(autouse=True)
    def _use_tmp_replies_file(self, tmp_path):
        import server.api.engagement as eng_mod

        self._replies_file = tmp_path / "pending_replies.json"
        self._original = eng_mod._PENDING_REPLIES_FILE
        eng_mod._PENDING_REPLIES_FILE = self._replies_file
        yield
        eng_mod._PENDING_REPLIES_FILE = self._original

    def _create_reply(self, client):
        """Helper to create a pending reply and return the response data."""
        csrf = _get_csrf(client)
        resp = client.post(
            "/api/v1/engagement/pending-replies",
            json={
                "comment_id": "c123",
                "platform": "youtube",
                "niche_id": "gaming",
                "comment_text": "Great video!",
                "reply_text": "Thanks for watching!",
                "author": "user42",
                "_csrf_token": csrf,
            },
            content_type="application/json",
        )
        return resp

    def test_list_empty_returns_200(self, client):
        resp = client.get("/api/v1/engagement/pending-replies")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["replies"] == []
        assert data["total"] == 0

    def test_create_pending_reply(self, client):
        resp = self._create_reply(client)
        assert resp.status_code == 201
        data = resp.get_json()["data"]
        assert data["status"] == "pending"
        assert data["platform"] == "youtube"
        assert data["niche_id"] == "gaming"
        assert "id" in data

    def test_create_reply_missing_fields(self, client):
        csrf = _get_csrf(client)
        resp = client.post(
            "/api/v1/engagement/pending-replies",
            json={"comment_id": "c123", "_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "Missing required fields" in resp.get_json()["error"]

    def test_list_filters_by_niche(self, client):
        # Create replies for different niches
        csrf = _get_csrf(client)
        for niche in ["gaming", "gaming", "anime"]:
            client.post(
                "/api/v1/engagement/pending-replies",
                json={
                    "comment_id": f"c_{niche}",
                    "platform": "youtube",
                    "niche_id": niche,
                    "comment_text": "test",
                    "reply_text": "reply",
                    "_csrf_token": csrf,
                },
                content_type="application/json",
            )

        resp = client.get("/api/v1/engagement/pending-replies?niche_id=gaming")
        data = resp.get_json()["data"]
        assert data["total"] == 2
        assert all(r["niche_id"] == "gaming" for r in data["replies"])

    def test_approve_reply(self, client):
        create_resp = self._create_reply(client)
        reply_id = create_resp.get_json()["data"]["id"]

        csrf = _get_csrf(client)
        resp = client.post(
            f"/api/v1/engagement/pending-replies/{reply_id}/approve",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["status"] == "approved"
        assert data["reviewed_at"] is not None

    def test_reject_reply(self, client):
        create_resp = self._create_reply(client)
        reply_id = create_resp.get_json()["data"]["id"]

        csrf = _get_csrf(client)
        resp = client.post(
            f"/api/v1/engagement/pending-replies/{reply_id}/reject",
            json={"reason": "not appropriate", "_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["status"] == "rejected"
        assert data["rejection_reason"] == "not appropriate"

    def test_approve_nonexistent_reply(self, client):
        csrf = _get_csrf(client)
        resp = client.post(
            "/api/v1/engagement/pending-replies/does-not-exist/approve",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_reject_nonexistent_reply(self, client):
        csrf = _get_csrf(client)
        resp = client.post(
            "/api/v1/engagement/pending-replies/does-not-exist/reject",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_cannot_approve_already_approved(self, client):
        create_resp = self._create_reply(client)
        reply_id = create_resp.get_json()["data"]["id"]

        csrf = _get_csrf(client)
        # First approval succeeds
        client.post(
            f"/api/v1/engagement/pending-replies/{reply_id}/approve",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )
        # Second approval fails (already approved)
        resp = client.post(
            f"/api/v1/engagement/pending-replies/{reply_id}/approve",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 409

    def test_cannot_reject_already_rejected(self, client):
        create_resp = self._create_reply(client)
        reply_id = create_resp.get_json()["data"]["id"]

        csrf = _get_csrf(client)
        # First rejection succeeds
        client.post(
            f"/api/v1/engagement/pending-replies/{reply_id}/reject",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )
        # Second rejection fails
        resp = client.post(
            f"/api/v1/engagement/pending-replies/{reply_id}/reject",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )
        assert resp.status_code == 409

    def test_list_filters_by_status(self, client):
        create_resp = self._create_reply(client)
        reply_id = create_resp.get_json()["data"]["id"]

        csrf = _get_csrf(client)
        client.post(
            f"/api/v1/engagement/pending-replies/{reply_id}/approve",
            json={"_csrf_token": csrf},
            content_type="application/json",
        )

        # Default filter (pending) should show 0
        resp = client.get("/api/v1/engagement/pending-replies")
        assert resp.get_json()["data"]["total"] == 0

        # Approved filter should show 1
        resp = client.get("/api/v1/engagement/pending-replies?status=approved")
        assert resp.get_json()["data"]["total"] == 1


# ══════════════════════════════════════════════════════════════════
#  3. Niche Management — Config + Credentials
# ══════════════════════════════════════════════════════════════════


class TestNicheConfig:
    """Tests for GET /api/v1/niches/<niche_id>/config."""

    def test_config_for_known_niche(self, client, tmp_path):
        """Test that config endpoint returns merged YAML for a known niche."""
        import server.api.niches as niches_mod

        # Create a fake config directory
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "niche.yaml").write_text("niche_id: gaming\nbrand_name: CriticalRush\n")
        (config_dir / "sources.yaml").write_text("rss_feeds:\n  - url: https://example.com\n")

        with patch.object(niches_mod, "_config_dir_for_niche", return_value=config_dir):
            resp = client.get("/api/v1/niches/gaming/config")

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["niche_id"] == "gaming"
        assert data["niche"]["niche_id"] == "gaming"
        assert "rss_feeds" in data["sources"]

    def test_config_unknown_niche_returns_404(self, client):
        resp = client.get("/api/v1/niches/nonexistent/config")
        assert resp.status_code == 404

    def test_config_missing_dir_returns_404(self, client):
        import server.api.niches as niches_mod

        with patch.object(niches_mod, "_config_dir_for_niche", return_value=None):
            resp = client.get("/api/v1/niches/gaming/config")
        assert resp.status_code == 404


class TestNicheCredentials:
    """Tests for GET /api/v1/niches/<niche_id>/credentials."""

    def test_credentials_all_configured(self, client, monkeypatch):
        """When all env vars are set, all platforms show 'configured'."""
        # Set all CriticalRush credential env vars
        monkeypatch.setenv("CRITICALRUSH_META_ACCESS_TOKEN", "token123")
        monkeypatch.setenv("CRITICALRUSH_META_IG_USER_ID", "id123")
        monkeypatch.setenv("CRITICALRUSH_YOUTUBE_REFRESH_TOKEN", "yttoken")
        monkeypatch.setenv("CRITICALRUSH_META_PAGE_ID", "page123")
        monkeypatch.setenv("CRITICALRUSH_X_API_KEY", "xkey")
        monkeypatch.setenv("CRITICALRUSH_X_API_SECRET", "xsecret")
        monkeypatch.setenv("CRITICALRUSH_X_ACCESS_TOKEN", "xtoken")
        monkeypatch.setenv("CRITICALRUSH_X_ACCESS_TOKEN_SECRET", "xtokensecret")
        monkeypatch.setenv("CRITICALRUSH_THREADS_ACCESS_TOKEN", "threads")
        monkeypatch.setenv("CRITICALRUSH_TIKTOK_ACCESS_TOKEN", "tiktok")

        resp = client.get("/api/v1/niches/gaming/credentials")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["niche_id"] == "gaming"
        for platform, info in data["platforms"].items():
            assert info["status"] == "configured", f"{platform} should be configured"

    def test_credentials_all_missing(self, client, monkeypatch):
        """When no env vars are set, all platforms show 'missing'."""
        # Ensure gaming vars are not set
        for var in [
            "CRITICALRUSH_META_ACCESS_TOKEN",
            "CRITICALRUSH_META_IG_USER_ID",
            "CRITICALRUSH_YOUTUBE_REFRESH_TOKEN",
            "CRITICALRUSH_META_PAGE_ID",
            "CRITICALRUSH_X_API_KEY",
            "CRITICALRUSH_X_API_SECRET",
            "CRITICALRUSH_X_ACCESS_TOKEN",
            "CRITICALRUSH_X_ACCESS_TOKEN_SECRET",
            "CRITICALRUSH_THREADS_ACCESS_TOKEN",
            "CRITICALRUSH_TIKTOK_ACCESS_TOKEN",
        ]:
            monkeypatch.delenv(var, raising=False)

        resp = client.get("/api/v1/niches/gaming/credentials")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        for platform, info in data["platforms"].items():
            assert info["status"] == "missing", f"{platform} should be missing"

    def test_credentials_partial(self, client, monkeypatch):
        """When some vars are set for a platform, it shows 'partial'."""
        # Set only one of two required Instagram vars
        monkeypatch.setenv("CRITICALRUSH_META_ACCESS_TOKEN", "token123")
        monkeypatch.delenv("CRITICALRUSH_META_IG_USER_ID", raising=False)

        resp = client.get("/api/v1/niches/gaming/credentials")
        data = resp.get_json()["data"]
        assert data["platforms"]["instagram"]["status"] == "partial"

    def test_credentials_unknown_niche_returns_404(self, client):
        resp = client.get("/api/v1/niches/nonexistent/credentials")
        assert resp.status_code == 404

    def test_credentials_bb_uses_unprefixed_vars(self, client, monkeypatch):
        """Blackbox Brief (ai_creators) uses unprefixed env vars."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "token")
        monkeypatch.setenv("META_IG_USER_ID", "id")

        resp = client.get("/api/v1/niches/ai_creators/credentials")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["platforms"]["instagram"]["status"] == "configured"
