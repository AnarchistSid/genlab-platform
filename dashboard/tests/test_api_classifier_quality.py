"""Integration pin: /api/v1/learning/classifier-quality.

Phase 1.C observability (2026-08-14). Aggregates
strategist_outcome_verification rows GROUP BY (classifier_source,
classifier_name), returns verdict mix + accuracy per group.

Pins:
1. Row shape (all expected fields)
2. Accuracy calculation — improved / (improved + regressed)
3. Null accuracy when all unchanged (denominator=0)
4. Empty data returns empty list, 200 OK
5. DATABASE_URL unset → 503
6. DB error → 500
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
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:5432/db")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _mock_conn(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=None)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=None)
    return conn


def _row(source, name, verified, imp, unch, reg):
    return {
        "classifier_source": source, "classifier_name": name,
        "n_verified": verified, "n_improved": imp,
        "n_unchanged": unch, "n_regressed": reg,
    }


class TestRowShape:
    def test_all_expected_fields_returned(self, client):
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("heuristic", "arm_add", 10, 6, 2, 2)]),
        ):
            r = client.get("/api/v1/learning/classifier-quality")
        assert r.status_code == 200
        row = r.get_json()["data"][0]
        assert row["classifier_source"] == "heuristic"
        assert row["classifier_name"] == "arm_add"
        assert row["n_verified"] == 10
        assert row["n_improved"] == 6
        assert row["n_unchanged"] == 2
        assert row["n_regressed"] == 2
        # accuracy = 6 / (6 + 2) = 0.75
        assert row["accuracy"] == pytest.approx(0.75)


class TestAccuracyCalculation:
    def test_null_accuracy_when_no_diagnostic_verdicts(self, client):
        """All unchanged → denominator=0 → null accuracy (cold-start
        for a source that has verified rows but none moved the metric
        either way)."""
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("llm", "arm_add", 5, 0, 5, 0)]),
        ):
            r = client.get("/api/v1/learning/classifier-quality")
        assert r.get_json()["data"][0]["accuracy"] is None

    def test_perfect_accuracy(self, client):
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("heuristic", "arm_add", 5, 5, 0, 0)]),
        ):
            r = client.get("/api/v1/learning/classifier-quality")
        assert r.get_json()["data"][0]["accuracy"] == 1.0

    def test_zero_accuracy(self, client):
        """All regressed → accuracy = 0."""
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("llm", "arm_add", 5, 0, 0, 5)]),
        ):
            r = client.get("/api/v1/learning/classifier-quality")
        assert r.get_json()["data"][0]["accuracy"] == 0.0

    def test_unchanged_excluded_from_denominator(self, client):
        """3 improved + 7 unchanged + 2 regressed = accuracy 3/(3+2) = 0.6.
        Unchanged doesn't count."""
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([_row("heuristic", "arm_add", 12, 3, 7, 2)]),
        ):
            r = client.get("/api/v1/learning/classifier-quality")
        assert r.get_json()["data"][0]["accuracy"] == pytest.approx(0.6)


class TestMultipleGroups:
    def test_orders_by_source_then_name(self, client):
        """Backend query orders by (source, name); endpoint preserves."""
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([
                _row("heuristic", "arm_add", 20, 15, 3, 2),
                _row("llm", "arm_add", 8, 5, 2, 1),
                _row("manual", "arm_add", 4, 2, 1, 1),
            ]),
        ):
            r = client.get("/api/v1/learning/classifier-quality")
        data = r.get_json()["data"]
        sources = [row["classifier_source"] for row in data]
        assert sources == ["heuristic", "llm", "manual"]


class TestEmptyAndFailStates:
    def test_no_rows_returns_empty_list(self, client):
        with patch(
            "server.api.learning.pg_connect",
            return_value=_mock_conn([]),
        ):
            r = client.get("/api/v1/learning/classifier-quality")
        assert r.status_code == 200
        assert r.get_json()["data"] == []

    def test_no_database_url_returns_503(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        r = client.get("/api/v1/learning/classifier-quality")
        assert r.status_code == 503

    def test_db_error_returns_500(self, client):
        with patch(
            "server.api.learning.pg_connect",
            side_effect=RuntimeError("db down"),
        ):
            r = client.get("/api/v1/learning/classifier-quality")
        assert r.status_code == 500
