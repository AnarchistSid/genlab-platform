"""PR #545 (2026-06-24): SR-F wire pass 6 — engagement.py pending-reply
approve/reject endpoints adopt a per-tenant guard.

Companion to PRs #539-#544 (foundation + 5 wire passes for blueprint
mutations). This PR extends SR-F coverage to a DIFFERENT resource
type — pending engagement replies — using the same per-user
allowlist resolver but a different fetch path (replies live in a
JSON file, not the blueprints table).

The helper ``_reject_if_reply_outside_allowlist`` mirrors the shape
of ``server.api.blueprints._enforce_blueprint_niche_allowlist`` so
operators don't have to learn two patterns. Reuses the same
``get_allowed_niches()`` resolver from PR #539's foundation.

If these pins regress, a tenant-B operator could approve or reject
a tenant-A reply by knowing the reply's UUID — same SR-F bug class
the blueprint endpoints closed, just on a different resource.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ── source pins ─────────────────────────────────────────────────────


def test_reply_guard_helper_present():
    """The per-reply tenant guard helper must exist + use the same
    get_allowed_niches() entry point as the blueprint guards."""
    import server.api.engagement as mod

    src = Path(mod.__file__).read_text()
    assert "def _reject_if_reply_outside_allowlist(reply: dict)" in src
    # Reuses the foundation resolver — not a duplicated env-var lookup
    assert "from server.auth.niche_allowlist import get_allowed_niches" in src


def test_both_reply_endpoints_call_guard():
    """approve_reply + reject_reply must BOTH call the guard.
    Pin ≥2 occurrences so dropping the guard from one endpoint
    silently re-opens that side."""
    import server.api.engagement as mod

    src = Path(mod.__file__).read_text()
    count = src.count("_reject_if_reply_outside_allowlist(reply)")
    assert count >= 2, (
        f"Expected ≥2 _reject_if_reply_outside_allowlist calls (approve + reject); found {count}."
    )


def test_pr_545_anchor_present():
    """Future archaeologist anchor."""
    import server.api.engagement as mod

    src = Path(mod.__file__).read_text()
    assert "PR #545" in src


# ── behavioral pins (via Flask test client) ────────────────────────


def _make_app():
    from flask import Flask
    from server.api.engagement import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _scope_user_to_gaming(monkeypatch):
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def test_approve_reply_403_on_cross_tenant(monkeypatch):
    """Gaming-scoped operator approves an anime reply → 403 before
    any platform dispatch or text mutation."""
    _scope_user_to_gaming(monkeypatch)

    fake_replies = [
        {
            "id": "rep-1",
            "niche_id": "anime",
            "status": "pending",
            "reply_text": "original text",
        }
    ]

    app = _make_app()
    with (
        patch("server.api.engagement._load_pending_replies", return_value=fake_replies),
        patch("server.api.engagement._save_pending_replies") as mock_save,
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/engagement/pending-replies/rep-1/approve",
                json={"reply_text": "edited"},
            )
    assert r.status_code == 403
    # No save — guard short-circuited before any mutation
    mock_save.assert_not_called()
    # Reply text MUST NOT have been mutated by the edit attempt
    assert fake_replies[0]["reply_text"] == "original text"


def test_reject_reply_403_on_cross_tenant(monkeypatch):
    """Same guard on reject_reply — preventing the operator from
    closing another tenant's reply."""
    _scope_user_to_gaming(monkeypatch)

    fake_replies = [{"id": "rep-2", "niche_id": "sports", "status": "pending"}]

    app = _make_app()
    with (
        patch("server.api.engagement._load_pending_replies", return_value=fake_replies),
        patch("server.api.engagement._save_pending_replies") as mock_save,
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/engagement/pending-replies/rep-2/reject",
                json={"reason": "wrong tone"},
            )
    assert r.status_code == 403
    mock_save.assert_not_called()
    # Reply status unchanged
    assert fake_replies[0]["status"] == "pending"


def test_unrestricted_user_can_act_on_any_reply(monkeypatch):
    """Backward compat: admin operator with no allowlist can
    reject any reply regardless of niche."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_replies = [{"id": "rep-3", "niche_id": "anime", "status": "pending"}]

    app = _make_app()
    with (
        patch("server.api.engagement._load_pending_replies", return_value=fake_replies),
        patch("server.api.engagement._save_pending_replies"),
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/engagement/pending-replies/rep-3/reject",
                json={"reason": "test"},
            )
    # NOT 403 — admin proceeds (200 success expected)
    assert r.status_code == 200
    assert fake_replies[0]["status"] == "rejected"


def test_reply_with_missing_niche_id_is_blocked_when_restricted(monkeypatch):
    """Defensive: a reply without a niche_id (data corruption /
    legacy row) is BLOCKED when the operator is restricted —
    fail-secure rather than fail-open. Unrestricted users still
    proceed (the missing-niche reply is a separate operational
    issue, not a tenancy decision)."""
    _scope_user_to_gaming(monkeypatch)

    fake_replies = [
        {"id": "rep-no-niche", "status": "pending"}  # no niche_id
    ]

    app = _make_app()
    with (
        patch("server.api.engagement._load_pending_replies", return_value=fake_replies),
        patch("server.api.engagement._save_pending_replies"),
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/engagement/pending-replies/rep-no-niche/reject",
                json={"reason": "test"},
            )
    # Restricted user + missing niche_id → blocked (fail-secure)
    assert r.status_code == 403


def test_approve_reply_proceeds_for_same_tenant(monkeypatch):
    """Gaming operator on a gaming reply → guard passes, normal
    approve flow runs. Save IS called and reply moves to 'replied'
    or platform-dispatch state."""
    _scope_user_to_gaming(monkeypatch)

    fake_replies = [
        {
            "id": "rep-ok",
            "niche_id": "gaming",
            "status": "pending",
            "reply_text": "original",
            "platform": "instagram",
            "comment_id": "ig123",
            "post_id": "post123",
        }
    ]

    app = _make_app()
    # Stub the helpers the handler reaches AFTER the guard so the
    # test only exercises the guard's pass-through behavior. The
    # platform dispatch happens via a per-platform client.post_reply
    # call inside the handler — we don't stub it because the request
    # will fall through to the platform-not-configured graceful
    # fall-back path, which still proves the guard did not 403.
    with (
        patch("server.api.engagement._load_pending_replies", return_value=fake_replies),
        patch("server.api.engagement._save_pending_replies"),
        patch(
            "server.api.engagement._recheck_outbound_toxicity",
            return_value={"toxic": False, "score": 0.05},
        ),
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/engagement/pending-replies/rep-ok/approve",
                json={},
            )
    # NOT 403 — guard passed. The exact status may vary (200 on
    # successful dispatch, 5xx if the platform helper errors). All
    # we're pinning is "the guard didn't reject"; the wider approve
    # flow has its own tests.
    assert r.status_code != 403
