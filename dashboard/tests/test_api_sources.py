"""Pins for /api/v1/sources/performance.

Surfaces per-source claim-rate so the operator can prune dead sources.
22 of 30 top sources have 0% claim rate (per 2026-06-13 deep dive);
this endpoint makes that visible.

Key invariants:
  1. Input validation: bad `days`/`min_fetched` → 400 (never crash)
  2. Missing DATABASE_URL → 503 (clear failure, not silent zero data)
  3. Output shape matches the dashboard's TypeScript interface exactly
  4. health classifier maps correctly across all 4 buckets
  5. Bucket counts in the headline match the row classifications
  6. Total aggregates computed in SQL, not from the returned rows
     (large `content_pool` tables would make the row sum wrong)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.api.sources import _classify_health
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestHealthClassifier:
    """Unit-test the bucket assignment — the operator's at-a-glance
    decision pivots on these labels."""

    def test_active_at_threshold(self):
        assert _classify_health(100, 5.0) == "active"

    def test_active_above_threshold(self):
        assert _classify_health(100, 50.0) == "active"

    def test_weak_just_above_dead(self):
        assert _classify_health(100, 1.0) == "weak"

    def test_weak_below_active(self):
        assert _classify_health(100, 4.9) == "weak"

    def test_dead_when_volume_and_below_one_pct(self):
        assert _classify_health(50, 0.0) == "dead"

    def test_dead_at_min_fetch_threshold(self):
        assert _classify_health(10, 0.5) == "dead"

    def test_unproven_below_min_fetch(self):
        """A brand-new source shouldn't be shamed for low fetch count —
        wait until it has at least 10 fetches before calling it dead."""
        assert _classify_health(5, 0.0) == "unproven"
        assert _classify_health(9, 0.0) == "unproven"


class TestInputValidation:
    def test_default_days(self, client, monkeypatch):
        """No params → default 14 days, queries DB.

        Must set DATABASE_URL explicitly via monkeypatch — the
        endpoint short-circuits to 503 when the env var is empty,
        BEFORE psycopg.connect is invoked, so mocking psycopg alone
        is insufficient. Matches the setup used by other tests in
        this class (test_returns_zero_when_no_data,
        test_rejects_invalid_niche_id). Locally this test passes
        accidentally because settings.py load_dotenv populates
        DATABASE_URL from .env; CI doesn't have a .env so the test
        sees the env-var pop from dashboard/tests/conftest.py and
        the endpoint returns 503."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchall.return_value = []
            mock_cur.fetchone.return_value = (0, 0)
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            mock_connect.return_value.__enter__.return_value = mock_conn

            resp = client.get("/api/v1/sources/performance")
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["window_days"] == 14

    def test_days_non_integer_400(self, client):
        resp = client.get("/api/v1/sources/performance?days=abc")
        assert resp.status_code == 400

    def test_days_out_of_range_high(self, client):
        resp = client.get("/api/v1/sources/performance?days=91")
        assert resp.status_code == 400

    def test_days_out_of_range_low(self, client):
        resp = client.get("/api/v1/sources/performance?days=0")
        assert resp.status_code == 400

    def test_min_fetched_non_integer_400(self, client):
        resp = client.get("/api/v1/sources/performance?min_fetched=xyz")
        assert resp.status_code == 400

    def test_min_fetched_below_one_400(self, client):
        resp = client.get("/api/v1/sources/performance?min_fetched=0")
        assert resp.status_code == 400

    def test_missing_database_url_503(self, client, monkeypatch):
        """Clear failure when DB not configured — not a silent zero."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/sources/performance")
        assert resp.status_code == 503


class TestOutputShape:
    def test_full_response_shape(self, client, monkeypatch):
        """Pin the JSON contract — the React TypeScript interface
        expects exactly these field names + types."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            # Per-source rows: name, platform, fetched, claimed, distinct_niches, latest
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            mock_cur.fetchall.return_value = [
                ("r/gaming new", "reddit", 119, 9, 1, now),
                ("Push Square", "rss", 117, 0, 0, now),
                ("New Source", "rss", 8, 0, 0, now),  # unproven (<10 fetched)
            ]
            # Totals
            mock_cur.fetchone.return_value = (4691, 559)
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            mock_connect.return_value.__enter__.return_value = mock_conn

            resp = client.get("/api/v1/sources/performance?days=14")
            assert resp.status_code == 200
            data = resp.get_json()["data"]

            # Top-level shape
            assert data["window_days"] == 14
            assert data["total_fetched"] == 4691
            assert data["total_claimed"] == 559
            assert data["claim_rate"] == round(559 / 4691, 3)
            assert "bucket_counts" in data
            assert "sources" in data

            # Per-source shape
            sources = data["sources"]
            assert len(sources) == 3
            first = sources[0]
            assert set(first.keys()) >= {
                "source_name",
                "source_platform",
                "fetched",
                "claimed",
                "claim_pct",
                "distinct_niches",
                "latest_fetch",
                "health",
            }

            # Specific classifications
            by_name = {s["source_name"]: s for s in sources}
            assert by_name["r/gaming new"]["health"] == "active"  # 9/119 = 7.6%
            assert by_name["Push Square"]["health"] == "dead"  # 0/117
            assert by_name["New Source"]["health"] == "unproven"  # <10 fetched

            # Bucket counts match the row classifications
            counts = data["bucket_counts"]
            assert counts["active"] == 1
            assert counts["dead"] == 1
            assert counts["unproven"] == 1
            assert counts["weak"] == 0

    def test_aggregates_from_sql_not_rows(self, client, monkeypatch):
        """The totals must come from the SQL aggregate query (covers all
        rows), not summing the truncated 200-row table — otherwise a
        large content_pool would report wrong totals."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        with patch("psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            # Per-source rows are limited; total query returns the
            # FULL aggregate (much larger than the row sum)
            mock_cur.fetchall.return_value = [
                ("Top Source", "rss", 100, 10, 1, None),
            ]
            mock_cur.fetchone.return_value = (40_000, 4800)  # full table
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            mock_connect.return_value.__enter__.return_value = mock_conn

            resp = client.get("/api/v1/sources/performance")
            data = resp.get_json()["data"]
            # The SQL-aggregate numbers, NOT the row sum
            assert data["total_fetched"] == 40_000
            assert data["total_claimed"] == 4800
            assert data["claim_rate"] == round(4800 / 40_000, 3)
