"""PR #544 (2026-06-24): SR-F wire pass 5 — schedule.py /reorder endpoint.

Final remaining single-record blueprint-mutation surface in the
dashboard adopts the tenant guard. After this PR, EVERY blueprint
mutation across blueprints.py, publishing_queue.py, AND schedule.py
goes through SR-F.

If this pin regresses, a tenant-B operator with a blueprint id can
still drag-and-drop reschedule a tenant-A blueprint via the
schedule reorder API — a write-side cross-tenant bug PR #540's
foundation was designed to close.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_reorder_imports_guard_from_blueprints():
    """Source pin: /reorder must call the SR-F guard helper from
    blueprints.py, not re-implement the check locally."""
    import server.api.schedule as mod

    src = Path(mod.__file__).read_text()
    assert "from server.api.blueprints import _enforce_blueprint_niche_allowlist" in src
    assert "_enforce_blueprint_niche_allowlist(str(blueprint_id))" in src
    assert "PR #544" in src


def _scope_user_to_gaming(monkeypatch):
    """dotenv-override workaround established in earlier SR-F tests."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def _make_app():
    """Schedule + blueprints blueprints mounted (guard is in blueprints.py)."""
    from flask import Flask
    from server.api.blueprints import bp as bp_blueprints
    from server.api.schedule import bp as bp_schedule

    app = Flask(__name__)
    app.register_blueprint(bp_schedule)
    app.register_blueprint(bp_blueprints)
    return app


def test_reorder_403_on_cross_tenant(monkeypatch):
    """Gaming-scoped operator drags an anime blueprint into a new
    slot via the reorder API → 403 before any update fires."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "anime"}

    app = _make_app()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.patch(
                "/api/v1/schedule/reorder",
                json={"blueprint_id": "bp1", "to_slot": "06:30 IST"},
            )
    assert r.status_code == 403
    # Update never fired — the guard short-circuited
    fake_client.blueprints.update.assert_not_called()


def test_reorder_proceeds_for_unrestricted_user(monkeypatch):
    """Backward compat: admin operator with no allowlist can
    reorder any blueprint."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_client = MagicMock()
    # Guard fast-paths without fetching when allowlist is None;
    # the rest of the handler then runs and would try its own
    # backlog interactions. Return shape that satisfies the
    # downstream slot-validation path.
    fake_client.blueprints.get.return_value = {
        "id": "bp1",
        "fields": {"niche_id": "anime"},
    }

    app = _make_app()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        # Stub out the slot loader + collision check to focus on the guard
        patch(
            "server.api.schedule._load_schedule_slots",
            return_value=["06:30 IST"],
        ),
        patch("server.api.schedule._check_slot_collision", return_value=None),
        patch(
            "server.api.schedule._get_client",
            return_value=fake_client,
        ),
    ):
        with app.test_client() as c:
            r = c.patch(
                "/api/v1/schedule/reorder",
                json={"blueprint_id": "bp1", "to_slot": "06:30 IST"},
            )
    # NOT 403 — guard passed → handler proceeded (may 200 or not
    # depending on downstream logic, but it's NOT the guard rejecting)
    assert r.status_code != 403
