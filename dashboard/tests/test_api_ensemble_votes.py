"""Integration pin: /api/v1/ensemble-votes/summary.

L5 audit item (2026-07-08). Endpoint aggregates ensemble_votes rows
into per-component participation stats + recommendation distribution.

Pins:
  1. Malformed niche_id → treated as "all niches" (allowlist enforced)
  2. Malformed window_days → default 7
  3. Window_days clamped to [1, 90]
  4. Storage import failure → 200 + data=null (never 500)
  5. DB query failure → 200 + data=null with pending-migration message
  6. Happy path with mocked rows: component summary + recommendation
     distribution shape
  7. NULL scores + counts handled correctly (mean_score can be None)
  8. flag_enabled reflects env var (strict "1")
"""

from __future__ import annotations

import sys
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
    monkeypatch.delenv("GENLAB_ENSEMBLE_PERSIST_ENABLED", raising=False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _install_pool(monkeypatch, comp_rows=None, total_row=None, reco_rows=None):
    """Wire a mocked psycopg pool so the endpoint can run without DB.

    Each successive `cur.execute` in the endpoint reads a different
    row set (component, total, recommendation). We stage them in
    order and switch the cursor's fetch behaviour per call.
    """
    cursor = MagicMock()
    # execute() is a no-op; fetchall / fetchone toggle across calls.
    fetchall_iter = iter([comp_rows or [], reco_rows or []])
    cursor.fetchall.side_effect = lambda: next(fetchall_iter)
    cursor.fetchone.return_value = total_row

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False

    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    pool.connection.return_value.__exit__.return_value = False

    monkeypatch.setattr(
        "genlab_core.storage.get_pool",
        lambda: pool,
        raising=False,
    )
    return cursor


class TestParameterHandling:
    def test_malformed_niche_treated_as_all(self, client, monkeypatch):
        """Not-in-allowlist niche_id → summary aggregates all niches
        (endpoint filters silently instead of 400ing, matching sibling
        endpoints' fail-open contract)."""
        _install_pool(
            monkeypatch,
            comp_rows=[],
            total_row=(0,),
            reco_rows=[],
        )
        resp = client.get(
            "/api/v1/ensemble-votes/summary?niche_id=nonexistent"
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"]["niche_id"] is None

    def test_bad_window_days_falls_back_to_default(self, client, monkeypatch):
        _install_pool(
            monkeypatch,
            comp_rows=[],
            total_row=(0,),
            reco_rows=[],
        )
        resp = client.get(
            "/api/v1/ensemble-votes/summary?window_days=not-a-number"
        )
        assert resp.get_json()["data"]["window_days"] == 7

    def test_window_days_clamped_upper(self, client, monkeypatch):
        _install_pool(
            monkeypatch,
            comp_rows=[],
            total_row=(0,),
            reco_rows=[],
        )
        resp = client.get(
            "/api/v1/ensemble-votes/summary?window_days=999"
        )
        assert resp.get_json()["data"]["window_days"] == 90

    def test_window_days_clamped_lower(self, client, monkeypatch):
        _install_pool(
            monkeypatch,
            comp_rows=[],
            total_row=(0,),
            reco_rows=[],
        )
        resp = client.get(
            "/api/v1/ensemble-votes/summary?window_days=0"
        )
        assert resp.get_json()["data"]["window_days"] == 1


class TestFailOpen:
    def test_storage_import_failure_returns_data_null(
        self, client, monkeypatch
    ):
        """A missing psycopg import shouldn't 500 — the endpoint gets
        polled every 60s by the dashboard, and a full outage would
        cascade."""
        # Patch get_pool to raise ImportError inside the endpoint's
        # try block. Cleanest way: overshadow storage in sys.modules
        # so the lazy import inside the endpoint hits the patched fn.
        import genlab_core.storage as storage

        original_get_pool = getattr(storage, "get_pool", None)

        def _raiser():
            raise RuntimeError("simulated: pool exhausted")

        monkeypatch.setattr(storage, "get_pool", _raiser, raising=False)
        try:
            resp = client.get("/api/v1/ensemble-votes/summary")
            assert resp.status_code == 200
            assert resp.get_json()["data"] is None
            assert (
                "migration"
                in resp.get_json().get("message", "").lower()
            )
        finally:
            if original_get_pool is not None:
                monkeypatch.setattr(storage, "get_pool", original_get_pool)

    def test_query_failure_returns_pending_migration_message(
        self, client, monkeypatch
    ):
        """When the table doesn't exist yet (migration pending), the
        endpoint MUST return a specific 'migration pending' message so
        the frontend can distinguish this from 'no data yet'."""
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError(
            'relation "ensemble_votes" does not exist'
        )
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False
        monkeypatch.setattr(
            "genlab_core.storage.get_pool", lambda: pool, raising=False
        )

        resp = client.get("/api/v1/ensemble-votes/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"] is None
        assert "migration" in body["message"].lower()


class TestHappyPath:
    def test_summary_shape(self, client, monkeypatch):
        """A full happy-path response — mocked rows exercise the
        aggregation math + shape."""
        # Component summary: (component, voted, abstained, mean_score,
        # mean_disagreement_when_voting)
        comp_rows = [
            ("bandit", 130, 13, 0.52, 0.024),
            ("bayesian_gate", 100, 43, 0.48, 0.030),
            ("conformal_router", 70, 73, 0.55, 0.020),
            ("hook_classifier", 143, 0, 0.51, 0.025),
        ]
        # Distinct decisions
        total_row = (143,)
        # Recommendation distribution
        reco_rows = [
            ("auto_approve", 45),
            ("worth_your_look", 32),
            ("route_to_operator", 40),
            ("auto_reject", 20),
            ("insufficient_data", 6),
        ]
        _install_pool(
            monkeypatch,
            comp_rows=comp_rows,
            total_row=total_row,
            reco_rows=reco_rows,
        )
        resp = client.get(
            "/api/v1/ensemble-votes/summary?niche_id=gaming&window_days=7"
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["total_decisions"] == 143
        assert data["niche_id"] == "gaming"
        assert data["window_days"] == 7

        # Component: vote_rate = voted / (voted + abstained)
        bandit = next(c for c in data["components"] if c["component"] == "bandit")
        assert bandit["voted"] == 130
        assert bandit["abstained"] == 13
        assert bandit["vote_rate"] == pytest.approx(130 / 143)
        assert bandit["mean_score"] == pytest.approx(0.52)

        # Hook classifier never abstained → vote_rate exactly 1.0
        hook = next(
            c for c in data["components"] if c["component"] == "hook_classifier"
        )
        assert hook["vote_rate"] == pytest.approx(1.0)
        assert hook["abstained"] == 0

        # Recommendation distribution round-trips
        assert data["recommendations"]["auto_approve"] == 45
        assert data["recommendations"]["insufficient_data"] == 6

    def test_null_mean_score_stays_null(self, client, monkeypatch):
        """A component that abstained on every decision in the window
        has NULL mean_score from Postgres — endpoint must forward as
        Python None, not coerce to 0.0."""
        _install_pool(
            monkeypatch,
            comp_rows=[("llm_judge", 0, 5, None, None)],
            total_row=(5,),
            reco_rows=[],
        )
        resp = client.get("/api/v1/ensemble-votes/summary")
        judge = next(
            c
            for c in resp.get_json()["data"]["components"]
            if c["component"] == "llm_judge"
        )
        assert judge["mean_score"] is None
        assert judge["mean_disagreement_when_voting"] is None
        assert judge["vote_rate"] == 0.0


class TestFlagEnabled:
    def test_flag_enabled_true_when_one(self, client, monkeypatch):
        _install_pool(
            monkeypatch, comp_rows=[], total_row=(0,), reco_rows=[]
        )
        monkeypatch.setenv("GENLAB_ENSEMBLE_PERSIST_ENABLED", "1")
        assert (
            client.get("/api/v1/ensemble-votes/summary")
            .get_json()["data"]["flag_enabled"]
            is True
        )

    def test_flag_enabled_false_when_typo(self, client, monkeypatch):
        """Strict "1" match — "true" is NOT enabled. Locks in the
        sibling flag-state semantic so a well-meaning loosening
        surfaces as a test failure."""
        _install_pool(
            monkeypatch, comp_rows=[], total_row=(0,), reco_rows=[]
        )
        monkeypatch.setenv("GENLAB_ENSEMBLE_PERSIST_ENABLED", "true")
        assert (
            client.get("/api/v1/ensemble-votes/summary")
            .get_json()["data"]["flag_enabled"]
            is False
        )
