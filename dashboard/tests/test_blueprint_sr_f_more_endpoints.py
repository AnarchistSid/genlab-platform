"""PR #541 (2026-06-24): SR-F wire — 3 more single-record blueprint
mutation endpoints adopt _enforce_blueprint_niche_allowlist.

Follow-up to PR #540 (which wired /review-queue + POST /review +
PATCH /review-action). This PR adopts the SAME helper on the
remaining single-record mutation endpoints:

  * POST /<id>/approve-and-schedule
  * PATCH /<id>/schedule
  * PATCH /<id>/content

After this PR, every single-record blueprint MUTATION goes through
the tenant guard. Batch endpoints (POST /batch-review, POST
/batch-approve-schedule) still need a different per-record guard
shape and are deferred to PR #542.

If these pins regress, a tenant-B operator could mutate tenant-A
blueprints via these endpoints by knowing the id — the same SR-F
bug class PR #540 closed for the primary review actions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ── source pin — exactly 5 guard call sites in the blueprints module ─


def test_guard_adoption_count_at_5():
    """After PR #541 there are 5 single-record mutation endpoints
    using the guard:
      * POST   /<id>/review                (#540)
      * PATCH  /<id>/review-action         (#540)
      * POST   /<id>/approve-and-schedule  (#541)
      * PATCH  /<id>/schedule              (#541)
      * PATCH  /<id>/content               (#541)

    Pin the count at ≥5 so a future refactor that removes one
    silently re-opens the cross-tenant mutation hole for that
    endpoint. Adding a 6th endpoint is fine (count > 5 passes)."""
    import server.api.blueprints as mod

    src = Path(mod.__file__).read_text()
    # The exact guard-call pattern from PR #540's helper. Note: matches
    # both record_id and blueprint_id parameter names — the
    # approve_and_schedule handler uses blueprint_id.
    count = src.count("_enforce_blueprint_niche_allowlist(record_id)")
    count += src.count("_enforce_blueprint_niche_allowlist(blueprint_id)")
    assert count >= 5, (
        f"Expected ≥5 _enforce_blueprint_niche_allowlist call sites "
        f"across single-record mutation endpoints; found {count}. "
        f"Lost guard means a tenant-B operator can mutate "
        f"tenant-A blueprints via that endpoint."
    )


def test_pr_541_anchor_present():
    """Future archaeologist anchor — grep PR #541 lands at the
    adoption comments."""
    import server.api.blueprints as mod

    src = Path(mod.__file__).read_text()
    assert "PR #541" in src


# ── behavioral pins (via stubbed backlog client) ───────────────────


def _make_app_with_routes():
    """Minimal Flask app with just the blueprints API mounted."""
    from flask import Flask
    from server.api.blueprints import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _scope_user_to_gaming(monkeypatch):
    """Apply the dotenv-override workaround from PR #540's tests.
    Same shape: env vars + monkeypatch on _username_for_session.
    monkeypatch.setenv (not direct os.environ) is required for
    test isolation across the dashboard suite."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def test_approve_and_schedule_403_on_cross_tenant(monkeypatch):
    """Gaming-scoped operator tries to approve-and-schedule an anime
    blueprint → 403. The underlying backlog update MUST NOT fire."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "anime"}

    app = _make_app_with_routes()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.post("/api/v1/blueprints/bp1/approve-and-schedule", json={})
    assert r.status_code == 403
    # Update never fired — that's the guard's job
    fake_client.blueprints.update.assert_not_called()


def test_schedule_patch_403_on_cross_tenant(monkeypatch):
    """Same guard on PATCH /<id>/schedule — reschedule is a mutation
    that needs the same tenant scoping."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "sports"}

    app = _make_app_with_routes()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.patch(
                "/api/v1/blueprints/bp1/schedule",
                json={"scheduled_for": "2026-07-01T06:30:00Z"},
            )
    assert r.status_code == 403
    fake_client.blueprints.update.assert_not_called()


def test_content_patch_403_on_cross_tenant(monkeypatch):
    """Same guard on PATCH /<id>/content — edit-the-hook flow needs
    tenant scoping too."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "bp1", "niche_id": "movies"}

    app = _make_app_with_routes()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            r = c.patch(
                "/api/v1/blueprints/bp1/content",
                json={"hook_override": "new hook"},
            )
    assert r.status_code == 403
    fake_client.blueprints.update.assert_not_called()


def test_approve_and_schedule_proceeds_for_same_tenant(monkeypatch):
    """Gaming operator on a gaming blueprint → guard is a no-op,
    action proceeds. Pins backward compat — same-tenant path
    works exactly as before."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {
        "id": "bp1",
        "niche_id": "gaming",
        "fields": {"niche_id": "gaming"},
    }
    fake_client.blueprints.update.return_value = None

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        # Stub out the scheduling helper so we don't need a real DB
        patch("server.core.publishing_queue._next_available_slot", return_value=None),
        # Stub calibration helper too
        patch("server.core.calibration_helper.log_calibration_for_action"),
    ):
        with app.test_client() as c:
            r = c.post("/api/v1/blueprints/bp1/approve-and-schedule", json={})

    # NOT 403 — guard passed → action ran
    assert r.status_code != 403


def test_unrestricted_user_skips_all_3_endpoint_guards(monkeypatch):
    """Default admin (no allowlist env) → all 3 endpoints proceed
    without the guard fetching a blueprint. Fast-path pin for the
    common-case operator who hasn't opted into per-tenant scoping."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_client = MagicMock()
    # No need to set return_value — unrestricted guard never fetches

    app = _make_app_with_routes()
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        with app.test_client() as c:
            # All 3 endpoints — each should pass the guard's fast path
            r1 = c.post("/api/v1/blueprints/bp1/approve-and-schedule", json={})
            r2 = c.patch(
                "/api/v1/blueprints/bp1/schedule",
                json={"scheduled_for": "2026-07-01T06:30:00Z"},
            )
            r3 = c.patch("/api/v1/blueprints/bp1/content", json={"hook_override": "x"})
    # None are 403 (guard didn't reject; downstream may still error
    # on missing fixtures, but that's outside the guard's scope)
    for r in (r1, r2, r3):
        assert r.status_code != 403
