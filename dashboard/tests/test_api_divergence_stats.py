"""Pin the divergence-stats endpoint (L3 PR 12a, 2026-07-07).

Mirrors the calibration-stats endpoint's contract for consistency —
frontend can reuse card patterns; operator has the same mental model.

Contract:
  1. Missing niche_id → 400
  2. Invalid niche_id → 400
  3. Invalid window_days → 400 (both non-int and out-of-range)
  4. Missing DATABASE_URL → success + 0 samples (not an error path)
  5. DB exception → success + 0 samples (fail-open, not 500)
  6. Empty result → agreement_rate=0.0, ready=false
  7. High agreement + samples ≥ 30 → ready_for_enforcement=true
  8. High agreement but <30 samples → ready=false (both thresholds
     must clear)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Match the sibling test files' pattern for review_server import.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module  # noqa: E402
from server.review_server import app  # noqa: E402


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestValidation:
    def test_missing_niche_id_returns_400(self, client):
        response = client.get("/api/v1/product-bandit/divergence-stats")
        assert response.status_code == 400

    def test_invalid_niche_id_returns_400(self, client):
        response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=invalid")
        assert response.status_code == 400

    def test_non_int_window_days_returns_400(self, client):
        response = client.get(
            "/api/v1/product-bandit/divergence-stats?niche_id=gaming&window_days=abc"
        )
        assert response.status_code == 400

    def test_out_of_range_window_days_returns_400(self, client):
        response = client.get(
            "/api/v1/product-bandit/divergence-stats?niche_id=gaming&window_days=999"
        )
        assert response.status_code == 400

        response = client.get(
            "/api/v1/product-bandit/divergence-stats?niche_id=gaming&window_days=0"
        )
        assert response.status_code == 400


class TestFailOpen:
    def test_no_database_url_returns_zero_samples(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)

        response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=gaming")
        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["sample_count"] == 0
        assert data["agreement_rate"] == 0.0
        assert data["ready_for_enforcement"] is False

    def test_db_exception_returns_zero_samples(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

        with patch("psycopg.connect", side_effect=RuntimeError("db unreachable")):
            response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=gaming")
        assert response.status_code == 200
        data = response.get_json()["data"]
        assert data["sample_count"] == 0


class TestStatsComputation:
    """The SQL aggregates into (sample_count, agreement_count).
    The endpoint derives rate + ready flag from those."""

    def _mock_query_result(self, sample_count: int, agreement_count: int):
        """Return a mock psycopg context that responds with (sample_count, agreement_count)."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (sample_count, agreement_count)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn
        return mock_ctx

    def test_zero_samples_reports_uniform(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

        with patch("psycopg.connect", return_value=self._mock_query_result(0, 0)):
            response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=gaming")
        data = response.get_json()["data"]
        assert data["sample_count"] == 0
        assert data["agreement_count"] == 0
        assert data["agreement_rate"] == 0.0
        assert data["ready_for_enforcement"] is False

    def test_perfect_agreement_computes_1_0(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

        with patch("psycopg.connect", return_value=self._mock_query_result(50, 50)):
            response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=gaming")
        data = response.get_json()["data"]
        assert data["sample_count"] == 50
        assert data["agreement_count"] == 50
        assert data["agreement_rate"] == 1.0
        # 50 samples ≥ 30 AND 100% ≥ 90% → ready
        assert data["ready_for_enforcement"] is True

    def test_high_agreement_but_low_sample_not_ready(self, client, monkeypatch):
        """20 samples at 100% agreement is NOT ready — need ≥30 samples."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

        with patch("psycopg.connect", return_value=self._mock_query_result(20, 20)):
            response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=gaming")
        data = response.get_json()["data"]
        assert data["sample_count"] == 20
        assert data["agreement_rate"] == 1.0
        assert data["ready_for_enforcement"] is False

    def test_low_agreement_high_samples_not_ready(self, client, monkeypatch):
        """50 samples at 80% agreement is NOT ready — need ≥90%."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

        with patch("psycopg.connect", return_value=self._mock_query_result(50, 40)):
            response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=gaming")
        data = response.get_json()["data"]
        assert data["sample_count"] == 50
        assert data["agreement_rate"] == 0.8
        assert data["ready_for_enforcement"] is False

    def test_borderline_90pct_is_ready(self, client, monkeypatch):
        """Exactly 90% agreement + ≥30 samples → ready."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

        with patch("psycopg.connect", return_value=self._mock_query_result(30, 27)):
            response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=gaming")
        data = response.get_json()["data"]
        assert data["sample_count"] == 30
        assert data["agreement_rate"] == 0.9
        assert data["ready_for_enforcement"] is True


class TestWindowDaysParameter:
    def test_default_window_is_7_days(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0, 0)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            response = client.get("/api/v1/product-bandit/divergence-stats?niche_id=gaming")
        data = response.get_json()["data"]
        assert data["window_days"] == 7

    def test_custom_window_days_honored(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0, 0)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch("psycopg.connect", return_value=mock_ctx):
            response = client.get(
                "/api/v1/product-bandit/divergence-stats?niche_id=gaming&window_days=14"
            )
        data = response.get_json()["data"]
        assert data["window_days"] == 14
