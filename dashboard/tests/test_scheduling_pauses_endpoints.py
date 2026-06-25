"""Niche-pause dashboard endpoints — PR #577 consumer.

Pins:

  GET /api/v1/scheduling/pauses
    * Returns list of active pauses (DTO-shaped, ISO strings)
    * SR-F post-filters to allowlist niches
    * Empty list on ImportError (pre-PR-577 core)

  POST /api/v1/scheduling/pauses
    * niche_id + reason required (400 when missing)
    * paused_until accepts ISO-8601 with/without Z timezone marker
    * paused_by auto-captured from session user
    * SR-F: 403 when niche outside allowlist
    * pause helper returning False → 500 (fail-CLOSED for writes)

  DELETE /api/v1/scheduling/pauses/<niche_id>
    * Idempotent (200 even when no pause existed)
    * SR-F: 403 when niche outside allowlist
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

# ── Flask app fixture ─────────────────────────────────────────────


def _make_app():
    from flask import Flask
    from server.api.scheduling_pauses import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


# ── GET /scheduling/pauses ────────────────────────────────────────


def test_list_returns_empty_on_import_error():
    """Pre-PR-577 core → ImportError on niche_pause module → empty
    list (graceful degrade, not 500)."""
    app = _make_app()
    with patch.dict("sys.modules", {"genlab_core.scheduling.niche_pause": None}):
        with app.test_client() as c:
            r = c.get("/api/v1/scheduling/pauses")
    assert r.status_code == 200
    assert r.get_json()["data"] == []


def test_list_returns_dto_with_iso_strings():
    """Pin DTO → JSON shape: datetimes ISO-stringified, NULL
    paused_until preserved as None for indefinite pauses."""
    from genlab_core.scheduling import niche_pause as np_mod
    from genlab_core.scheduling.niche_pause import NichePause

    fake = [
        NichePause(
            niche_id="gaming",
            paused_until=None,  # indefinite
            reason="investigating_engagement_drop",
            paused_by="admin",
            created_at=datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC),
        ),
        NichePause(
            niche_id="anime",
            paused_until=datetime(2026, 6, 26, tzinfo=UTC),
            reason="shadowban_warning",
            paused_by="ops_bot",
            created_at=datetime(2026, 6, 25, 10, 0, 0, tzinfo=UTC),
        ),
    ]

    app = _make_app()
    with patch.object(np_mod, "list_active_pauses", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/scheduling/pauses")
    rows = r.get_json()["data"]
    assert len(rows) == 2
    # Indefinite pause: paused_until is None
    assert rows[0]["niche_id"] == "gaming"
    assert rows[0]["paused_until"] is None
    assert rows[0]["created_at"] == "2026-06-25T12:00:00+00:00"
    # Timed pause: ISO-stringified
    assert rows[1]["paused_until"] == "2026-06-26T00:00:00+00:00"


def test_list_sr_f_filters_to_allowlist(monkeypatch):
    """SR-F: restricted operator sees only their allowed niches'
    pauses. Same post-filter pattern as other list endpoints."""
    from genlab_core.scheduling import niche_pause as np_mod
    from genlab_core.scheduling.niche_pause import NichePause

    fake = [
        NichePause("gaming", None, "x", "admin", datetime(2026, 6, 25, tzinfo=UTC)),
        NichePause("anime", None, "x", "admin", datetime(2026, 6, 25, tzinfo=UTC)),
    ]

    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    app = _make_app()
    with patch.object(np_mod, "list_active_pauses", return_value=fake):
        with app.test_client() as c:
            r = c.get("/api/v1/scheduling/pauses")
    rows = r.get_json()["data"]
    # Only gaming row visible to gaming-scoped operator
    assert len(rows) == 1
    assert rows[0]["niche_id"] == "gaming"


# ── POST /scheduling/pauses ───────────────────────────────────────


def test_post_requires_niche_id():
    app = _make_app()
    with app.test_client() as c:
        r = c.post("/api/v1/scheduling/pauses", json={"reason": "x"})
    body = r.get_json() or {}
    assert "niche_id" in (body.get("error", "") or "")


def test_post_requires_reason():
    """Server-side enforcement of audit-log invariant."""
    app = _make_app()
    with app.test_client() as c:
        r = c.post("/api/v1/scheduling/pauses", json={"niche_id": "gaming"})
    body = r.get_json() or {}
    assert "reason" in (body.get("error", "") or "")


def test_post_403_on_cross_tenant_niche(monkeypatch):
    """SR-F: restricted operator can't pause a niche outside their
    allowlist (would be cross-tenant abuse)."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    app = _make_app()
    with app.test_client() as c:
        r = c.post(
            "/api/v1/scheduling/pauses",
            json={"niche_id": "anime", "reason": "test"},
        )
    assert r.status_code == 403


