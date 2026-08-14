"""Pin Phase 5.C session 2 flag-flip-proposals endpoint:

  * Cold-start (no DSN) returns {"data": null}
  * DB error returns null (fail-open)
  * Rows include age_hours + hours_until_auto_apply + auto_apply_eligible
  * Not-eligible rows report future hours_until_auto_apply
  * Eligible rows have hours_until_auto_apply == 0
  * Reject flips status='pending' → 'rejected'
  * Reject returns 404 on unknown id
  * Reject returns 500 without DSN
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from server.api.flag_flip_proposals import bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


class TestPendingColdStart:
    def test_no_dsn_returns_null(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/flag-flip-proposals/pending")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_db_error_returns_null(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable:1/x")
        resp = client.get("/api/v1/flag-flip-proposals/pending")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None


class TestPendingShape:
    def _mock_rows(self, mock_connect, rows):
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        conn_ctx.execute.return_value.fetchall.return_value = rows
        mock_connect.return_value = conn_ctx

    @patch("psycopg.connect")
    def test_row_carries_computed_fields(
        self, mock_connect, client, monkeypatch,
    ):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        # Row is 6h old → not eligible yet.
        row = {
            "id": "abc",
            "flag_name": "GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT",
            "from_state": "25", "to_state": "50",
            "rationale": "lift 30%",
            "confidence": 0.95,
            "age_hours": 6.2,
            "proposed_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
            "evidence": {"lift_pct": 30},
        }
        self._mock_rows(mock_connect, [row])
        body = client.get(
            "/api/v1/flag-flip-proposals/pending",
        ).get_json()
        assert body["data"] is not None
        assert body["data"]["override_window_hours"] == 24
        assert body["data"]["confidence_threshold"] == 0.9
        r = body["data"]["rows"][0]
        assert r["auto_apply_eligible"] is False
        # 24 - 6.2 = 17.8h
        assert abs(r["hours_until_auto_apply"] - 17.8) < 0.01
        assert r["age_hours"] == 6.2

    @patch("psycopg.connect")
    def test_eligible_when_age_and_conf_pass(
        self, mock_connect, client, monkeypatch,
    ):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        row = {
            "id": "abc",
            "flag_name": "GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT",
            "from_state": "25", "to_state": "50",
            "rationale": "aged",
            "confidence": 0.95,
            "age_hours": 30.0,  # > 24h
            "proposed_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
            "evidence": {},
        }
        self._mock_rows(mock_connect, [row])
        body = client.get(
            "/api/v1/flag-flip-proposals/pending",
        ).get_json()
        r = body["data"]["rows"][0]
        assert r["auto_apply_eligible"] is True
        assert r["hours_until_auto_apply"] == 0.0

    @patch("psycopg.connect")
    def test_low_conf_not_eligible_even_when_aged(
        self, mock_connect, client, monkeypatch,
    ):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        row = {
            "id": "abc", "flag_name": "F",
            "from_state": "25", "to_state": "50",
            "rationale": "aged but weak",
            "confidence": 0.5,  # below 0.9 threshold
            "age_hours": 100.0,
            "proposed_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "evidence": {},
        }
        self._mock_rows(mock_connect, [row])
        body = client.get(
            "/api/v1/flag-flip-proposals/pending",
        ).get_json()
        assert body["data"]["rows"][0]["auto_apply_eligible"] is False

    @patch("psycopg.connect")
    def test_empty_rows_ok(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        self._mock_rows(mock_connect, [])
        body = client.get(
            "/api/v1/flag-flip-proposals/pending",
        ).get_json()
        assert body["data"]["rows"] == []


class TestReject:
    def test_missing_dsn_returns_500(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.post(
            "/api/v1/flag-flip-proposals/abc/reject",
            json={"reason": "wrong"},
        )
        assert resp.status_code == 500

    @patch("psycopg.connect")
    def test_success(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        result = MagicMock()
        result.rowcount = 1
        conn_ctx.execute.return_value = result
        mock_connect.return_value = conn_ctx
        resp = client.post(
            "/api/v1/flag-flip-proposals/abc/reject",
            json={"reason": "wrong direction"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "success"

    @patch("psycopg.connect")
    def test_unknown_id_returns_404(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        result = MagicMock()
        result.rowcount = 0
        conn_ctx.execute.return_value = result
        mock_connect.return_value = conn_ctx
        resp = client.post(
            "/api/v1/flag-flip-proposals/abc/reject",
            json={"reason": "wrong"},
        )
        assert resp.status_code == 404
