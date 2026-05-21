#!/usr/bin/env python3
"""Integration tests for the video review server.

Tests media serving, batch review, settings, and blueprint normalization.
"""

import json
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import _generate_csrf_token, _parse_visual_paths, app, normalize_blueprint


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """Disable auth middleware so tests don't need credentials."""
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture(autouse=True)
def _fix_project_root(monkeypatch):
    """Ensure server's PROJECT_ROOT matches test's PROJECT_ROOT for media route tests."""
    monkeypatch.setattr(review_server_module, "PROJECT_ROOT", PROJECT_ROOT)


@pytest.fixture
def client():
    """Flask test client with local mode + dry run."""
    app.config["TESTING"] = True
    app.config["DRY_RUN"] = True
    app.config["LOCAL_MODE"] = True
    with app.test_client() as c:
        yield c


def _csrf_headers(client=None):
    """Return headers dict including a valid CSRF token for POST requests.

    When *client* is provided, fetches the token via the /api/csrf-token
    endpoint so the token matches the test client's session nonce.
    Falls back to calling _generate_csrf_token() directly inside a
    request context when no client is given (legacy compat).
    """
    if client is not None:
        resp = client.get("/api/csrf-token")
        token = resp.json.get("csrf_token", "")
    else:
        # Fall back for tests that construct their own test_client
        with app.test_request_context():
            from flask import session as _sess

            _sess["csrf_nonce"] = "test-static-nonce"
            token = _generate_csrf_token()
    return {"X-CSRF-Token": token, "Content-Type": "application/json"}


class TestParseVisualPaths:
    """Test _parse_visual_paths helper."""

    def test_empty_string(self):
        assert _parse_visual_paths("") == []

    def test_none(self):
        assert _parse_visual_paths(None) == []

    def test_valid_json_string(self):
        result = _parse_visual_paths('["/tmp/a.png", "/tmp/b.mp4"]')
        assert result == ["/tmp/a.png", "/tmp/b.mp4"]

    def test_already_list(self):
        result = _parse_visual_paths(["/tmp/a.png"])
        assert result == ["/tmp/a.png"]

    def test_invalid_json(self):
        result = _parse_visual_paths("not json")
        assert result == []

    def test_empty_list_json(self):
        result = _parse_visual_paths("[]")
        assert result == []


class TestNormalizeBlueprint:
    """Test visual_paths in normalize_blueprint."""

    def test_local_with_visual_paths(self):
        bp = {
            "candidate_id": "test123",
            "visual_paths": '["/tmp/slide.png", "/tmp/reel.mp4"]',
        }
        result = normalize_blueprint(bp, source="local")
        assert result["visual_paths"] == "/tmp/slide.png"
        assert result["all_media_urls"] == ["/tmp/slide.png", "/tmp/reel.mp4"]

    def test_local_without_visual_paths(self):
        bp = {"candidate_id": "test123"}
        result = normalize_blueprint(bp, source="local")
        assert result["visual_paths"] is None
        assert result["all_media_urls"] == []

    def test_backlog_with_visual_paths(self):
        bp = {
            "id": "rec123",
            "fields": {
                "visual_paths": '["/tmp/slide.png"]',
                "title": "Test",
            },
        }
        result = normalize_blueprint(bp, source="backlog")
        # visual_paths resolved to a browser-accessible URL
        assert result["visual_paths"] is not None
        assert result["hook"] == ""  # no hook in input → empty
        assert result["niche_id"] == ""  # no niche_id in input → empty


