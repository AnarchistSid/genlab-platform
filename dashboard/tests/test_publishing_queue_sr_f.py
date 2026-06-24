"""PR #543 (2026-06-24): SR-F wire pass 4 — publishing_queue endpoints
adopt the tenant guard.

Companion to PRs #539-#542 (foundation + 3 wire passes for
blueprints.py endpoints). This PR closes the parallel mutation
surface in publishing_queue.py: queue-level approve/hold/release/
unschedule/archive endpoints + the /queue list endpoint.

After this PR, blueprint operations are SR-F-guarded across BOTH
API surfaces (blueprints.py + publishing_queue.py). A tenant-B
operator can no longer mutate tenant-A blueprints through any
existing dashboard endpoint.

If these pins regress, an attacker / buggy caller knowing a
blueprint id could approve / hold / release / unschedule / archive
it via the queue API even when the per-user allowlist forbids it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ── source pins ─────────────────────────────────────────────────────


def test_queue_guard_helper_present():
    """The wrapper helper that imports from blueprints.py must exist.
    Lazy-import shape pinned — re-implementing the guard locally would
    drift from blueprints.py's version."""
    import server.api.publishing_queue as mod

    src = Path(mod.__file__).read_text()
    assert "def _enforce_queue_tenant_guard(record_id: str)" in src
    # Lazy import preserves Flask blueprint-registration order safety
    assert "from server.api.blueprints import _enforce_blueprint_niche_allowlist" in src


def test_all_5_mutation_endpoints_call_guard():
    """All 5 queue mutation endpoints (approve / hold / release /
    unschedule / archive) must call the guard. Pin at ≥5 so dropping
    one re-opens that endpoint."""
    import server.api.publishing_queue as mod

    src = Path(mod.__file__).read_text()
    count = src.count("_enforce_queue_tenant_guard(record_id)")
    assert count >= 5, (
        f"Expected ≥5 _enforce_queue_tenant_guard calls (one per "
        f"queue mutation endpoint); found {count}."
    )


def test_queue_list_endpoint_filters_by_allowlist():
    """The /queue GET endpoint must call get_allowed_niches() and
    filter items by _record_niche_id. Symmetric with /review-queue
    (#540) — operator scoped to gaming shouldn't see anime items."""
    import server.api.publishing_queue as mod

    src = Path(mod.__file__).read_text()
    # Filter call site inside list_queue
    assert "get_allowed_niches" in src
    assert "_record_niche_id(it) in _allowed" in src


def test_pr_543_anchor_present():
    """Future archaeologist anchor."""
    import server.api.publishing_queue as mod

    src = Path(mod.__file__).read_text()
    assert "PR #543" in src


# ── behavioral pins via Flask test client ──────────────────────────


def _make_app():
    """Minimal Flask app with the publishing_queue blueprint mounted.

    Also mount blueprints.py since publishing_queue's guard imports
    from there at call time."""
    from flask import Flask
    from server.api.blueprints import bp as bp_blueprints
    from server.api.publishing_queue import bp as bp_queue

    app = Flask(__name__)
    app.register_blueprint(bp_queue)
    app.register_blueprint(bp_blueprints)
    return app


def _scope_user_to_gaming(monkeypatch):
    """Apply the dotenv-override workaround from earlier SR-F tests."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def test_queue_approve_403_on_cross_tenant(monkeypatch):
    """Gaming-scoped operator approves an anime blueprint via the
    queue API → 403. The queue manager's approve() MUST NOT fire."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "anime"}

    fake_mgr = MagicMock()

    app = _make_app()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.api.publishing_queue._get_queue_manager", return_value=fake_mgr),
    ):
        with app.test_client() as c:
            r = c.post("/api/v1/queue/bp1/approve", json={})
    assert r.status_code == 403
    fake_mgr.approve.assert_not_called()


def test_queue_hold_403_on_cross_tenant(monkeypatch):
    """Same guard on /queue/<id>/hold — preventing the operator from
    blocking another tenant's publishing."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "sports"}
    fake_mgr = MagicMock()

    app = _make_app()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.api.publishing_queue._get_queue_manager", return_value=fake_mgr),
    ):
        with app.test_client() as c:
            r = c.post("/api/v1/queue/bp1/hold", json={"reason": "test"})
    assert r.status_code == 403
    fake_mgr.hold.assert_not_called()


def test_queue_release_unschedule_archive_all_403_on_cross_tenant(monkeypatch):
    """The remaining 3 mutation endpoints (release / unschedule /
    archive) all share the same guard. Behavioral pin for the trio
    so we don't need 3 separate test functions."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "movies"}
    fake_mgr = MagicMock()
    fake_sync = MagicMock()

    app = _make_app()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.api.publishing_queue._get_queue_manager", return_value=fake_mgr),
        patch("server.core.graph_sync.get_sync_client", return_value=fake_sync),
    ):
        with app.test_client() as c:
            r1 = c.post("/api/v1/queue/bp1/release", json={})
            r2 = c.post("/api/v1/queue/bp1/unschedule", json={})
            r3 = c.post("/api/v1/queue/bp1/archive", json={})

    assert r1.status_code == 403
    assert r2.status_code == 403
    assert r3.status_code == 403


def test_queue_list_filters_cross_tenant_items(monkeypatch):
    """Gaming-scoped operator hits /api/v1/queue → results filtered
    to gaming items only. Pre-PR-543: anime + sports items would
    leak into the response."""
    _scope_user_to_gaming(monkeypatch)

    fake_mgr = MagicMock()
    fake_mgr.get_queue.return_value = [
        {"id": "g1", "niche_id": "gaming"},
        {"id": "a1", "niche_id": "anime"},
        {"id": "s1", "niche_id": "sports"},
    ]

    app = _make_app()
    with (
        patch("server.api.publishing_queue._get_queue_manager", return_value=fake_mgr),
        # _transform_media is imported lazily inside list_queue; identity stub
        patch("server.api.blueprints._transform_media", side_effect=lambda x, lite: x),
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/queue")

    assert r.status_code == 200
    body = r.get_json()["data"]
    # Only gaming items survived the filter
    ids = [it["id"] for it in body["data"]]
    assert ids == ["g1"]
    assert body["meta"]["total"] == 1


def test_queue_list_unrestricted_preserves_all_items(monkeypatch):
    """Backward compat: admin operator sees every niche's items
    in the queue list."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_mgr = MagicMock()
    fake_mgr.get_queue.return_value = [
        {"id": "g1", "niche_id": "gaming"},
        {"id": "a1", "niche_id": "anime"},
        {"id": "s1", "niche_id": "sports"},
    ]

    app = _make_app()
    with (
        patch("server.api.publishing_queue._get_queue_manager", return_value=fake_mgr),
        patch("server.api.blueprints._transform_media", side_effect=lambda x, lite: x),
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/queue")

    assert r.status_code == 200
    body = r.get_json()["data"]
    assert len(body["data"]) == 3  # all 3 niches visible


def test_queue_approve_proceeds_for_same_tenant(monkeypatch):
    """Same-tenant approve → guard passes, queue manager fires.
    Pin the happy-path so the guard doesn't accidentally over-block."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "gaming"}
    fake_mgr = MagicMock()

    app = _make_app()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.api.publishing_queue._get_queue_manager", return_value=fake_mgr),
        # _log_queue_calibration is best-effort; stub to keep the test
        # focused on the guard wiring.
        patch("server.api.publishing_queue._log_queue_calibration"),
    ):
        with app.test_client() as c:
            r = c.post("/api/v1/queue/bp1/approve", json={})

    assert r.status_code != 403
    fake_mgr.approve.assert_called_once()
