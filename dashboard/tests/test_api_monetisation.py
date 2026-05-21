"""Tests for /api/v1/monetisation/progress endpoint."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

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


MOCK_RECORDS = [
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "subscribers",
        "current_value": 850,
        "target_value": 1000,
        "pct_complete": 85.0,
        "delta_7d": 50,
        "days_to_threshold_est": 21,
        "is_threshold_met": False,
        "data_source": "api",
        "as_of_date": "2026-03-13T08:00:00+00:00",
        "error_log": None,
    },
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "watch_hours_12mo",
        "current_value": 4500,
        "target_value": 4000,
        "pct_complete": 112.5,
        "delta_7d": None,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
        "data_source": "api",
        "as_of_date": "2026-03-13T08:00:00+00:00",
        "error_log": None,
    },
    {
        "niche_id": "gaming",
        "platform": "instagram",
        "metric_name": "followers",
        "current_value": 12000,
        "target_value": 10000,
        "pct_complete": 120.0,
        "delta_7d": 500,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
        "data_source": "api",
        "as_of_date": "2026-03-13T08:00:00+00:00",
        "error_log": None,
    },
]


class TestMonetisationProgress:
    """GET /api/v1/monetisation/progress"""

    @patch("server.api.monetisation._fetch_progress", return_value=MOCK_RECORDS)
    def test_progress_returns_grouped_data(self, mock_fetch, client):
        resp = client.get("/api/v1/monetisation/progress")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert "ai_creators" in data
        assert "gaming" in data
        assert "youtube" in data["ai_creators"]

    @patch("server.api.monetisation._fetch_progress", return_value=MOCK_RECORDS)
    def test_progress_metrics_structure(self, mock_fetch, client):
        resp = client.get("/api/v1/monetisation/progress")
        data = json.loads(resp.data)["data"]
        yt_metrics = data["ai_creators"]["youtube"]["metrics"]
        assert len(yt_metrics) == 2
        names = {m["metric_name"] for m in yt_metrics}
        assert names == {"subscribers", "watch_hours_12mo"}

    @patch("server.api.monetisation._fetch_progress", return_value=MOCK_RECORDS)
    def test_progress_is_monetised_flag(self, mock_fetch, client):
        resp = client.get("/api/v1/monetisation/progress")
        data = json.loads(resp.data)["data"]
        # gaming/instagram: 1 metric met, 1 metric with target → monetised
        assert data["gaming"]["instagram"]["is_monetised"] is True
        # ai_creators/youtube: 1/2 metrics met → not monetised
        assert data["ai_creators"]["youtube"]["is_monetised"] is False

    @patch("server.api.monetisation._fetch_progress", return_value=[])
    def test_progress_empty_data(self, mock_fetch, client):
        resp = client.get("/api/v1/monetisation/progress")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data == {}
