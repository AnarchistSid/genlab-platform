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


def _patch_pg(rows, engagement_rows=None):
    """Build a psycopg.connect mock returning the given DictRow-shaped rows.

    W3 (2026-06-18) added a second SELECT (engagement join). Caller
    can pass ``engagement_rows`` for tests that want non-empty
    engagement data; defaults to ``[]`` so existing tests still pass
    (their bins get collected_count=0, avg_reward_48h=None added).
    """
    conn = MagicMock()

    calibration_cursor = MagicMock()
    calibration_cursor.fetchall.return_value = rows

    # W3 (2026-06-18): second SELECT joins pending_feedback for
    # engagement enrichment. Caller can pass engagement_rows via
    # `_patch_pg(rows, engagement_rows=...)` — default [] = no data.
    engagement_cursor = MagicMock()
    engagement_cursor.fetchall.return_value = engagement_rows or []

    def execute(sql, params=None):
        upper = sql.upper()
        # The engagement SELECT joins pending_feedback; the calibration
        # SELECT does not. Match by table presence (cleaner than
        # parameter-count or call-index heuristics).
        if "PENDING_FEEDBACK" in upper:
            return engagement_cursor
        if "FROM AUTO_APPROVAL_CALIBRATION" in upper:
            return calibration_cursor
        return MagicMock()

    conn.execute = execute
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    return patch("psycopg.connect", return_value=conn)


def _row(day, samples, agreement):
    return {"day": day, "sample_count": samples, "agreement_count": agreement}


