"""PR #542 (2026-06-24): SR-F wire — batch endpoints filter
cross-tenant IDs upfront.

Companion to PRs #540 (single-record list/action wire) and #541 (3
more single-record mutation endpoints). Batch endpoints process a
list of IDs; the natural shape for partial-success batches is to
SKIP cross-tenant rows with a clear denied marker rather than
reject the whole batch (one bad ID would otherwise block 49 valid
ones).

This PR ships:
  * _filter_batch_ids_by_allowlist(ids) helper returning
    (allowed_ids, denied_results)
  * Both batch endpoints (POST /batch-review + POST
    /batch-approve-schedule) call it upfront and pre-populate
    results with the denied rows

If these pins regress, batch endpoints silently re-open the
cross-tenant write hole — operators clicking "approve N selected"
on a multi-niche selection would still process every row regardless
of allowlist.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ── source pins ─────────────────────────────────────────────────────


def test_batch_filter_helper_exists():
    """The new helper must be importable + return a (allowed, denied)
    tuple. Without this, batch wire-up has nothing to call."""
    from server.api.blueprints import _filter_batch_ids_by_allowlist

    assert callable(_filter_batch_ids_by_allowlist)


def test_pr_542_anchor_present():
    """Future archaeologist anchor."""
    import server.api.blueprints as mod

    src = Path(mod.__file__).read_text()
    assert "PR #542" in src


def test_both_batch_endpoints_call_helper():
    """Both batch-review AND batch-approve-schedule must call the
    new filter helper. Pin both occurrences so a refactor that drops
    one re-opens the cross-tenant batch hole for that path."""
    import server.api.blueprints as mod

    src = Path(mod.__file__).read_text()
    occurrences = src.count("_filter_batch_ids_by_allowlist(ids)")
    assert occurrences >= 2, (
        f"Expected ≥2 _filter_batch_ids_by_allowlist calls (one per "
        f"batch endpoint); found {occurrences}."
    )


# ── helper unit tests ──────────────────────────────────────────────


def _scope_user_to_gaming(monkeypatch):
    """Apply the dotenv-override workaround from earlier SR-F tests."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "gaming_op")
    monkeypatch.setenv("REVIEW_NICHE_ALLOWLIST_GAMING_OP", "gaming")
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: "gaming_op",
    )


def test_filter_returns_all_when_unrestricted(monkeypatch):
    """No allowlist → every ID allowed, no fetches, no denials.
    Fast path mirrors single-record guard's None-allowlist behavior."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_client = MagicMock()  # should NEVER be called on fast path
    with patch("server.api.blueprints._get_client", return_value=fake_client):
        from server.api.blueprints import _filter_batch_ids_by_allowlist

        allowed, denied = _filter_batch_ids_by_allowlist(["a", "b", "c"])

    assert allowed == ["a", "b", "c"]
    assert denied == []
    # Fast path: no client fetch should have happened
    fake_client.blueprints.get.assert_not_called()


def test_filter_splits_allowed_vs_denied(monkeypatch):
    """Heterogeneous batch: gaming + anime + sports rows for a
    gaming-only operator → only gaming is allowed; anime + sports
    each land in denied_results with a clear SR-F message."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.side_effect = lambda rid: {
        "g1": {"id": "g1", "niche_id": "gaming"},
        "a1": {"id": "a1", "niche_id": "anime"},
        "s1": {"id": "s1", "niche_id": "sports"},
    }[rid]

    with patch("server.api.blueprints._get_client", return_value=fake_client):
        from server.api.blueprints import _filter_batch_ids_by_allowlist

        allowed, denied = _filter_batch_ids_by_allowlist(["g1", "a1", "s1"])

    assert allowed == ["g1"]
    assert len(denied) == 2
    # Per-row error shape matches the existing batch loops' error rows
    for d in denied:
        assert d["status"] == "denied"
        assert "SR-F" in d["error"] or "PR #542" in d["error"]
        assert "gaming" in d["error"]  # the allowlist shown to operator


