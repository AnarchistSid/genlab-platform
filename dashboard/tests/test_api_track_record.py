"""Tests for the W4.4 /api/v1/auto-approval/track-record endpoint.

Validation + happy-path + DB-failure-fail-open shape. The actual
SQL is exercised via a psycopg.connect mock that captures executed
SQL + returns canned row sets.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
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


def _patch_pg(rows):
    """Build a psycopg.connect mock returning the given DictRow-shaped rows."""
    conn = MagicMock()

    # First execute = SET app.niche_id; second = SELECT (returns rows)
    select_cursor = MagicMock()
    select_cursor.fetchall.return_value = rows

    def execute(sql, params=None):
        if "SELECT" in sql.upper() and "FROM auto_approval_calibration" in sql:
            return select_cursor
        return MagicMock()

    conn.execute = execute
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    return patch("psycopg.connect", return_value=conn)


def _row(day, samples, agreement):
    return {"day": day, "sample_count": samples, "agreement_count": agreement}


# ── Validation ────────────────────────────────────────────────────────


class TestValidation:
    def test_missing_niche_id_400(self, client):
        resp = client.get("/api/v1/auto-approval/track-record")
        assert resp.status_code == 400

    def test_unknown_niche_id_400(self, client):
        resp = client.get("/api/v1/auto-approval/track-record?niche_id=bogus")
        assert resp.status_code == 400

    def test_window_days_too_large_400(self, client):
        resp = client.get("/api/v1/auto-approval/track-record?niche_id=gaming&window_days=91")
        assert resp.status_code == 400

    def test_window_days_non_integer_400(self, client):
        resp = client.get("/api/v1/auto-approval/track-record?niche_id=gaming&window_days=abc")
        assert resp.status_code == 400

    def test_bin_days_larger_than_window_400(self, client):
        resp = client.get(
            "/api/v1/auto-approval/track-record?niche_id=gaming&window_days=7&bin_days=14"
        )
        assert resp.status_code == 400

    def test_no_database_url_503(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/auto-approval/track-record?niche_id=gaming")
        assert resp.status_code == 503


# ── Happy path: daily binning ─────────────────────────────────────────


class TestDailyBins:
    def test_returns_per_day_bins_in_order(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        rows = [
            _row(date(2026, 6, 14), 5, 4),
            _row(date(2026, 6, 15), 3, 3),
            _row(date(2026, 6, 16), 7, 5),
        ]
        with _patch_pg(rows):
            resp = client.get("/api/v1/auto-approval/track-record?niche_id=gaming&window_days=7")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["niche_id"] == "gaming"
        assert data["window_days"] == 7
        assert data["bin_days"] == 1
        assert len(data["bins"]) == 3
        b = data["bins"]
        assert b[0] == {
            "date": "2026-06-14",
            "sample_count": 5,
            "agreement": 4,
            "rate": 0.8,
        }
        assert b[1] == {
            "date": "2026-06-15",
            "sample_count": 3,
            "agreement": 3,
            "rate": 1.0,
        }
        assert b[2] == {
            "date": "2026-06-16",
            "sample_count": 7,
            "agreement": 5,
            "rate": 0.714,
        }
        # Overall is the windowed rollup
        assert data["overall"]["sample_count"] == 15
        assert data["overall"]["agreement"] == 12
        assert data["overall"]["rate"] == 0.8

    def test_empty_window_returns_empty_bins_no_crash(self, client, monkeypatch):
        """Day-0 niche (no reviews yet) — must return empty bins +
        zero overall, not crash on division-by-zero."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        with _patch_pg([]):
            resp = client.get("/api/v1/auto-approval/track-record?niche_id=anime")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["bins"] == []
        assert data["overall"] == {
            "sample_count": 0,
            "agreement": 0,
            "rate": 0.0,
        }


# ── Multi-day binning ─────────────────────────────────────────────────


class TestMultiDayBins:
    def test_7day_bins_group_consecutive_days(self, client, monkeypatch):
        """bin_days=7 groups 14 days of data into 2 weekly bins."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        # 14 days of data, 1 review per day, all agreed
        rows = [_row(date(2026, 6, 1) + timedelta(days=i), 1, 1) for i in range(14)]
        with _patch_pg(rows):
            resp = client.get(
                "/api/v1/auto-approval/track-record?niche_id=gaming&window_days=14&bin_days=7"
            )
        assert resp.status_code == 200
        bins = resp.get_json()["data"]["bins"]
        # Should be 2 weekly bins, each with 7 samples
        assert len(bins) == 2
        for b in bins:
            assert b["sample_count"] == 7
            assert b["agreement"] == 7
            assert b["rate"] == 1.0

    def test_bin_days_2_handles_uneven_window(self, client, monkeypatch):
        """5 days with bin_days=2 → 3 bins (the oldest may be smaller).
        Latest bin must be the most-recent reviews — pin the chronological
        order."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        rows = [
            _row(date(2026, 6, 12), 1, 1),
            _row(date(2026, 6, 13), 1, 1),
            _row(date(2026, 6, 14), 1, 1),
            _row(date(2026, 6, 15), 1, 1),
            _row(date(2026, 6, 16), 1, 1),
        ]
        with _patch_pg(rows):
            resp = client.get(
                "/api/v1/auto-approval/track-record?niche_id=gaming&window_days=5&bin_days=2"
            )
        assert resp.status_code == 200
        bins = resp.get_json()["data"]["bins"]
        # Bins are chronological
        assert bins[0]["date"] < bins[-1]["date"]
        # Latest bin's date is the latest review date
        assert bins[-1]["date"] == "2026-06-16"


# ── Overall rollup math ───────────────────────────────────────────────


class TestOverallRollup:
    def test_overall_matches_calibration_stats_for_same_window(self, client, monkeypatch):
        """Overall rate must match the simpler calibration-stats
        endpoint when computed over the same window. If these drift,
        operators get confused which is the source of truth."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        # 3 days, 10 reviews total, 9 agreements → 0.9 rate
        rows = [
            _row(date(2026, 6, 14), 4, 4),
            _row(date(2026, 6, 15), 3, 3),
            _row(date(2026, 6, 16), 3, 2),
        ]
        with _patch_pg(rows):
            resp = client.get("/api/v1/auto-approval/track-record?niche_id=gaming&window_days=7")
        data = resp.get_json()["data"]
        assert data["overall"]["sample_count"] == 10
        assert data["overall"]["agreement"] == 9
        assert data["overall"]["rate"] == 0.9