def test_post_calls_pause_helper_with_session_user(monkeypatch):
    """paused_by auto-captured from session — operators don't have
    to type it in the request body."""
    from genlab_core.scheduling import niche_pause as np_mod

    monkeypatch.setenv("REVIEW_AUTH_USER", "alice")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "alice",
    )

    app = _make_app()
    with patch.object(np_mod, "pause", return_value=True) as mock_pause:
        with app.test_client() as c:
            r = c.post(
                "/api/v1/scheduling/pauses",
                json={"niche_id": "gaming", "reason": "copyright_strike"},
            )
    assert r.status_code == 200
    # Pin: paused_by=alice, not absent and not empty
    mock_pause.assert_called_once_with(
        niche_id="gaming",
        reason="copyright_strike",
        paused_until=None,
        paused_by="alice",
    )


def test_post_accepts_iso_paused_until(monkeypatch):
    """paused_until is parsed from ISO-8601 (with or without Z marker)
    into a datetime + passed to the pause helper."""
    from genlab_core.scheduling import niche_pause as np_mod

    app = _make_app()
    with patch.object(np_mod, "pause", return_value=True) as mock_pause:
        with app.test_client() as c:
            c.post(
                "/api/v1/scheduling/pauses",
                json={
                    "niche_id": "gaming",
                    "reason": "test",
                    "paused_until": "2026-06-26T00:00:00Z",
                },
            )
    bound = mock_pause.call_args.kwargs
    assert bound["paused_until"] == datetime(2026, 6, 26, tzinfo=UTC)


def test_post_rejects_invalid_paused_until():
    app = _make_app()
    with app.test_client() as c:
        r = c.post(
            "/api/v1/scheduling/pauses",
            json={"niche_id": "gaming", "reason": "test", "paused_until": "not-a-date"},
        )
    body = r.get_json() or {}
    assert "ISO-8601" in (body.get("error", "") or "")


def test_post_returns_500_when_pause_helper_fails():
    """pause() returning False (fail-CLOSED on write error) → 500.
    Operator's UI sees the failure and can retry."""
    from genlab_core.scheduling import niche_pause as np_mod

    app = _make_app()
    with patch.object(np_mod, "pause", return_value=False):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/scheduling/pauses",
                json={"niche_id": "gaming", "reason": "test"},
            )
    assert r.status_code == 500


# ── DELETE /scheduling/pauses/<niche_id> ──────────────────────────


def test_delete_calls_unpause_helper():
    from genlab_core.scheduling import niche_pause as np_mod

    app = _make_app()
    with patch.object(np_mod, "unpause", return_value=True) as mock_unpause:
        with app.test_client() as c:
            r = c.delete("/api/v1/scheduling/pauses/gaming")
    assert r.status_code == 200
    assert r.get_json()["data"]["paused"] is False
    mock_unpause.assert_called_once_with("gaming")


def test_delete_403_on_cross_tenant_niche(monkeypatch):
    """SR-F: restricted operator can't unpause a niche outside their
    allowlist."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )

    app = _make_app()
    with app.test_client() as c:
        r = c.delete("/api/v1/scheduling/pauses/anime")
    assert r.status_code == 403


def test_delete_returns_500_when_unpause_helper_fails():
    """unpause() returning False → 500. Operator's UI sees + retries."""
    from genlab_core.scheduling import niche_pause as np_mod

    app = _make_app()
    with patch.object(np_mod, "unpause", return_value=False):
        with app.test_client() as c:
            r = c.delete("/api/v1/scheduling/pauses/gaming")
    assert r.status_code == 500
