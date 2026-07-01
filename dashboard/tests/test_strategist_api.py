"""Tests for /api/v1/strategist/* endpoints (PR Strategist-2 backend).

Uses mocked persister to avoid real DB dependency. Follows the same
auth-disabled + Flask test client pattern as test_api_smoke.py.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_report(niche_id="ai_creators"):
    """Build a StrategistReport for tests (lazy import so this file
    doesn't drag genlab-core into places that don't need it)."""
    from genlab_core.intelligence.proposal_schema import (
        StrategicPhase,
        StrategistReport,
    )

    return StrategistReport(
        niche_id=niche_id,
        week_of=date(2026, 6, 29),
        detected_phase=StrategicPhase.GROWTH,
        phase_evidence="Follower count crossed 100 on 2026-06-22; growth positive.",
        weekly_summary="ai_creators in early GROWTH. " * 4,
    )


class TestGetLatestReport:
    def test_400_on_invalid_niche(self, client):
        resp = client.get("/api/v1/strategist/reports/latest?niche_id=not_a_niche")
        assert resp.status_code == 400
        assert "Invalid niche_id" in resp.get_json()["message"]

    def test_400_on_missing_niche(self, client):
        resp = client.get("/api/v1/strategist/reports/latest")
        assert resp.status_code == 400

    def test_returns_none_when_no_reports(self, client):
        mock_persister = MagicMock()
        mock_persister.get_latest.return_value = None
        with patch(
            "server.api.strategist._get_persister",
            return_value=(mock_persister, MagicMock()),
        ):
            resp = client.get("/api/v1/strategist/reports/latest?niche_id=gaming")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"] is None

    def test_serializes_full_report(self, client):
        mock_persister = MagicMock()
        mock_persister.get_latest.return_value = _make_report(niche_id="ai_creators")
        with patch(
            "server.api.strategist._get_persister",
            return_value=(mock_persister, MagicMock()),
        ):
            resp = client.get("/api/v1/strategist/reports/latest?niche_id=ai_creators")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["niche_id"] == "ai_creators"
        assert body["data"]["detected_phase"] == "GROWTH"
        assert "id" in body["data"]
        assert body["data"]["proposals"] == []

    def test_503_when_persister_unavailable(self, client):
        with patch("server.api.strategist._get_persister", return_value=None):
            resp = client.get("/api/v1/strategist/reports/latest?niche_id=movies")
        assert resp.status_code == 503

    def test_500_on_persister_exception(self, client):
        mock_persister = MagicMock()
        mock_persister.get_latest.side_effect = RuntimeError("boom")
        with patch(
            "server.api.strategist._get_persister",
            return_value=(mock_persister, MagicMock()),
        ):
            resp = client.get("/api/v1/strategist/reports/latest?niche_id=anime")
        assert resp.status_code == 500


class TestListUnreviewed:
    def test_empty_when_no_reports(self, client):
        mock_persister = MagicMock()
        mock_persister.list_unreviewed.return_value = []
        with patch(
            "server.api.strategist._get_persister",
            return_value=(mock_persister, MagicMock()),
        ):
            resp = client.get("/api/v1/strategist/reports/unreviewed")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["count"] == 0
        assert body["data"]["reports"] == []

    def test_returns_reports(self, client):
        mock_persister = MagicMock()
        mock_persister.list_unreviewed.return_value = [
            _make_report(niche_id="ai_creators"),
            _make_report(niche_id="gaming"),
        ]
        with patch(
            "server.api.strategist._get_persister",
            return_value=(mock_persister, MagicMock()),
        ):
            resp = client.get("/api/v1/strategist/reports/unreviewed?limit=10")
        body = resp.get_json()
        assert body["data"]["count"] == 2
        niches = {r["niche_id"] for r in body["data"]["reports"]}
        assert niches == {"ai_creators", "gaming"}

    def test_limit_capped_to_100(self, client):
        mock_persister = MagicMock()
        mock_persister.list_unreviewed.return_value = []
        with patch(
            "server.api.strategist._get_persister",
            return_value=(mock_persister, MagicMock()),
        ):
            client.get("/api/v1/strategist/reports/unreviewed?limit=99999")
        # Verify persister was called with capped limit
        mock_persister.list_unreviewed.assert_called_once_with(limit=100)

    def test_invalid_limit_returns_400(self, client):
        resp = client.get("/api/v1/strategist/reports/unreviewed?limit=abc")
        assert resp.status_code == 400


class TestRecordReview:
    """POST tests need CSRF token — pass via _csrf_token in JSON body,
    the same shape config_routes tests use."""

    @staticmethod
    def _csrf(client) -> str:
        return client.get("/api/csrf-token").get_json().get("csrf_token", "")

    def test_400_on_non_list_indices(self, client):
        csrf = self._csrf(client)
        resp = client.post(
            "/api/v1/strategist/reports/some-uuid/review",
            json={"accepted_proposal_indices": "not-a-list", "_csrf_token": csrf},
        )
        assert resp.status_code == 400

    def test_400_on_non_list_rejected_indices(self, client):
        csrf = self._csrf(client)
        resp = client.post(
            "/api/v1/strategist/reports/some-uuid/review",
            json={
                "accepted_proposal_indices": [0],
                "rejected_proposal_indices": "not-a-list",
                "_csrf_token": csrf,
            },
        )
        assert resp.status_code == 400

    def test_success_updates_review_fields(self, client):
        csrf = self._csrf(client)
        report_id = str(uuid4())
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda self: mock_cursor
        mock_cursor.__exit__ = lambda *a: None
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg.connect", return_value=mock_conn):
            resp = client.post(
                f"/api/v1/strategist/reports/{report_id}/review",
                json={
                    "accepted_proposal_indices": [0, 2],
                    "rejected_proposal_indices": [1],
                    "notes": "phase shift makes sense; novelty rate too aggressive",
                    "_csrf_token": csrf,
                },
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["accepted_count"] == 2
        assert body["data"]["rejected_count"] == 1

    def test_404_when_report_not_found(self, client):
        csrf = self._csrf(client)
        report_id = str(uuid4())
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda self: mock_cursor
        mock_cursor.__exit__ = lambda *a: None
        mock_cursor.rowcount = 0  # UPDATE affected 0 rows = not found
        mock_conn.cursor.return_value = mock_cursor

        with patch("psycopg.connect", return_value=mock_conn):
            resp = client.post(
                f"/api/v1/strategist/reports/{report_id}/review",
                json={"accepted_proposal_indices": [], "_csrf_token": csrf},
            )
        assert resp.status_code == 404