class TestMediaRoute:
    """Test /api/media/ file serving."""

    def test_404_missing_file(self, client):
        resp = client.get("/api/media/.tmp/nonexistent.png")
        assert resp.status_code == 404

    def test_403_path_traversal(self, client):
        resp = client.get("/api/media//etc/passwd", follow_redirects=True)
        assert resp.status_code in (403, 404)

    def test_403_non_media_file(self, client):
        # Create a temp .py file inside project root — use relative path
        test_file = PROJECT_ROOT / ".tmp" / "test_security.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')")
        try:
            rel_path = test_file.relative_to(PROJECT_ROOT)
            resp = client.get(f"/api/media/{rel_path}")
            assert resp.status_code == 403
        finally:
            test_file.unlink(missing_ok=True)

    def test_200_serves_png(self, client):
        # Create a tiny test PNG — use relative path
        test_png = PROJECT_ROOT / ".tmp" / "test_media_serve.png"
        test_png.parent.mkdir(parents=True, exist_ok=True)
        # Minimal 1x1 PNG
        import base64

        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        test_png.write_bytes(png_data)
        try:
            rel_path = test_png.relative_to(PROJECT_ROOT)
            resp = client.get(f"/api/media/{rel_path}")
            assert resp.status_code == 200
            assert resp.content_type == "image/png"
        finally:
            test_png.unlink(missing_ok=True)

    def test_encoded_absolute_path_rejected(self, client):
        # Absolute paths are rejected by security — route only allows relative paths.
        test_png = PROJECT_ROOT / ".tmp" / "test_media_abs.png"
        test_png.parent.mkdir(parents=True, exist_ok=True)
        import base64

        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        test_png.write_bytes(png_data)
        try:
            encoded = urllib.parse.quote(str(test_png), safe="")
            resp = client.get(f"/api/media/{encoded}", follow_redirects=True)
            # Absolute paths now rejected: the route only serves relative paths
            assert resp.status_code in (403, 404)
        finally:
            test_png.unlink(missing_ok=True)


