"""Pins for /api/v1/costs/* — the operator's LLM-spend visibility API.

Closes the cost-tracking gap from the 2026-06-13 autonomy deep-dive
that #8 in the queue targeted. Before this, there was NO database
table tracking LLM spend; CostAccumulator dumped totals into
per-run JSON files.

What this file pins:
  1. Input validation: bad params → 400 (never 500 silently)
  2. Missing DATABASE_URL → 503 (clear failure, not silent zeros)
  3. /summary aggregates by niche over a rolling window
  4. /by-niche/<id> rejects unknown niches at the path-param boundary
  5. /by-niche groups by day OR week (whitelisted) — no SQL injection
     via the group_by param
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# ── /summary endpoint ────────────────────────────────────────────────────


class TestSummaryValidation:
    def test_default_days_returns_200(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect") as mock_connect:
            mock_cur = MagicMock()
            mock_cur.fetchall.return_value = []
            mock_cur.fetchone.return_value = (0,)
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            mock_conn.__enter__.return_value = mock_conn
            mock_connect.return_value = mock_conn

            resp = client.get("/api/v1/costs/summary")
            assert resp.status_code == 200
            assert resp.get_json()["data"]["window_days"] == 7

    def test_days_non_integer_400(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        resp = client.get("/api/v1/costs/summary?days=xyz")
        assert resp.status_code == 400

    def test_days_out_of_range_400(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        resp = client.get("/api/v1/costs/summary?days=366")
        assert resp.status_code == 400
        resp = client.get("/api/v1/costs/summary?days=0")
        assert resp.status_code == 400

    def test_missing_database_url_503(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/costs/summary")
        assert resp.status_code == 503


class TestSummaryShape:
    def test_aggregates_rows_per_niche(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect") as mock_connect:
            mock_cur = MagicMock()
            # 2 niche rows from the GROUP BY query
            mock_cur.fetchall.return_value = [
                ("gaming", 4.56, 3.20, 1.36, 0.0, 0.0, 0.0, 0.0, 7, 0.6514),
                ("ai_creators", 2.10, 1.80, 0.30, 0.0, 0.0, 0.0, 0.0, 5, 0.42),
            ]
            # Total query result (returns a single (sum,) tuple)
            mock_cur.fetchone.return_value = (6.66,)
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            mock_conn.__enter__.return_value = mock_conn
            mock_connect.return_value = mock_conn

            resp = client.get("/api/v1/costs/summary?days=7")
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["total_usd"] == 6.66
            assert len(data["rows"]) == 2
            gaming = next(r for r in data["rows"] if r["niche_id"] == "gaming")
            assert gaming["total_usd"] == 4.56
            assert gaming["llm_usd"] == 3.20
            assert gaming["run_count"] == 7
            assert gaming["avg_per_run_usd"] == 0.6514


# ── /by-niche/<id> endpoint ──────────────────────────────────────────────


class TestByNicheValidation:
    def test_unknown_niche_400(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        resp = client.get("/api/v1/costs/by-niche/politics")
        assert resp.status_code == 400

    def test_invalid_group_by_400(self, client, monkeypatch):
        """group_by interpolated into date_trunc() — defense-in-depth
        whitelist prevents SQL injection."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        resp = client.get("/api/v1/costs/by-niche/gaming?group_by=hour'; DROP TABLE x;--")
        assert resp.status_code == 400

    def test_invalid_days_400(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        resp = client.get("/api/v1/costs/by-niche/gaming?days=0")
        assert resp.status_code == 400
        resp = client.get("/api/v1/costs/by-niche/gaming?days=400")
        assert resp.status_code == 400

    @pytest.mark.parametrize("niche", ["ai_creators", "gaming", "sports", "movies", "anime"])
    def test_all_5_valid_niches_accepted(self, client, monkeypatch, niche):
        """Pin each of the 5 niches works at the path-param boundary —
        defense against typos when adding niches or routes."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect") as mock_connect:
            mock_cur = MagicMock()
            mock_cur.fetchall.return_value = []
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            mock_conn.__enter__.return_value = mock_conn
            mock_connect.return_value = mock_conn

            resp = client.get(f"/api/v1/costs/by-niche/{niche}")
            assert resp.status_code == 200


class TestByNicheShape:
    def test_returns_time_series(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect") as mock_connect:
            mock_cur = MagicMock()
            mock_cur.fetchall.return_value = [
                ("2026-06-13", 0.6234, 1),
                ("2026-06-12", 0.5012, 1),
            ]
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            mock_conn.__enter__.return_value = mock_conn
            mock_connect.return_value = mock_conn

            resp = client.get("/api/v1/costs/by-niche/gaming?days=30&group_by=day")
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["niche_id"] == "gaming"
            assert data["window_days"] == 30
            assert data["group_by"] == "day"
            assert len(data["series"]) == 2
            assert data["series"][0]["period"] == "2026-06-13"
            assert data["series"][0]["total_usd"] == 0.6234
            assert data["series"][0]["run_count"] == 1