def _engagement_row(day, collected_count, avg_reward_48h):
    """W3 (2026-06-18): build an engagement-shape row matching the
    second SELECT's column names (day, collected_count, avg_reward_48h)."""
    return {
        "day": day,
        "collected_count": collected_count,
        "avg_reward_48h": avg_reward_48h,
    }


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
        # W3 (2026-06-18): bins now carry engagement enrichment.
        # No engagement data in this test (engagement_rows default=[]) →
        # each bin gets collected_count=0, avg_reward_48h=None.
        assert b[0] == {
            "date": "2026-06-14",
            "sample_count": 5,
            "agreement": 4,
            "rate": 0.8,
            "collected_count": 0,
            "avg_reward_48h": None,
        }
        assert b[1] == {
            "date": "2026-06-15",
            "sample_count": 3,
            "agreement": 3,
            "rate": 1.0,
            "collected_count": 0,
            "avg_reward_48h": None,
        }
        assert b[2] == {
            "date": "2026-06-16",
            "sample_count": 7,
            "agreement": 5,
            "rate": 0.714,
            "collected_count": 0,
            "avg_reward_48h": None,
        }
        # Overall is the windowed rollup
        assert data["overall"]["sample_count"] == 15
        assert data["overall"]["agreement"] == 12
        assert data["overall"]["rate"] == 0.8
        assert data["overall"]["collected_count"] == 0
        assert data["overall"]["avg_reward_48h"] is None

    def test_empty_window_returns_empty_bins_no_crash(self, client, monkeypatch):
        """Day-0 niche (no reviews yet) — must return empty bins +
        zero overall, not crash on division-by-zero."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        with _patch_pg([]):
            resp = client.get("/api/v1/auto-approval/track-record?niche_id=anime")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["bins"] == []
        # W3 (2026-06-18): overall now carries engagement aggregates.
        # No bins → 0 collected, None avg_reward.
        assert data["overall"] == {
            "sample_count": 0,
            "agreement": 0,
            "rate": 0.0,
            "collected_count": 0,
            "avg_reward_48h": None,
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


# ── W3 engagement enrichment (2026-06-18) ──────────────────────


class TestW3EngagementEnrichment:
    """Each bin must carry collected_count + avg_reward_48h from the
    pending_feedback JOIN. Operator can pair calibration agreement
    with post-publish engagement signal on the same chart."""

    def test_bin_carries_engagement_fields(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        cal = [_row(date(2026, 6, 14), 5, 4)]
        eng = [_engagement_row(date(2026, 6, 14), collected_count=9, avg_reward_48h=0.123)]
        with _patch_pg(cal, engagement_rows=eng):
            resp = client.get("/api/v1/auto-approval/track-record?niche_id=sports&window_days=7")
        assert resp.status_code == 200
        bin0 = resp.get_json()["data"]["bins"][0]
        assert bin0["collected_count"] == 9
        assert bin0["avg_reward_48h"] == 0.123

    def test_day_without_engagement_returns_zero_and_null(self, client, monkeypatch):
        """Calibration row exists for a day but engagement query
        returns nothing for that day → bin gets collected_count=0,
        avg_reward_48h=None. Critical because the LEFT JOIN can
        produce this when posts haven't yet had their 48h window."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        cal = [_row(date(2026, 6, 14), 5, 4)]
        with _patch_pg(cal, engagement_rows=[]):
            resp = client.get("/api/v1/auto-approval/track-record?niche_id=sports&window_days=7")
        bin0 = resp.get_json()["data"]["bins"][0]
        assert bin0["collected_count"] == 0
        assert bin0["avg_reward_48h"] is None
        # Agreement fields preserved
        assert bin0["sample_count"] == 5
        assert bin0["agreement"] == 4

    def test_overall_weighted_avg_across_window(self, client, monkeypatch):
        """Overall avg_reward_48h is sum-weighted across bins:
        (sum_per_day(collected * avg) / sum_per_day(collected))."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        # 3 days; engagement:
        #   day 1: 10 rewards avg 0.5  → contributes 5.0
        #   day 2:  4 rewards avg 0.25 → contributes 1.0
        #   day 3:  0 rewards          → contributes nothing
        # weighted: (5.0 + 1.0) / 14 = 0.4286
        cal = [
            _row(date(2026, 6, 14), 5, 4),
            _row(date(2026, 6, 15), 3, 3),
            _row(date(2026, 6, 16), 3, 2),
        ]
        eng = [
            _engagement_row(date(2026, 6, 14), 10, 0.5),
            _engagement_row(date(2026, 6, 15), 4, 0.25),
            # day 16 absent → endpoint defaults to 0/None
        ]
        with _patch_pg(cal, engagement_rows=eng):
            resp = client.get("/api/v1/auto-approval/track-record?niche_id=sports&window_days=7")
        overall = resp.get_json()["data"]["overall"]
        assert overall["collected_count"] == 14
        # Tolerate rounding to 4 decimals (matches endpoint's round())
        assert overall["avg_reward_48h"] == 0.4286

    def test_overall_no_engagement_returns_null(self, client, monkeypatch):
        """Zero engagement across the window → avg_reward_48h=None
        (not 0.0 — distinguishes "no data" from "zero engagement")."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        cal = [_row(date(2026, 6, 14), 5, 4)]
        with _patch_pg(cal, engagement_rows=[]):
            resp = client.get("/api/v1/auto-approval/track-record?niche_id=sports&window_days=7")
        overall = resp.get_json()["data"]["overall"]
        assert overall["collected_count"] == 0
        assert overall["avg_reward_48h"] is None

    def test_multi_day_bin_aggregates_engagement(self, client, monkeypatch):
        """bin_days=7: engagement metrics aggregate across the week."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        # Build 7 days of data with engagement on alternate days
        cal = [_row(date(2026, 6, 10) + timedelta(days=i), 1, 1) for i in range(7)]
        eng = [
            _engagement_row(date(2026, 6, 10), 2, 0.3),
            _engagement_row(date(2026, 6, 12), 4, 0.5),
            _engagement_row(date(2026, 6, 14), 6, 0.1),
        ]
        with _patch_pg(cal, engagement_rows=eng):
            resp = client.get(
                "/api/v1/auto-approval/track-record?niche_id=sports&window_days=7&bin_days=7"
            )
        bins = resp.get_json()["data"]["bins"]
        assert len(bins) == 1
        # Weekly bin: 12 collected (2+4+6), weighted avg = (0.6+2.0+0.6)/12 = 0.2667
        assert bins[0]["collected_count"] == 12
        assert bins[0]["avg_reward_48h"] == 0.2667



class TestTrackRecordAllBatch:
    """PR #394 — /track-record-all returns per-niche dict in ONE HTTP request."""

    def test_returns_all_5_niches(self, client, monkeypatch):
        """Batch endpoint always returns all 5 niches in `niches:` dict."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        # Each per-niche query returns the same canned row set; that's
        # fine for shape-pinning. The integration test would verify
        # per-niche WHERE filtering against a real DB.
        cal = [_row(date(2026, 6, 14), 10, 9)]
        with _patch_pg(cal):
            resp = client.get("/api/v1/auto-approval/track-record-all")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert set(data["niches"].keys()) == {
            "ai_creators",
            "gaming",
            "sports",
            "movies",
            "anime",
        }
        assert data["window_days"] == 30
        assert data["bin_days"] == 1
        # Each niche entry has the same shape as /track-record's response.
        for niche_id, payload in data["niches"].items():
            assert payload["niche_id"] == niche_id
            assert "bins" in payload
            assert "overall" in payload
            assert payload["overall"]["sample_count"] == 10

    def test_validates_window_days(self, client):
        resp = client.get("/api/v1/auto-approval/track-record-all?window_days=999")
        assert resp.status_code == 400

    def test_per_niche_failure_returns_empty_shape_not_500(self, client, monkeypatch):
        """If ONE niche's query fails, other niches still load (batch endpoint
        graceful-degrades per-niche). The failed niche gets an empty bins
        list + zero overall — frontend renders "no data" for that row.
        """
        monkeypatch.setenv("DATABASE_URL", "")  # forces _TrackRecordError(503) per niche
        resp = client.get("/api/v1/auto-approval/track-record-all")
        # Even with every niche erroring, the batch returns 200 with
        # empty-shaped entries for each. This is the documented
        # graceful-degradation contract.
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        for niche_id in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert data["niches"][niche_id]["bins"] == []
            assert data["niches"][niche_id]["overall"]["sample_count"] == 0
