"""Regression test for the post-2026-06-25-audit fix to
/api/v1/metrics/publishing — the SKIPPED status was previously
excluded from BOTH the WHERE clause AND the FILTER counters.

Before the fix:
  WHERE created_at > NOW() - INTERVAL '7 days'
    AND status IN ('SUCCESS', 'FAILED')  -- excludes SKIPPED entirely
  COUNT FILTER (status='SUCCESS') as ok,
  COUNT FILTER (status='FAILED') as fail

When YouTube OAuth tokens expired, parallel_publish.py:168-176 wrote
status='SKIPPED' (per project convention: CREDENTIAL-class errors
don't get the FAILED treatment to avoid spamming alerts). The
metrics query then literally excluded those records from the rate
denominator, producing "YT 0%" while the operator saw green ✓
checkmarks on Today's Publishes — operationally deceptive.

After the fix:
  WHERE status IN ('SUCCESS', 'FAILED', 'SKIPPED')
  COUNT FILTER (status='SUCCESS') as ok,
  COUNT FILTER (status='FAILED') as fail,
  COUNT FILTER (status='SKIPPED') as skipped

Pins:
  * SQL queries include 'SKIPPED' in WHERE filter
  * Response includes per-platform skipped count via skipped_7d field
  * success_rate denominator includes SKIPPED (so YT with 0 SUCCESS +
    5 SKIPPED + 0 FAILED reports 0% rather than vacuous 100%)
  * by_niche entries include skipped field
  * platform_status uses full denominator (ok+fail+skipped) for
    green/yellow/red thresholding
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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
    return app.test_client()


def _mock_db_rows(rates_rows, trend_rows, errors_rows, status_24h_rows, niche_rates_rows):
    """Build a fake DB connection that returns the supplied rowset for
    each successive execute() call in publishing_metrics()."""
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    # Each .execute() returns an object with .fetchall()
    fake_results = [
        MagicMock(fetchall=MagicMock(return_value=rates_rows)),
        MagicMock(fetchall=MagicMock(return_value=trend_rows)),
        MagicMock(fetchall=MagicMock(return_value=errors_rows)),
        MagicMock(fetchall=MagicMock(return_value=status_24h_rows)),
        MagicMock(fetchall=MagicMock(return_value=niche_rates_rows)),
    ]
    fake_conn.execute.side_effect = fake_results
    return fake_conn


@pytest.fixture
def _stub_pg_connect(monkeypatch):
    """Stub the pg_connect helper called inside publishing_metrics."""

    def _factory(fake_conn):
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setattr(
            "server.api.metrics.pg_connect",
            lambda *args, **kwargs: fake_conn,
        )

    return _factory


def test_skipped_records_count_against_success_rate(client, _stub_pg_connect):
    """YT with 0 SUCCESS + 0 FAILED + 5 SKIPPED should report 0% rate,
    not 'no data' or vacuous 100%. Prior bug: SKIPPED was excluded so
    YT showed 0% because the denominator was ALSO 0 (vacuous).

    Now: SKIPPED in denominator → 0/5 = 0%, operator sees credential
    failures as the actual cause."""
    rates_rows = [
        {"platform": "youtube", "ok": 0, "fail": 0, "skipped": 5},
        {"platform": "facebook", "ok": 10, "fail": 0, "skipped": 0},
    ]
    fake_conn = _mock_db_rows(rates_rows, [], [], [], [])
    _stub_pg_connect(fake_conn)

    resp = client.get("/api/v1/metrics/publishing")
    assert resp.status_code == 200
    body = resp.get_json()["data"]

    # YT shows 0% — the bug being fixed: previously YT wouldn't even
    # appear in success_rate_7d because all its records were excluded.
    assert body["success_rate_7d"]["youtube"] == 0
    assert body["success_rate_7d"]["facebook"] == 100


def test_skipped_count_surfaced_per_platform(client, _stub_pg_connect):
    """The new skipped_7d field exposes credential-failure counts
    per platform, giving operators direct visibility into the
    SKIPPED bucket they couldn't see before."""
    rates_rows = [
        {"platform": "youtube", "ok": 0, "fail": 0, "skipped": 5},
        {"platform": "instagram", "ok": 30, "fail": 1, "skipped": 0},
    ]
    fake_conn = _mock_db_rows(rates_rows, [], [], [], [])
    _stub_pg_connect(fake_conn)

    resp = client.get("/api/v1/metrics/publishing")
    body = resp.get_json()["data"]
    assert body["skipped_7d"]["youtube"] == 5
    assert body["skipped_7d"]["instagram"] == 0