def test_filter_handles_missing_blueprint_by_passing_through(monkeypatch):
    """When the backlog returns None for an ID, the filter passes it
    through to the loop — the existing per-row error handling
    surfaces the 'not found' to the operator. We must NOT silently
    deny a missing blueprint (would leak existence info)."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.side_effect = lambda rid: (
        {"id": "g1", "niche_id": "gaming"} if rid == "g1" else None
    )

    with patch("server.api.blueprints._get_client", return_value=fake_client):
        from server.api.blueprints import _filter_batch_ids_by_allowlist

        allowed, denied = _filter_batch_ids_by_allowlist(["g1", "missing"])

    # Both pass through — guard doesn't 404 a missing blueprint
    assert "g1" in allowed
    assert "missing" in allowed
    assert denied == []


def test_filter_handles_per_row_fetch_exception_by_passing_through(monkeypatch):
    """A backend error on ONE row shouldn't deny that row — the
    batch loop's error handling is the right place to surface it.
    Same defensive-pass-through pattern as missing-blueprint."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()

    def _side(rid):
        if rid == "boom":
            raise RuntimeError("DB timeout")
        return {"id": rid, "niche_id": "gaming"}

    fake_client.blueprints.get.side_effect = _side

    with patch("server.api.blueprints._get_client", return_value=fake_client):
        from server.api.blueprints import _filter_batch_ids_by_allowlist

        allowed, denied = _filter_batch_ids_by_allowlist(["g1", "boom"])

    assert "g1" in allowed
    assert "boom" in allowed
    assert denied == []


# ── behavioral pins via the batch endpoints ────────────────────────


def _make_app_with_routes():
    from flask import Flask
    from server.api.blueprints import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def test_batch_review_denies_cross_tenant_rows(monkeypatch):
    """End-to-end: gaming operator approves a 3-ID batch [g1, a1, s1]
    → response includes denied rows for a1 + s1 and processes g1."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.side_effect = lambda rid: {
        "g1": {"id": "g1", "niche_id": "gaming"},
        "a1": {"id": "a1", "niche_id": "anime"},
        "s1": {"id": "s1", "niche_id": "sports"},
    }[rid]
    fake_client.blueprints.update.return_value = None

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.core.calibration_helper.log_calibration_for_action"),
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/batch-review",
                json={"ids": ["g1", "a1", "s1"], "action": "approve"},
            )

    assert r.status_code == 200
    body = r.get_json()
    # Response is wrapped in {"status": "success", "data": [...]}
    # but the batch-review response uses {"data": {"data": [...]}}
    payload = body.get("data") if isinstance(body.get("data"), list) else body["data"]["data"]
    statuses = {row.get("id"): row.get("status") for row in payload if isinstance(row, dict)}
    assert statuses.get("a1") == "denied"
    assert statuses.get("s1") == "denied"
    # g1 should have gone through normal approval (status="ok" or similar)
    assert statuses.get("g1") != "denied"


def test_batch_approve_schedule_denies_cross_tenant_rows(monkeypatch):
    """Same shape for batch-approve-schedule — denied rows pre-populate
    the results so the operator sees a clear partial-success picture."""
    _scope_user_to_gaming(monkeypatch)

    fake_client = MagicMock()
    fake_client.blueprints.get.side_effect = lambda rid: {
        "g1": {"id": "g1", "niche_id": "gaming", "fields": {"niche_id": "gaming"}},
        "a1": {"id": "a1", "niche_id": "anime", "fields": {"niche_id": "anime"}},
    }[rid]
    fake_client.blueprints.update.return_value = None

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.core.publishing_queue._next_available_slot", return_value=None),
        patch("server.core.publishing_queue._advisory_lock"),
        patch("server.core.calibration_helper.log_calibration_for_action"),
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/batch-approve-schedule",
                json={"ids": ["g1", "a1"]},
            )

    assert r.status_code == 200
    body = r.get_json()
    payload = body.get("data") if isinstance(body.get("data"), list) else body["data"]["data"]
    statuses = {row.get("id"): row.get("status") for row in payload if isinstance(row, dict)}
    assert statuses.get("a1") == "denied"


def test_batch_review_unrestricted_processes_all_rows(monkeypatch):
    """Backward compat: admin operator's batch is unchanged."""
    monkeypatch.setenv("REVIEW_AUTH_USER", "admin")
    monkeypatch.delenv("REVIEW_NICHE_ALLOWLIST_ADMIN", raising=False)

    fake_client = MagicMock()
    fake_client.blueprints.get.return_value = {"id": "x", "niche_id": "any"}
    fake_client.blueprints.update.return_value = None

    app = _make_app_with_routes()
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch("server.core.calibration_helper.log_calibration_for_action"),
    ):
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/batch-review",
                json={"ids": ["a", "b", "c"], "action": "reject"},
            )

    assert r.status_code == 200
    body = r.get_json()
    payload = body.get("data") if isinstance(body.get("data"), list) else body["data"]["data"]
    # No denied rows when operator is unrestricted
    statuses = {row.get("id"): row.get("status") for row in payload if isinstance(row, dict)}
    assert not any(s == "denied" for s in statuses.values())
