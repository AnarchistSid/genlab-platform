"""Integration pin: /api/v1/trend-anticipation/latest.

Intervention 5 Session 3 (2026-07-01). The endpoint reads the daily
artifact written by ``scripts/anticipate_trends.py`` and returns it
verbatim. Pins:

1. 200 + data payload when artifact exists for today
2. 200 + data=null when no artifact exists (cold-start / first run)
3. 400 on invalid niche_id (typo protection)
4. Malformed JSON on disk → 200 + data=null (never crashes the API)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from datetime import UTC, datetime

import pytest
import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Point the endpoint at a tmp artifact root so tests can write
    fixture files without polluting the real .tmp/ dir."""
    monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
    app.config["TESTING"] = True
    app.config["DRY_RUN"] = True
    with app.test_client() as c:
        yield c


def _write_artifact(root: Path, niche_id: str, payload: dict) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    dest = root / "trend-anticipation"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{stamp}-{niche_id}.json"
    path.write_text(json.dumps(payload))
    return path


class TestGetLatest:
    def test_returns_payload_when_artifact_exists(self, client, tmp_path):
        payload = {
            "generated_at": "2026-07-01T03:30:00+00:00",
            "niche_id": "gaming",
            "flag_enabled": False,
            "ranking": [
                {
                    "topic": "Zelda TOTK 2",
                    "niche_id": "gaming",
                    "composite_score": 0.82,
                    "confidence": 0.75,
                    "anticipated_peak_hours_ahead": 12,
                    "signals": {
                        "search_velocity": 0.85,
                        "creator_pickup": 0.72,
                        "social_velocity": 0.6,
                        "news_lead": None,
                    },
                    "reasons": ["search_velocity=0.850"],
                }
            ],
        }
        _write_artifact(tmp_path, "gaming", payload)

        resp = client.get("/api/v1/trend-anticipation/latest?niche_id=gaming")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        data = body["data"]
        assert data is not None
        assert data["niche_id"] == "gaming"
        assert len(data["ranking"]) == 1
        assert data["ranking"][0]["composite_score"] == pytest.approx(0.82)

    def test_data_null_when_no_artifact(self, client):
        """Cold start — no artifact today → data=null. Frontend
        renders "No ranking yet" defensively."""
        resp = client.get("/api/v1/trend-anticipation/latest?niche_id=anime")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"] is None

    def test_400_on_invalid_niche(self, client):
        resp = client.get("/api/v1/trend-anticipation/latest?niche_id=notaniche")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["status"] == "error"
        assert "Invalid niche_id" in body["message"]

    def test_malformed_json_returns_null_not_500(self, client, tmp_path):
        """A corrupted artifact must NOT crash the endpoint —
        renders the same "no data yet" fallback."""
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        dest = tmp_path / "trend-anticipation"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{stamp}-movies.json").write_text("{not valid json")

        resp = client.get("/api/v1/trend-anticipation/latest?niche_id=movies")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None


class TestGetAccuracy:
    """Session 3b — the ``/accuracy`` endpoint mirrors the ``/latest``
    shape but reads from a different artifact dir. Same defensive
    contract: never 500, null-data on cold start."""

    def _write_accuracy(self, root: Path, niche_id: str, payload: dict) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        dest = root / "anticipation-accuracy"
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{stamp}-{niche_id}.json"
        path.write_text(json.dumps(payload))
        return path

    def test_returns_payload_when_measurement_exists(self, client, tmp_path):
        payload = {
            "niche_id": "gaming",
            "spearman_r": 0.62,
            "p_value": 0.03,
            "n_topics": 8,
            "artifact_date": "20260701",
            "measured_at": "2026-07-08T05:00:00+00:00",
            "reasons": [],
        }
        self._write_accuracy(tmp_path, "gaming", payload)
        resp = client.get("/api/v1/trend-anticipation/accuracy?niche_id=gaming")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["spearman_r"] == pytest.approx(0.62)
        assert data["n_topics"] == 8

    def test_data_null_when_no_measurement(self, client):
        resp = client.get("/api/v1/trend-anticipation/accuracy?niche_id=movies")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_400_on_invalid_niche(self, client):
        resp = client.get("/api/v1/trend-anticipation/accuracy?niche_id=notaniche")
        assert resp.status_code == 400
