"""PR #550 (2026-06-25): SR-F wire pass 11 — per-record GET endpoints
adopt the per-tenant guard.

Companion to:
  * PR #540: /<id>/review + /<id>/review-action (mutations)
  * PR #541: /<id>/{approve-and-schedule, schedule, content} (mutations)
  * PR #549: main GET / list filter
  * THIS PR: GET /<id> + GET /<id>/auto-approval-preview (read-only)

Pre-PR-550 a restricted operator could fetch any single blueprint by
UUID (information disclosure) even though mutations were already
403'd by #540-#548. Auto-approval-preview was similar — read-only
but exposed gate verdicts for cross-tenant blueprints.

If these pins regress, the restricted-operator dashboard leaks
single-record read access. Less impactful than the list-side leak
(which #549 closed) but still cross-tenant info disclosure.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_source_pin_get_blueprint_calls_guard():
    """The /<id> GET handler must call _enforce_blueprint_niche_allowlist
    right after RECORD_RE validation."""
    import server.api.blueprints as mod

    src = Path(mod.__file__).read_text()
    # Locate the get_blueprint handler block
    handler_start = src.find('@bp.route("/<record_id>", methods=["GET"])')
    next_route = src.find("@bp.route(", handler_start + 100)
    handler = src[handler_start:next_route]
    assert "_enforce_blueprint_niche_allowlist(record_id)" in handler
    assert "PR #550" in handler


def test_source_pin_auto_approval_preview_calls_guard():
    """Same guard on the auto-approval-preview endpoint — exposing
    gate verdicts cross-tenant is info disclosure."""
    import server.api.blueprints as mod

    src = Path(mod.__file__).read_text()
    handler_start = src.find('@bp.route("/<record_id>/auto-approval-preview"')
    next_route = src.find("@bp.route(", handler_start + 100)
    handler = src[handler_start:next_route]
    assert "_enforce_blueprint_niche_allowlist(record_id)" in handler


def _scope_user_to_gaming(monkeypatch):
    """dotenv-override workaround from earlier SR-F tests."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def _make_app():
    from flask import Flask
    from server.api.blueprints import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def test_get_blueprint_403_on_cross_tenant(monkeypatch):
    """Gaming-scoped operator tries to fetch an anime blueprint
    by UUID → 403. The blueprint payload MUST NOT leak."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp1",
        "niche_id": "anime",
        "fields": {"niche_id": "anime", "hook": "secret anime hook"},
    }

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/blueprints/bp1")
    assert r.status_code == 403
    # The secret hook MUST NOT appear in the response body
    body = r.get_data(as_text=True)
    assert "secret anime hook" not in body


def test_get_blueprint_proceeds_for_same_tenant(monkeypatch):
    """Same-tenant fetch returns the blueprint payload."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp1",
        "niche_id": "gaming",
        "fields": {"niche_id": "gaming", "hook": "gaming hook"},
    }

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/blueprints/bp1")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["data"]["hook"] == "gaming hook"


def test_auto_approval_preview_403_on_cross_tenant(monkeypatch):
    """Preview endpoint also guarded — gate verdicts for cross-
    tenant blueprints would otherwise leak."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp1",
        "niche_id": "movies",
        "fields": {"niche_id": "movies"},
    }

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/blueprints/bp1/auto-approval-preview")
    assert r.status_code == 403


def test_get_blueprint_unrestricted_admin_can_fetch_any(monkeypatch):
    """Backward compat: admin operator with no allowlist can fetch
    any blueprint."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp1",
        "fields": {"niche_id": "anime", "hook": "admin sees this"},
    }

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/blueprints/bp1")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["data"]["hook"] == "admin sees this"
