"""PR #540 (2026-06-24): SR-F wire — blueprint endpoints filter by
per-user niche allowlist.

Companion to PR #539 (the resolver foundation). This PR wires the
high-traffic blueprint endpoints to actually CALL the resolver and
either filter the result list (review-queue) or reject the action
with 403 (single-blueprint actions).

If these pins regress, the SR-F sprint silently re-opens:
  * /review-queue stops filtering → tenant-B operator sees tenant-A
    blueprints in their queue (UX bug + risk of cross-tenant click)
  * /<id>/review action stops 403-guarding → tenant-B can approve
    tenant-A blueprints by knowing the id (URL-guessable bug)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ── source pins ─────────────────────────────────────────────────────


def _src() -> str:
    import server.api.blueprints as mod

    return Path(mod.__file__).read_text()


def test_helper_record_niche_id_present():
    """The helper that extracts niche_id from both flat + nested
    record shapes must exist. Without it, the allowlist filter can't
    work across both Postgres + SharePoint backends."""
    src = _src()
    assert "def _record_niche_id(record: dict)" in src
    # Both record shapes covered
    assert 'flat = record.get("niche_id")' in src
    assert 'fields.get("niche_id")' in src


def test_helper_enforce_blueprint_niche_allowlist_present():
    """The per-action guard helper must exist + return api_error(403)
    on cross-tenant. Pin the structural shape so a refactor that
    drops the 403 silently lets cross-tenant actions through."""
    src = _src()
    assert "def _enforce_blueprint_niche_allowlist(record_id: str):" in src
    assert "code=403" in src
    # The early-return fast path for unrestricted users
    assert "if allowed is None:" in src
    # PR anchor
    assert "PR #540" in src


def test_review_queue_calls_allowlist_filter():
    """The /review-queue handler must call get_allowed_niches() and
    apply the filter alongside the existing niche_id query-param
    filter. The two filters compose (intersection)."""
    src = _src()
    # The filter call appears in review_queue's body
    queue_block_start = src.find('@bp.route("/review-queue"')
    queue_block_end = src.find("@bp.route", queue_block_start + 100)
    queue_block = src[queue_block_start:queue_block_end]
    assert "get_allowed_niches" in queue_block
    assert "_record_niche_id(r) in allowed" in queue_block


def test_review_action_endpoints_call_guard():
    """Both /<id>/review (POST) and /<id>/review-action (PATCH) must
    call _enforce_blueprint_niche_allowlist before invoking the
    underlying action. Pin both call sites — losing either re-opens
    the cross-tenant approve bug."""
    src = _src()
    # Two endpoint guards expected
    assert src.count("_enforce_blueprint_niche_allowlist(record_id)") >= 2


# ── behavioral pins (via mocked backlog client) ────────────────────


def _make_app_with_routes():
    """Build a minimal Flask app with just the blueprints API mounted
    so we can hit the routes without spinning up the full review_server."""
    from flask import Flask
    from server.api.blueprints import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def test_review_action_403_when_niche_outside_allowlist(monkeypatch):
    """Operator with allowlist={gaming} tries to approve an anime
    blueprint → 403 with a clear message. The underlying
    _execute_review_action MUST NOT be called."""
    # Allowlist: gaming only
    # dashboard's review_server.py does load_dotenv(override=True) at
    # import time. monkeypatch.setenv alone isn't enough — the .env
    # value wins inside the Flask request context. Patch BOTH the env
    # AND _username_for_session so the SR-F resolver sees the test
    # user regardless of env reset. monkeypatch.setenv (not direct
    # os.environ mutation) auto-reverts after the test — critical for
    # test isolation; other dashboard tests will fail if REVIEW_AUTH_USER
    # leaks between tests.
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    # Stub the backlog client to return an anime blueprint
    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "anime"}

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.review_server._execute_review_action") as mock_act,
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/bp1/review",
                json={"action": "approve"},
            )
    assert r.status_code == 403
    # The guard's message includes the niche + allowlist for debugging
    body = r.get_data(as_text=True)
    assert "anime" in body
    assert "PR #540" in body or "SR-F" in body
    # The action never fired — that's the whole point of the guard
    mock_act.assert_not_called()


def test_review_action_proceeds_when_niche_in_allowlist(monkeypatch):
    """Operator with allowlist={gaming} approves a gaming blueprint →
    normal success path. The guard is a transparent no-op."""
    # dashboard's review_server.py does load_dotenv(override=True) at
    # import time. monkeypatch.setenv alone isn't enough — the .env
    # value wins inside the Flask request context. Patch BOTH the env
    # AND _username_for_session so the SR-F resolver sees the test
    # user regardless of env reset. monkeypatch.setenv (not direct
    # os.environ mutation) auto-reverts after the test — critical for
    # test isolation; other dashboard tests will fail if REVIEW_AUTH_USER
    # leaks between tests.
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "gaming"}

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.review_server._execute_review_action") as mock_act,
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/bp1/review",
                json={"action": "approve"},
            )
    # Action proceeded (no 403); the exact status depends on downstream
    # but it's NOT 403 and _execute_review_action was called.
    assert r.status_code != 403
    mock_act.assert_called_once()


def test_review_action_proceeds_when_user_unrestricted(monkeypatch):
    """Default admin (no allowlist env var) → unrestricted →
    backward compat for the single-operator deploy. Guard is a
    fast no-op (no backlog fetch needed)."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "anime"}

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.review_server._execute_review_action") as mock_act,
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/bp1/review",
                json={"action": "approve"},
            )
    assert r.status_code != 403
    mock_act.assert_called_once()
    # Critical fast-path pin: unrestricted users shouldn't trigger a
    # backlog fetch in the guard (the guard short-circuits on None
    # allowlist BEFORE calling _get_client). One call total = the
    # approved-extra fetch later in the body, NOT the guard.
    # The guard would add a 2nd call if it didn't short-circuit.
    assert fake_client.blueprints.get.call_count <= 1