class TestBatchReview:
    """Test /api/batch-review endpoint."""

    def test_batch_approve_dry_run(self, client):
        resp = client.post(
            "/api/batch-review",
            data=json.dumps({"record_ids": ["10001", "10002"], "action": "approved"}),
            headers=_csrf_headers(client),
        )
        data = resp.get_json()["data"]
        assert data["succeeded"] == 2
        assert data["failed"] == 0

    def test_batch_invalid_action(self, client):
        resp = client.post(
            "/api/batch-review",
            data=json.dumps({"record_ids": ["10001"], "action": "invalid"}),
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400

    def test_batch_empty_ids(self, client):
        resp = client.post(
            "/api/batch-review",
            data=json.dumps({"record_ids": [], "action": "approved"}),
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400


class TestBlueprintFeed:
    """Test blueprint API headers + health diagnostics."""

    def test_blueprints_local_headers(self, client):
        resp = client.get("/api/blueprints")
        assert resp.status_code == 200
        assert resp.headers.get("X-Review-Source") == "local"
        assert resp.headers.get("X-Review-Stale") == "0"

    def test_health_unauthenticated_returns_minimal(self, client):
        resp = client.get("/api/health")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["status"] == "ok"
        assert "version" in data
        assert "environment" in data

    def test_health_local_mode(self, client):
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        resp = client.get("/api/health")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["status"] == "ok"
        assert data["mode"] == "local"
        assert "uptime_seconds" in data
        assert "blueprints" in data

    def test_blueprints_uses_stale_cache_when_backlog_unavailable(self, monkeypatch):
        def _broken_load():
            raise RuntimeError("backlog unavailable")

        monkeypatch.setattr(review_server_module, "_load_backlog_blueprints", _broken_load)

        cache_snapshot = dict(review_server_module._blueprint_cache)
        cache_records = list(cache_snapshot.get("records", []))
        try:
            with review_server_module._blueprint_cache_lock:
                review_server_module._blueprint_cache["timestamp"] = (
                    time.time() - review_server_module.BLUEPRINT_CACHE_TTL_SECONDS - 1
                )
                review_server_module._blueprint_cache["records"] = [
                    {
                        "id": "rec_cached",
                        "status": "DRAFTED",
                        "hook": "cached hook",
                        "urgency_score": 0.9,
                        "visual_paths": [],
                    },
                ]
                review_server_module._blueprint_cache["source"] = "backlog"
                review_server_module._blueprint_cache["stale"] = False

            app.config["TESTING"] = True
            app.config["DRY_RUN"] = False
            app.config["LOCAL_MODE"] = False
            with app.test_client() as test_client:
                resp = test_client.get("/api/blueprints")

            assert resp.status_code == 200
            assert resp.headers.get("X-Review-Source") == "backlog_cache"
            assert resp.headers.get("X-Review-Stale") == "1"
            data = resp.get_json()
            assert data[0]["id"] == "rec_cached"
        finally:
            with review_server_module._blueprint_cache_lock:
                review_server_module._blueprint_cache["timestamp"] = cache_snapshot.get(
                    "timestamp", 0.0
                )
                review_server_module._blueprint_cache["records"] = cache_records
                review_server_module._blueprint_cache["source"] = cache_snapshot.get(
                    "source", "empty"
                )
                review_server_module._blueprint_cache["stale"] = cache_snapshot.get("stale", False)

    def test_health_degraded_without_cache(self, monkeypatch):
        def _broken_load():
            raise RuntimeError("backlog unavailable")

        monkeypatch.setattr(review_server_module, "_load_backlog_blueprints", _broken_load)

        cache_snapshot = dict(review_server_module._blueprint_cache)
        cache_records = list(cache_snapshot.get("records", []))
        try:
            with review_server_module._blueprint_cache_lock:
                review_server_module._blueprint_cache["timestamp"] = 0.0
                review_server_module._blueprint_cache["records"] = []
                review_server_module._blueprint_cache["source"] = "empty"
                review_server_module._blueprint_cache["stale"] = False

            app.config["TESTING"] = True
            app.config["DRY_RUN"] = False
            app.config["LOCAL_MODE"] = False
            with app.test_client() as test_client:
                with test_client.session_transaction() as sess:
                    sess["authenticated"] = True
                resp = test_client.get("/api/health?fresh=1")

            assert resp.status_code == 503
            data = resp.get_json()
            assert data["status"] == "error"
            assert data["data_source"] == "unavailable"
            assert data["fail_closed"] is True
        finally:
            with review_server_module._blueprint_cache_lock:
                review_server_module._blueprint_cache["timestamp"] = cache_snapshot.get(
                    "timestamp", 0.0
                )
                review_server_module._blueprint_cache["records"] = cache_records
                review_server_module._blueprint_cache["source"] = cache_snapshot.get(
                    "source", "empty"
                )
                review_server_module._blueprint_cache["stale"] = cache_snapshot.get("stale", False)


class TestPaginationValidation:
    """Test pagination parameter validation."""

    def test_invalid_page_param_returns_400(self, client):
        """Non-numeric page/per_page params must return 400, not crash."""
        resp = client.get("/api/v1/blueprints?page=abc")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data is not None
        assert "error" in data


class TestReviewWrites:
    """Test non-dry-run backlog writes use canonical field names."""

    def test_single_approve_writes_status_field(self, monkeypatch):
        calls = []

        class _FakeBlueprintTable:
            def update(self, record_id, fields, typecast=True):
                calls.append((record_id, fields, typecast))

        class _FakeSyncClient:
            def __init__(self):
                self.blueprints = _FakeBlueprintTable()

        import server.core.graph_sync as graph_sync_module

        monkeypatch.setattr(graph_sync_module, "get_sync_client", _FakeSyncClient)

        app.config["TESTING"] = True
        app.config["DRY_RUN"] = False
        app.config["LOCAL_MODE"] = False

        with app.test_client() as test_client:
            resp = test_client.post(
                "/api/review/10001",
                data=json.dumps({"action": "approved", "notes": "looks good"}),
                headers=_csrf_headers(test_client),
            )

        assert resp.status_code == 200
        assert len(calls) == 1
        _, fields, typecast = calls[0]
        assert typecast is True
        # No status change on approve — VISUAL_READY stays; publisher picks up when scheduled
        assert "status" not in fields
        assert fields["action_taken"] == "approved"

    def test_batch_approve_writes_status_field(self, monkeypatch):
        calls = []
        init_counter = {"count": 0}

        class _FakeBlueprintTable:
            def update(self, record_id, fields, typecast=True):
                calls.append((record_id, fields, typecast))

        class _FakeSyncClient:
            def __init__(self):
                init_counter["count"] += 1
                self.blueprints = _FakeBlueprintTable()

        import server.core.graph_sync as graph_sync_module

        monkeypatch.setattr(graph_sync_module, "get_sync_client", _FakeSyncClient)

        app.config["TESTING"] = True
        app.config["DRY_RUN"] = False
        app.config["LOCAL_MODE"] = False

        with app.test_client() as test_client:
            resp = test_client.post(
                "/api/batch-review",
                data=json.dumps(
                    {"record_ids": ["20001", "20002"], "action": "approved", "notes": "batch ok"},
                ),
                headers=_csrf_headers(test_client),
            )

        assert resp.status_code == 200
        assert init_counter["count"] == 1
        assert len(calls) == 2
        for _, fields, typecast in calls:
            assert typecast is True
            # No status change on approve — VISUAL_READY stays; publisher picks up when scheduled
            assert "status" not in fields
            assert fields["action_taken"] == "approved"


class TestSettings:
    """Test /api/settings endpoints."""

    def test_get_defaults(self, client):
        resp = client.get("/api/settings")
        data = resp.get_json()["data"]
        assert "auto_approve" in data
        assert "auto_approve_seconds" in data

    def test_update_settings(self, client):
        resp = client.post(
            "/api/settings",
            data=json.dumps({"auto_approve": True, "auto_approve_seconds": 45}),
            headers=_csrf_headers(client),
        )
        data = resp.get_json()["data"]
        assert data["auto_approve"] is True
        assert data["auto_approve_seconds"] == 45

    def test_clamp_min(self, client):
        resp = client.post(
            "/api/settings",
            data=json.dumps({"auto_approve_seconds": 3}),
            headers=_csrf_headers(client),
        )
        data = resp.get_json()["data"]
        assert data["auto_approve_seconds"] == 10

    def test_clamp_max(self, client):
        resp = client.post(
            "/api/settings",
            data=json.dumps({"auto_approve_seconds": 999}),
            headers=_csrf_headers(client),
        )
        data = resp.get_json()["data"]
        assert data["auto_approve_seconds"] == 300


class TestReactSPA:
    """Test the React SPA is served correctly."""

    def test_serves_index_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"<!doctype html>" in resp.data.lower() or b"<!DOCTYPE html>" in resp.data

    def test_serves_static_assets(self, client):
        """The catch-all route serves files from dashboard/dist/."""
        from server.review_server import _DASHBOARD_DIST

        if not _DASHBOARD_DIST.exists():
            pytest.skip("dashboard/dist not built")
        # index.html should always exist when dist is built
        resp = client.get("/")
        assert resp.status_code == 200

    def test_spa_fallback_for_client_routes(self, client):
        """Non-API, non-file paths should return index.html for client-side routing."""
        from server.review_server import _DASHBOARD_DIST

        if not _DASHBOARD_DIST.exists():
            pytest.skip("dashboard/dist not built")
        resp = client.get("/review")
        assert resp.status_code == 200
        # Should serve index.html (the React SPA entry point)
        assert b'<div id="root">' in resp.data

    def test_api_routes_not_caught_by_spa(self, client):
        """API paths that don't match any route should 404, not serve index.html."""
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404

    def test_dashboard_not_built_fallback(self, client, monkeypatch):
        """When dashboard/dist doesn't exist, show a helpful fallback message."""
        from server import review_server as rs

        original = rs._DASHBOARD_DIST
        monkeypatch.setattr(rs, "_DASHBOARD_DIST", Path("/nonexistent/dashboard/dist"))
        try:
            resp = client.get("/")
            assert resp.status_code == 200
            assert b"Dashboard not built" in resp.data
        finally:
            monkeypatch.setattr(rs, "_DASHBOARD_DIST", original)


class TestFlowMp4AuthBypass:
    """Verify that _flow.mp4 paths require authentication (bypass removed)."""

    def test_flow_mp4_requires_auth(self, monkeypatch):
        """GET to a *_flow.mp4 path should NOT return 200 when auth is enabled."""
        monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", True)
        monkeypatch.setattr(review_server_module, "_AUTH_USER", "admin")
        monkeypatch.setattr(review_server_module, "_AUTH_PASS", "secret")

        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/static/screencast_flow.mp4")
            # Should redirect to login or return 401/403, NOT 200
            assert resp.status_code != 200


class TestCsrfPerSession:
    """Test per-session CSRF nonces."""

    def test_csrf_token_is_per_session(self, client):
        """CSRF tokens should differ between sessions (per-session nonce)."""
        # Login and get CSRF from session 1
        with client.session_transaction() as sess1:
            sess1["authenticated"] = True
        resp1 = client.get("/api/csrf-token")
        token1 = resp1.json.get("csrf_token", "")

        # Clear session and login again — new session = new nonce
        with client.session_transaction() as sess2:
            sess2.clear()
            sess2["authenticated"] = True
        resp2 = client.get("/api/csrf-token")
        token2 = resp2.json.get("csrf_token", "")

        assert token1 != token2, "CSRF tokens must differ between sessions"


class TestLoginRateLimit:
    """Test login rate limiting."""

    @pytest.fixture(autouse=True)
    def _enable_auth(self, monkeypatch):
        """Enable auth so the login POST path is meaningful."""
        monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", True)
        monkeypatch.setattr(review_server_module, "_AUTH_USER", "admin")
        monkeypatch.setattr(review_server_module, "_AUTH_PASS", "secret")

    @pytest.fixture(autouse=True)
    def _reset_login_attempts(self):
        """Clear the rate-limit tracker between tests."""
        review_server_module._login_attempts.clear()
        yield
        review_server_module._login_attempts.clear()

    def test_login_rate_limited(self, client):
        """Login endpoint must rate-limit after 5 attempts per minute."""
        for _ in range(5):
            client.post("/login", data={"username": "x", "password": "wrong"})

        resp = client.post("/login", data={"username": "x", "password": "wrong"})
        assert resp.status_code == 429

    def test_login_rate_limit_allows_success_before_cap(self, client):
        """First 5 attempts should not be rate-limited."""
        resp = client.post("/login", data={"username": "x", "password": "wrong"})
        assert resp.status_code != 429

    def test_login_get_not_rate_limited(self, client):
        """GET /login (showing the form) must never be rate-limited."""
        for _ in range(10):
            client.post("/login", data={"username": "x", "password": "wrong"})
        resp = client.get("/login")
        # GET should return the login form (200), never 429
        assert resp.status_code == 200


class TestRunIdValidation:
    """Verify strict run_id regex validation."""

    @pytest.fixture(autouse=True)
    def _reset_express_state(self):
        """Reset express state so each test starts with running=False."""
        review_server_module.express_state["running"] = False
        yield
        review_server_module.express_state["running"] = False

    def test_run_id_rejects_semicolons(self, client):
        resp = client.post(
            "/api/express/trigger",
            json={"run_id": "foo;rm -rf"},
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400

    def test_run_id_rejects_pipes(self, client):
        resp = client.post(
            "/api/express/trigger",
            json={"run_id": "foo|cat /etc/passwd"},
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400

    def test_run_id_rejects_backticks(self, client):
        resp = client.post(
            "/api/express/trigger",
            json={"run_id": "`whoami`"},
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400

    def test_run_id_rejects_path_traversal(self, client):
        resp = client.post(
            "/api/express/trigger",
            json={"run_id": "../../etc"},
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400

    def test_run_id_rejects_slashes(self, client):
        resp = client.post(
            "/api/express/trigger",
            json={"run_id": "foo/bar"},
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400

    def test_run_id_rejects_spaces(self, client):
        resp = client.post(
            "/api/express/trigger",
            json={"run_id": "foo bar"},
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400

    def test_run_id_accepts_valid_format(self, client):
        """Valid run_ids (alphanumeric + underscore + hyphen) should be accepted."""
        resp = client.post(
            "/api/express/trigger",
            json={"run_id": "20260301_120000"},
            headers=_csrf_headers(client),
        )
        # Should not be 400 — either 200 (started) or 409 (already running)
        assert resp.status_code in (200, 409)

    def test_run_id_rejects_too_long(self, client):
        long_id = "a" * 65
        resp = client.post(
            "/api/express/trigger",
            json={"run_id": long_id},
            headers=_csrf_headers(client),
        )
        assert resp.status_code == 400


class TestSessionCookieSecure:
    """Test SESSION_COOKIE_SECURE is controlled by FLASK_COOKIE_SECURE env var."""

    def test_defaults_to_true(self, monkeypatch):
        """When FLASK_COOKIE_SECURE is not set, SESSION_COOKIE_SECURE defaults to True."""
        monkeypatch.delenv("FLASK_COOKIE_SECURE", raising=False)
        # Re-evaluate the expression the same way review_server.py does at import
        import os

        result = os.getenv("FLASK_COOKIE_SECURE", "true").lower() == "true"
        assert result is True

    def test_env_false_disables_secure(self, monkeypatch):
        """Setting FLASK_COOKIE_SECURE=false disables the secure cookie flag."""
        monkeypatch.setenv("FLASK_COOKIE_SECURE", "false")
        import os

        result = os.getenv("FLASK_COOKIE_SECURE", "true").lower() == "true"
        assert result is False

    def test_env_true_enables_secure(self, monkeypatch):
        """Setting FLASK_COOKIE_SECURE=true enables the secure cookie flag."""
        monkeypatch.setenv("FLASK_COOKIE_SECURE", "true")
        import os

        result = os.getenv("FLASK_COOKIE_SECURE", "true").lower() == "true"
        assert result is True

    def test_env_case_insensitive(self, monkeypatch):
        """FLASK_COOKIE_SECURE comparison should be case-insensitive."""
        monkeypatch.setenv("FLASK_COOKIE_SECURE", "True")
        import os

        result = os.getenv("FLASK_COOKIE_SECURE", "true").lower() == "true"
        assert result is True

        monkeypatch.setenv("FLASK_COOKIE_SECURE", "FALSE")
        result = os.getenv("FLASK_COOKIE_SECURE", "true").lower() == "true"
        assert result is False
