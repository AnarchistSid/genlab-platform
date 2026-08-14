"""Integration pin: /api/v1/learning/reward-audit.

Phase 0.C observability (2026-08-14). Endpoint queries
pending_feedback for last-7d reward distributions per (niche,
platform), computes stddev/percentiles, categorizes health.

Pins:
1. Endpoint returns 5 niches (all shipping niches) even when some
   have zero rewards
2. Signal status heuristic — healthy / weak / stale / cold
3. Per-niche verdict — healthy (≥3 platforms) / partial / broken
4. DB error returns 500 with structured error (fail-safe)
5. DATABASE_URL unset returns 503
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:5432/db")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _mock_conn(rows):
    """Rows = list of dicts to return from fetchall()."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=None)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=None)
    return conn


def _row(niche, platform, n=10, stddev=0.1, hours=6.0, avg=0.3):
    return {
        "niche_id": niche, "platform": platform,
        "n_rewards_7d": n,
        "min_r": max(0.0, avg - stddev), "max_r": avg + stddev,
        "avg_r": avg, "stddev_r": stddev,
        "p25": avg - stddev * 0.5, "p50": avg, "p75": avg + stddev * 0.5,
        "hours_since_latest": hours,
    }


class TestSignalStatusHeuristic:
    def test_healthy_row_signal_status(self, client):
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("anime", "facebook", n=10, stddev=0.2)]),
        ):
            r = client.get("/api/v1/learning/reward-audit")
        assert r.status_code == 200
        data = r.get_json()["data"]
        anime = next(n for n in data if n["niche_id"] == "anime")
        fb = next(p for p in anime["platforms"] if p["platform"] == "facebook")
        assert fb["signal_status"] == "healthy"

    def test_weak_when_low_stddev(self, client):
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("gaming", "youtube", n=10, stddev=0.01)]),
        ):
            r = client.get("/api/v1/learning/reward-audit")
        gaming = next(n for n in r.get_json()["data"] if n["niche_id"] == "gaming")
        yt = next(p for p in gaming["platforms"] if p["platform"] == "youtube")
        assert yt["signal_status"] == "weak"

    def test_stale_when_latest_older_than_48h(self, client):
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("sports", "threads", n=10, stddev=0.1, hours=72)]),
        ):
            r = client.get("/api/v1/learning/reward-audit")
        sports = next(n for n in r.get_json()["data"] if n["niche_id"] == "sports")
        th = next(p for p in sports["platforms"] if p["platform"] == "threads")
        assert th["signal_status"] == "stale"

    def test_cold_when_fewer_than_3_samples(self, client):
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("movies", "instagram", n=2, stddev=0.2)]),
        ):
            r = client.get("/api/v1/learning/reward-audit")
        movies = next(n for n in r.get_json()["data"] if n["niche_id"] == "movies")
        ig = next(p for p in movies["platforms"] if p["platform"] == "instagram")
        assert ig["signal_status"] == "cold"


class TestNicheVerdict:
    def test_broken_when_zero_healthy_platforms(self, client):
        # All platforms weak: n small OR stddev 0
        rows = [
            _row("gaming", "youtube", n=10, stddev=0.001),
            _row("gaming", "instagram", n=10, stddev=0.001),
            _row("gaming", "threads", n=10, stddev=0.001),
        ]
        with patch("server.api.learning.pg_connect", return_value=_mock_conn(rows)):
            r = client.get("/api/v1/learning/reward-audit")
        gaming = next(n for n in r.get_json()["data"] if n["niche_id"] == "gaming")
        assert gaming["verdict"] == "broken"

    def test_partial_when_one_healthy(self, client):
        rows = [
            _row("anime", "facebook", n=10, stddev=0.3),   # healthy
            _row("anime", "youtube", n=10, stddev=0.001),  # weak
            _row("anime", "instagram", n=10, stddev=0.001),
        ]
        with patch("server.api.learning.pg_connect", return_value=_mock_conn(rows)):
            r = client.get("/api/v1/learning/reward-audit")
        anime = next(n for n in r.get_json()["data"] if n["niche_id"] == "anime")
        assert anime["verdict"] == "partial"

    def test_healthy_when_three_platforms_healthy(self, client):
        rows = [
            _row("sports", p, n=10, stddev=0.3)
            for p in ("facebook", "instagram", "youtube")
        ]
        with patch("server.api.learning.pg_connect", return_value=_mock_conn(rows)):
            r = client.get("/api/v1/learning/reward-audit")
        sports = next(n for n in r.get_json()["data"] if n["niche_id"] == "sports")
        assert sports["verdict"] == "healthy"


class TestAllNichesReturned:
    def test_five_niches_returned_even_when_data_missing(self, client):
        """Empty prod DB → 5 rows still returned (with empty platforms)."""
        with patch("server.api.learning.pg_connect", return_value=_mock_conn([])):
            r = client.get("/api/v1/learning/reward-audit")
        data = r.get_json()["data"]
        niches = {n["niche_id"] for n in data}
        assert niches == {"ai_creators", "anime", "gaming", "movies", "sports"}
        for n in data:
            assert n["verdict"] == "broken"  # 0 healthy platforms
            assert n["platforms"] == []


class TestFailModes:
    def test_no_database_url_returns_503(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        r = client.get("/api/v1/learning/reward-audit")
        assert r.status_code == 503

    def test_db_error_returns_500(self, client):
        with patch(
            "server.api.learning.pg_connect",
            side_effect=RuntimeError("db unreachable"),
        ):
            r = client.get("/api/v1/learning/reward-audit")
        assert r.status_code == 500
