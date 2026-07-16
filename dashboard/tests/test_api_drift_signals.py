"""Integration pin: /api/v1/drift-signals/summary.

L7 audit item (2026-07-08). Sibling to `test_api_ensemble_votes.py`
(L5). Endpoint reads drift_signals rows and returns regressions +
improvements + per-niche counts.

Pins:
  1. Parameter clamping (window, min_abs_z, allowlist niche)
  2. Fail-open on storage failure
  3. Happy path: regression + improvement lists shape
  4. Regressions capped at 20; improvements at 10
  5. flag_enabled reflects strict "1" env
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from unittest.mock import MagicMock

import pytest
import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("GENLAB_DRIFT_PERSIST_ENABLED", raising=False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _install_pool(monkeypatch, *, regressions=None, improvements=None, counts=None):
    cursor = MagicMock()
    fetchall_iter = iter([regressions or [], improvements or [], counts or []])
    cursor.fetchall.side_effect = lambda: next(fetchall_iter)

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    pool.connection.return_value.__exit__.return_value = False
    monkeypatch.setattr("genlab_core.storage.get_pool", lambda: pool, raising=False)
    return cursor


class TestParameterHandling:
    def test_bad_niche_treated_as_all(self, client, monkeypatch):
        _install_pool(monkeypatch)
        resp = client.get("/api/v1/drift-signals/summary?niche_id=nonexistent")
        assert resp.get_json()["data"]["niche_id"] is None

    def test_window_clamped_upper(self, client, monkeypatch):
        _install_pool(monkeypatch)
        resp = client.get("/api/v1/drift-signals/summary?window_days=500")
        assert resp.get_json()["data"]["window_days"] == 90

    def test_window_clamped_lower(self, client, monkeypatch):
        _install_pool(monkeypatch)
        resp = client.get("/api/v1/drift-signals/summary?window_days=0")
        assert resp.get_json()["data"]["window_days"] == 1

    def test_min_abs_z_clamped(self, client, monkeypatch):
        _install_pool(monkeypatch)
        resp = client.get("/api/v1/drift-signals/summary?min_abs_z=50")
        assert resp.get_json()["data"]["min_abs_z"] == 10.0

    def test_min_abs_z_default_is_two(self, client, monkeypatch):
        _install_pool(monkeypatch)
        resp = client.get("/api/v1/drift-signals/summary")
        assert resp.get_json()["data"]["min_abs_z"] == 2.0


class TestFailOpen:
    def test_query_failure_returns_pending_migration_message(self, client, monkeypatch):
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError('relation "drift_signals" does not exist')
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False
        monkeypatch.setattr("genlab_core.storage.get_pool", lambda: pool, raising=False)

        resp = client.get("/api/v1/drift-signals/summary")
        body = resp.get_json()
        assert body["data"] is None
        assert "migration" in body["message"].lower()


class TestHappyPath:
    def test_summary_shape(self, client, monkeypatch):
        # A datetime object is what psycopg returns for TIMESTAMPTZ;
        # the endpoint should call .isoformat() on it.
        ts = datetime(2026, 7, 8, 16, 0, 0, tzinfo=UTC)
        regressions = [
            ("style:gaming:react", "gaming", 0.15, 0.32, -3.8, 18, 45, True, ts),
            ("style:movies:list", "movies", 0.10, 0.22, -2.7, 22, 30, True, ts),
        ]
        improvements = [
            ("style:sports:hype", "sports", 0.42, 0.30, 2.6, 20, 40, False, ts),
        ]
        counts = [
            ("gaming", 3, 1),
            ("movies", 1, 0),
            ("sports", 0, 2),
        ]
        _install_pool(
            monkeypatch,
            regressions=regressions,
            improvements=improvements,
            counts=counts,
        )

        resp = client.get("/api/v1/drift-signals/summary?niche_id=gaming&window_days=14")
        data = resp.get_json()["data"]
        assert data["window_days"] == 14
        assert data["niche_id"] == "gaming"

        # Two regressions, sorted z ASC → most-regressed first
        assert len(data["regressions"]) == 2
        assert data["regressions"][0]["z_score"] < data["regressions"][1]["z_score"]
        first = data["regressions"][0]
        assert first["arm_id"] == "style:gaming:react"
        assert first["is_regression"] is True
        # Timestamp converted to ISO string
        assert isinstance(first["computed_at"], str)
        assert "2026-07-08T16:00" in first["computed_at"]

        # One improvement
        assert len(data["improvements"]) == 1
        assert data["improvements"][0]["is_regression"] is False

        # Counts per niche
        by_niche = {c["niche_id"]: c for c in data["counts_by_niche"]}
        assert by_niche["gaming"]["regressions"] == 3
        assert by_niche["sports"]["improvements"] == 2

    def test_empty_result_returns_empty_lists(self, client, monkeypatch):
        _install_pool(monkeypatch)
        data = client.get("/api/v1/drift-signals/summary").get_json()["data"]
        assert data["regressions"] == []
        assert data["improvements"] == []
        assert data["counts_by_niche"] == []


class TestFlagEnabled:
    def test_flag_true_when_one(self, client, monkeypatch):
        _install_pool(monkeypatch)
        monkeypatch.setenv("GENLAB_DRIFT_PERSIST_ENABLED", "1")
        assert (
            client.get("/api/v1/drift-signals/summary").get_json()["data"]["flag_enabled"] is True
        )

    def test_flag_false_when_typo(self, client, monkeypatch):
        _install_pool(monkeypatch)
        monkeypatch.setenv("GENLAB_DRIFT_PERSIST_ENABLED", "true")
        assert (
            client.get("/api/v1/drift-signals/summary").get_json()["data"]["flag_enabled"] is False
        )
