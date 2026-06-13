"""Pins for /api/v1/auto-approval/calibration-stats.

AUTO #1b (2026-06-13). Read-only endpoint surfacing the per-niche
confusion matrix. Operators consult before flipping the AUTO #2 opt-in
flag.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import server.review_server as review_server_module
from genlab_core.scheduling.calibration_logger import CalibrationStats
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestValidation:
    def test_missing_niche_id_400(self, client):
        resp = client.get("/api/v1/auto-approval/calibration-stats")
        assert resp.status_code == 400

    def test_invalid_niche_id_400(self, client):
        resp = client.get("/api/v1/auto-approval/calibration-stats?niche_id=hacker'; DROP TABLE--")
        assert resp.status_code == 400

    def test_unknown_niche_id_400(self, client):
        # Whitelisted to 5 niches; anything else rejected.
        resp = client.get("/api/v1/auto-approval/calibration-stats?niche_id=politics")
        assert resp.status_code == 400

    def test_window_days_out_of_range(self, client):
        resp = client.get("/api/v1/auto-approval/calibration-stats?niche_id=gaming&window_days=200")
        assert resp.status_code == 400

    def test_window_days_non_integer(self, client):
        resp = client.get("/api/v1/auto-approval/calibration-stats?niche_id=gaming&window_days=abc")
        assert resp.status_code == 400


class TestShape:
    def test_cold_start_returns_zeros(self, client):
        stub = CalibrationStats(
            niche_id="gaming",
            sample_count=0,
            agreement_count=0,
            true_positives=0,
            true_negatives=0,
            false_positives=0,
            false_negatives=0,
        )
        with patch("genlab_core.scheduling.calibration_logger.stats", return_value=stub):
            resp = client.get("/api/v1/auto-approval/calibration-stats?niche_id=gaming")
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["niche_id"] == "gaming"
            assert data["sample_count"] == 0
            assert data["agreement_rate"] == 0.0
            assert data["ready_for_enforcement"] is False
            assert "confusion_matrix" in data

    def test_populated_stats(self, client):
        stub = CalibrationStats(
            niche_id="movies",
            sample_count=42,
            agreement_count=38,
            true_positives=30,
            true_negatives=8,
            false_positives=3,
            false_negatives=1,
        )
        with patch("genlab_core.scheduling.calibration_logger.stats", return_value=stub):
            resp = client.get(
                "/api/v1/auto-approval/calibration-stats?niche_id=movies&window_days=14"
            )
            assert resp.status_code == 200
            data = resp.get_json()["data"]
            assert data["window_days"] == 14
            assert data["sample_count"] == 42
            # 38 / 42 ≈ 0.905
            assert abs(data["agreement_rate"] - 0.905) < 0.005
            assert data["confusion_matrix"]["true_positives"] == 30
            assert data["confusion_matrix"]["false_positives"] == 3
            assert data["ready_for_enforcement"] is True


class TestAllFiveNichesAllowed:
    """Pin the whitelist — adding a 6th niche must require an explicit
    code change here (defense-in-depth against typo-rooms)."""

    @pytest.mark.parametrize("niche", ["ai_creators", "gaming", "sports", "movies", "anime"])
    def test_each_niche_accepted(self, client, niche):
        stub = CalibrationStats(
            niche_id=niche,
            sample_count=0,
            agreement_count=0,
            true_positives=0,
            true_negatives=0,
            false_positives=0,
            false_negatives=0,
        )
        with patch("genlab_core.scheduling.calibration_logger.stats", return_value=stub):
            resp = client.get(f"/api/v1/auto-approval/calibration-stats?niche_id={niche}")
            assert resp.status_code == 200