def test_review_action_patch_endpoint_also_guarded(monkeypatch):
    """The PATCH /<id>/review-action endpoint shares the same guard
    as POST /<id>/review. Pin behavioral parity so a future split
    that drops the guard from one of them is caught."""
    # Same dotenv-override workaround as the 403 test — see comment there.
    monkeypatch.setenv("REVIEW_AUTH_USER", "movies_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_MOVIES_OP", "movies")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "movies_op",
    )

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "sports"}

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.review_server._execute_review_action") as mock_act,
    ):
        with app.test_client() as c:
            r = c.patch(
                "/api/v1/blueprints/bp1/review-action",
                json={"action": "approve"},
            )
    assert r.status_code == 403
    mock_act.assert_not_called()


def test_review_action_guard_handles_missing_blueprint(monkeypatch):
    """When the backlog client returns None for the blueprint id,
    the guard's job is done — let the action handler 404. We must
    NOT 403 a non-existent blueprint (would leak existence info)."""
    # dashboard's review_server.py does load_dotenv(override=True) at
    # import time. monkeypatch.setenv alone isn't enough — the .env
    # value wins inside the Flask request context. Patch BOTH the env
    # AND _username_for_session so the SR-F resolver sees the test
    # user regardless of env reset. monkeypatch.setenv (not direct
    # os.environ mutation) auto-reverts after the test — critical for
    # test isolation; other dashboard tests will fail if REVIEW_AUTH_USER
    # leaks between tests.
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = None

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        # _execute_review_action is patched to no-op so the action
        # path doesn't try to touch the real backlog. We don't assert
        # on the mock — the assertion is purely about the guard NOT
        # 403'ing on a missing blueprint (existence isn't a tenant
        # issue; let the action handler 404).
        patch("server.review_server._execute_review_action"),
    ):
        with app.test_client() as c:
            r = c.post("/api/v1/blueprints/missing", json={"action": "approve"})
    # NOT 403 — guard returned None so the action proceeded.
    # The action's own error handling determines the actual response.
    assert r.status_code != 403


def test_record_niche_id_extracts_flat_field():
    """Postgres backend returns {niche_id: X, ...} flat — helper picks
    it up directly."""
    from server.api.blueprints import _record_niche_id

    assert _record_niche_id({"id": "1", "niche_id": "gaming"}) == "gaming"


def test_record_niche_id_extracts_nested_field():
    """SharePoint backend returns {id: X, fields: {niche_id: Y, ...}}
    — helper falls through to the nested dict."""
    from server.api.blueprints import _record_niche_id

    rec = {"id": "1", "fields": {"niche_id": "sports", "status": "VR"}}
    assert _record_niche_id(rec) == "sports"


def test_record_niche_id_falls_back_to_ai_creators():
    """No niche_id anywhere → ai_creators default. Matches the
    historical default at the review-queue filter site (line ~501)."""
    from server.api.blueprints import _record_niche_id

    assert _record_niche_id({"id": "1"}) == "ai_creators"
    assert _record_niche_id({"id": "1", "fields": {}}) == "ai_creators"


def test_record_niche_id_handles_non_dict():
    """Defensive: a malformed record (None, str, list) should return
    the fallback rather than crashing the guard."""
    from server.api.blueprints import _record_niche_id

    assert _record_niche_id(None) == "ai_creators"  # type: ignore[arg-type]
    assert _record_niche_id("not a dict") == "ai_creators"  # type: ignore[arg-type]
