"""Tests for /api/v1/pipeline endpoints — prefect_connected field and status."""
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["DRY_RUN"] = True
    app.config["LOCAL_MODE"] = True
    # Clear the module-level status cache between tests
    from server.api import pipeline as _pipe_mod
    _pipe_mod._status_cache["data"] = None
    _pipe_mod._status_cache["ts"] = 0.0
    with app.test_client() as c:
        yield c


def test_status_includes_prefect_connected(client):
    """Status response should include prefect_connected boolean."""
    with patch("server.api.pipeline._prefect_healthy", return_value=False):
        resp = client.get("/api/v1/pipeline/status")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "prefect_connected" in data
    assert data["prefect_connected"] is False


def test_status_prefect_connected_true(client):
    """When Prefect is healthy, prefect_connected should be True."""
    with patch("server.api.pipeline._prefect_healthy", return_value=True), \
         patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x):
        resp = client.get("/api/v1/pipeline/status")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["prefect_connected"] is True


def test_status_has_niches_key(client):
    """Status response should always have a niches array."""
    with patch("server.api.pipeline._prefect_healthy", return_value=False):
        resp = client.get("/api/v1/pipeline/status")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "niches" in data
    assert isinstance(data["niches"], list)


def test_runs_endpoint(client):
    """GET /pipeline/runs should return paginated data."""
    resp = client.get("/api/v1/pipeline/runs")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "data" in data
    assert "meta" in data
    assert "page" in data["meta"]
