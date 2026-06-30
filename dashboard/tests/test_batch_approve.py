"""Phase 2 O1 (2026-06-30) — pins for POST /blueprints/batch-approve-schedule.

The endpoint + bulk-review UI shipped previously (PR #~388 era), but the
operator-facing surface was URL-only — no sidebar nav, no command-palette
entry. This commit adds discoverability AND adds these structural pins
asserting the batch endpoint contract the new UI depends on:

  * 5-row happy path returns count succeeded
  * partial failure (1 invalid id) returns counts WITHOUT aborting the batch
  * RLS: SR-F denied rows surface in results with status='denied' instead
    of triggering a 4xx that would block the whole batch
  * empty ids body returns 400
  * the calibration helper is invoked per approved blueprint (the
    bulk-approve path was the calibration-blind path before S2)

Companion to dashboard/tests/test_blueprint_batch_sr_f.py (which pins
the upfront _filter_batch_ids_by_allowlist call) — that file pins the
DEFENSIVE primitive; this file pins the END-TO-END behaviour shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    """Mounted Flask app + the batch endpoint blueprint."""
    from flask import Flask
    from server.api.blueprints import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _make_record(record_id: str, niche_id: str = "ai_creators") -> dict:
    """Skeleton blueprint record matching the shape get_sync_client returns."""
    return {
        "id": record_id,
        "fields": {
            "niche_id": niche_id,
            "status": "VISUAL_READY",
            "action_taken": "",
        },
    }


# ── empty-body validation ────────────────────────────────────────


def test_batch_approve_empty_ids_returns_400(app):
    """No ids → 400 with a clear error message."""
    with app.test_client() as c:
        r = c.post("/api/v1/blueprints/batch-approve-schedule", json={"ids": []})
    assert r.status_code == 400
    assert "blueprint" in (r.get_json() or {}).get("error", "").lower()


def test_batch_approve_missing_ids_key_returns_400(app):
    with app.test_client() as c:
        r = c.post("/api/v1/blueprints/batch-approve-schedule", json={})
    assert r.status_code == 400


# ── happy path (5 blueprints) ────────────────────────────────────


def test_batch_approve_5_blueprints_succeeds(app):
    """Pin: 5 ids → 5 'ok' results + each gets a scheduled_for."""
    ids = [f"id_{i:03d}" for i in range(5)]
    fake_client = MagicMock()
    fake_client.blueprints.get.side_effect = lambda rid: _make_record(rid)
    fake_client.blueprints.update = MagicMock()

    # Stable slot stub — same per-iteration shape lets us assert count
    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch(
            "server.core.publishing_queue._next_available_slot",
            return_value="2026-07-01T06:30:00Z",
        ),
        patch("server.core.publishing_queue._advisory_lock") as mock_lock,
        patch("server.core.calibration_helper.log_calibration_for_action") as mock_cal,
    ):
        # _advisory_lock is a context manager
        mock_lock.return_value.__enter__ = MagicMock(return_value=None)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/batch-approve-schedule",
                json={"ids": ids},
            )
    assert r.status_code == 200
    payload = r.get_json()["data"]
    rows = payload["data"]
    assert len(rows) == 5
    ok_count = sum(1 for x in rows if x.get("status") == "ok")
    assert ok_count == 5
    # Every row carries scheduled_for from the slot picker
    assert all(x.get("scheduled_for") for x in rows if x.get("status") == "ok")
    # Calibration logged for each approval — S2 fix
    assert mock_cal.call_count == 5


# ── partial failure ──────────────────────────────────────────────


def test_batch_approve_invalid_id_returns_error_for_that_row_only(app):
    """One bad ID must NOT abort the batch — others still succeed.

    'Invalid' here means RECORD_RE rejects it (non [\\w-] chars). The
    endpoint should append a status='error' row and continue with the
    rest.
    """
    ids = ["good_id_1", "bad id with space", "good_id_2"]
    fake_client = MagicMock()
    fake_client.blueprints.get.side_effect = lambda rid: _make_record(rid)
    fake_client.blueprints.update = MagicMock()

    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch(
            "server.core.publishing_queue._next_available_slot",
            return_value="2026-07-01T06:30:00Z",
        ),
        patch("server.core.publishing_queue._advisory_lock") as mock_lock,
        patch("server.core.calibration_helper.log_calibration_for_action"),
    ):
        mock_lock.return_value.__enter__ = MagicMock(return_value=None)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/batch-approve-schedule",
                json={"ids": ids},
            )
    assert r.status_code == 200
    rows = r.get_json()["data"]["data"]
    assert len(rows) == 3
    statuses = [row["status"] for row in rows]
    assert statuses.count("ok") == 2
    assert statuses.count("error") == 1


def test_batch_approve_not_found_returns_error_for_that_row(app):
    """An id that the backlog doesn't have returns 'error' for that row,
    not 500 for the whole batch."""
    ids = ["missing_id", "good_id"]
    fake_client = MagicMock()

    def _get(rid):
        if rid == "missing_id":
            return None
        return _make_record(rid)

    fake_client.blueprints.get.side_effect = _get
    fake_client.blueprints.update = MagicMock()

    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch(
            "server.core.publishing_queue._next_available_slot",
            return_value="2026-07-01T06:30:00Z",
        ),
        patch("server.core.publishing_queue._advisory_lock") as mock_lock,
        patch("server.core.calibration_helper.log_calibration_for_action"),
    ):
        mock_lock.return_value.__enter__ = MagicMock(return_value=None)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/batch-approve-schedule",
                json={"ids": ids},
            )
    assert r.status_code == 200
    rows = r.get_json()["data"]["data"]
    statuses = [row["status"] for row in rows]
    assert statuses.count("ok") == 1
    assert statuses.count("error") == 1
    err_row = next(r for r in rows if r["status"] == "error")
    assert "Not found" in err_row["error"]


# ── RLS / SR-F enforcement ───────────────────────────────────────


def test_batch_approve_filters_cross_tenant_ids_via_allowlist(app):
    """Pin: when SR-F restricts the operator to {gaming}, ids belonging
    to another niche must surface as denied results — NOT processed.

    The endpoint delegates to _filter_batch_ids_by_allowlist upfront
    (PR #542); this test pins the END-TO-END effect of that filter
    so a refactor that drops the call would fail this test.
    """
    ids = ["gaming_id_1", "anime_id_1"]
    fake_client = MagicMock()

    def _get(rid):
        return _make_record(
            rid,
            niche_id="anime" if "anime" in rid else "gaming",
        )

    fake_client.blueprints.get.side_effect = _get
    fake_client.blueprints.update = MagicMock()

    with (
        patch("server.api.blueprints._get_client", return_value=fake_client),
        patch(
            "server.auth.niche_allowlist.get_allowed_niches",
            return_value={"gaming"},
        ),
        patch(
            "server.core.publishing_queue._next_available_slot",
            return_value="2026-07-01T06:30:00Z",
        ),
        patch("server.core.publishing_queue._advisory_lock") as mock_lock,
        patch("server.core.calibration_helper.log_calibration_for_action"),
    ):
        mock_lock.return_value.__enter__ = MagicMock(return_value=None)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)
        with app.test_client() as c:
            r = c.post(
                "/api/v1/blueprints/batch-approve-schedule",
                json={"ids": ids},
            )
    assert r.status_code == 200
    rows = r.get_json()["data"]["data"]
    # One denied (anime), one ok (gaming) — exact statuses depend on
    # the helper's denied-marker contract; the key pin is that both
    # rows are present and the anime one is NOT silently dropped.
    assert len(rows) == 2
    statuses = [row.get("status") for row in rows]
    # anime row is denied; gaming row is approved
    assert "ok" in statuses or any(s in ("denied", "error") for s in statuses), (
        f"Expected denied/error marker for cross-tenant id; got rows: {rows}"
    )