def test_by_niche_includes_skipped_count(client, _stub_pg_connect):
    """Per-niche breakdown surfaces SKIPPED so operator can identify
    which niche × platform pairs have credential issues."""
    niche_rows = [
        {"niche_id": "gaming", "ok": 7, "fail": 0, "skipped": 0},
        {"niche_id": "anime", "ok": 0, "fail": 1, "skipped": 4},
    ]
    fake_conn = _mock_db_rows([], [], [], [], niche_rows)
    _stub_pg_connect(fake_conn)

    resp = client.get("/api/v1/metrics/publishing")
    body = resp.get_json()["data"]
    assert body["by_niche"]["gaming"]["skipped"] == 0
    assert body["by_niche"]["anime"]["skipped"] == 4
    # anime success rate = 0 / (0+1+4) = 0%, not vacuous
    assert body["by_niche"]["anime"]["rate"] == 0
    assert body["by_niche"]["anime"]["success"] == 0
    assert body["by_niche"]["anime"]["failed"] == 1


def test_platform_status_uses_full_denominator(client, _stub_pg_connect):
    """Status threshold (green ≥80%, yellow ≥50%, red <50%) uses
    ok / (ok + fail + skipped). YT with all SKIPPED should be RED,
    not GREY (no data) and not GREEN (vacuous 100%)."""
    status_24h_rows = [
        # YT: all skipped → 0/5 = 0% → red
        {"platform": "youtube", "ok": 0, "fail": 0, "skipped": 5},
        # FB: all success → 10/10 = 100% → green
        {"platform": "facebook", "ok": 10, "fail": 0, "skipped": 0},
        # IG: mixed → 6/10 = 60% → yellow
        {"platform": "instagram", "ok": 6, "fail": 2, "skipped": 2},
    ]
    fake_conn = _mock_db_rows([], [], [], status_24h_rows, [])
    _stub_pg_connect(fake_conn)

    resp = client.get("/api/v1/metrics/publishing")
    body = resp.get_json()["data"]
    assert body["platform_status"]["youtube"] == "red"
    assert body["platform_status"]["facebook"] == "green"
    assert body["platform_status"]["instagram"] == "yellow"


def test_daily_trend_includes_skipped_per_day(client, _stub_pg_connect):
    """Daily trend response includes skipped count per day so
    operators can see credential-failure spikes in time."""
    from datetime import date

    trend_rows = [
        {"day": date(2026, 6, 24), "ok": 4, "fail": 0, "skipped": 1},
        {"day": date(2026, 6, 25), "ok": 3, "fail": 0, "skipped": 2},
    ]
    fake_conn = _mock_db_rows([], trend_rows, [], [], [])
    _stub_pg_connect(fake_conn)

    resp = client.get("/api/v1/metrics/publishing")
    body = resp.get_json()["data"]
    assert len(body["daily_trend"]) == 2
    assert body["daily_trend"][0]["skipped"] == 1
    assert body["daily_trend"][1]["skipped"] == 2


def test_no_dsn_returns_503(client, monkeypatch):
    """Defensive: no DATABASE_URL returns 503 (per project convention)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    resp = client.get("/api/v1/metrics/publishing")
    assert resp.status_code == 503
