"""PR #549 (2026-06-25): SR-F wire pass 10 — main /api/v1/blueprints
list endpoint filters by per-user allowlist.

Companion to PRs #540 (/review-queue) + #543 (/queue) which closed
the SR-F list-side gap on the focused-view + queue endpoints. The
MAIN blueprint list endpoint (the one Mission Control + nav-bar
queries hit on every page load) was still un-filtered — a tenant-B
operator would see tenant-A blueprints in the default list view
even after #540 + #543.

If this pin regresses, restricted operators get a leaky default
list view; they can still see (but not mutate — that was closed by
#540-#548) cross-tenant blueprints in their dashboard.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_source_pin_list_blueprints_filters_by_allowlist():
    """The list_blueprints handler must call get_allowed_niches +
    filter records by _record_niche_id membership. Same shape as
    /review-queue + /queue."""
    import server.api.blueprints as mod

    src = Path(mod.__file__).read_text()
    # The list-filter shape (the helper line — `_record_niche_id(r) in _allowed`)
    # appears in list_blueprints body. Same exact pattern as the
    # /review-queue filter from #540 just inside list_blueprints.
    list_block_start = src.find('@bp.route("", methods=["GET"])')
    next_route = src.find("@bp.route(", list_block_start + 100)
    list_block = src[list_block_start:next_route]
    assert "get_allowed_niches" in list_block
    assert "_record_niche_id(r) in _allowed" in list_block
    # PR anchor scoped to the list_block (the PR mentions PR #549 in
    # the comment, not in the module-wide source — anchor pinned per-block)
    assert "PR #549" in list_block


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


def _records(*niches):
    """Build a list of records, one per supplied niche tag."""
    return [
        {"id": f"bp-{i}", "fields": {"niche_id": n, "status": "VISUAL_READY"}}
        for i, n in enumerate(niches)
    ]


def test_list_blueprints_filters_cross_tenant_records(monkeypatch):
    """Gaming-scoped operator hits GET /api/v1/blueprints → results
    filtered to gaming only (anime + sports + movies rows dropped)."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.all.return_value = _records(
        "gaming", "anime", "sports", "movies", "gaming"
    )

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/blueprints?niche_id=all")

    assert r.status_code == 200
    body = r.get_json()["data"]
    # Only the 2 gaming records survived
    ids = {row["id"] for row in body["data"]}
    assert ids == {"bp-0", "bp-4"}


def test_list_blueprints_unrestricted_shows_all(monkeypatch):
    """Admin operator with no allowlist sees every niche's blueprints.
    Backward compat — the existing single-operator workflow unchanged."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_client = MagicMock()
    fake_client.blueprints.all.return_value = _records(
        "gaming", "anime", "sports", "movies", "ai_creators"
    )

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/blueprints?niche_id=all")

    assert r.status_code == 200
    body = r.get_json()["data"]
    # All 5 records returned (no allowlist filter)
    assert len(body["data"]) == 5


def test_list_blueprints_filter_composes_with_niche_id_query_param(monkeypatch):
    """The existing niche_id query-param filter and the new allowlist
    filter must compose (intersection). Operator restricted to gaming
    + sports, requests niche_id=anime → empty (neither filter allows
    it)."""
    _scope_user_to_gaming(monkeypatch)
    # Widen the allowlist to gaming + sports for this test
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming,sports")

    fake_client = MagicMock()
    fake_client.blueprints.all.return_value = _records("gaming", "anime", "sports", "movies")

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            # Query for anime — operator allowlist doesn't include it
            r = c.get("/api/v1/blueprints?niche_id=anime")

    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["data"] == []


def test_list_blueprints_filter_after_niche_id_query_param(monkeypatch):
    """When niche_id query is ALSO restricted (gaming), the allowlist
    filter is a no-op because the query already pre-filtered. Pin
    that the result is correct even with both filters in play."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.all.return_value = _records("gaming", "anime", "sports", "gaming")

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.get("/api/v1/blueprints?niche_id=gaming")

    assert r.status_code == 200
    body = r.get_json()["data"]
    ids = {row["id"] for row in body["data"]}
    assert ids == {"bp-0", "bp-3"}  # both gaming records survive
