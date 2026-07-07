"""Integration pin: /api/v1/top-creator-priors/{latest,uploads}.

Mission Control observability endpoint for the A + B intelligence
stack (2026-07-08). B.2's runner writes correlation artifacts weekly;
A.2's runner writes upload artifacts 4× daily. This endpoint mirrors
both for the Mission Control card.

Contract:
  1. 200 + payload when artifact for today exists
  2. 200 + payload from yesterday when today missing (fallback
     window)
  3. 200 + data=null when no artifact in fallback window
  4. 400 on invalid niche_id
  5. Malformed JSON → 200 + data=null (never crashes API)
  6. ``flag_enabled`` server-injected from the ENV — reflects real
     server state at request time
  7. ``artifact_stamp`` server-injected — YYYYMMDD of the file
     that was actually served (so frontend can render "as of X")
  8. Priors endpoint has 8-day fallback; uploads endpoint has
     1-day fallback (matches consumer semantics)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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


def _write_priors_artifact(
    root: Path,
    niche_id: str,
    correlations: dict,
    days_old: int = 0,
) -> Path:
    stamp = (datetime.now(UTC) - timedelta(days=days_old)).strftime("%Y%m%d")
    dest = root / "top-creator-priors"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{stamp}-{niche_id}.json"
    payload = {
        "generated_at": (datetime.now(UTC) - timedelta(days=days_old)).isoformat(),
        "niche_id": niche_id,
        "video_count": 24,
        "correlations": correlations,
    }
    path.write_text(json.dumps(payload))
    return path


def _write_uploads_artifact(
    root: Path,
    niche_id: str,
    creators: list,
    days_old: int = 0,
) -> Path:
    stamp = (datetime.now(UTC) - timedelta(days=days_old)).strftime("%Y%m%d")
    dest = root / "top-creator-uploads"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{stamp}-{niche_id}.json"
    payload = {
        "generated_at": (datetime.now(UTC) - timedelta(days=days_old)).isoformat(),
        "niche_id": niche_id,
        "creators": creators,
    }
    path.write_text(json.dumps(payload))
    return path


class TestGetLatest:
    def test_returns_todays_correlations(self, client, tmp_path):
        _write_priors_artifact(
            tmp_path,
            "gaming",
            {
                "title_length_chars": -0.15,
                "title_ends_with_question": 0.31,
                "description_has_timestamps": 0.28,
            },
        )
        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=gaming")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        data = body["data"]
        assert data is not None
        assert data["niche_id"] == "gaming"
        assert data["video_count"] == 24
        assert data["correlations"]["title_ends_with_question"] == pytest.approx(0.31)
        # Server-injected fields
        assert "flag_enabled" in data
        assert "artifact_stamp" in data
        assert data["artifact_stamp"] == datetime.now(UTC).strftime("%Y%m%d")

    def test_falls_back_within_8_days(self, client, tmp_path):
        """Weekly cadence — most days won't have today's artifact.
        Fall back to the most recent within 8 days."""
        _write_priors_artifact(
            tmp_path,
            "gaming",
            {"title_length_chars": 0.2},
            days_old=5,
        )
        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=gaming")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data is not None
        assert data["correlations"] == {"title_length_chars": 0.2}
        expected_stamp = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y%m%d")
        assert data["artifact_stamp"] == expected_stamp

    def test_beyond_8_days_returns_null(self, client, tmp_path):
        _write_priors_artifact(
            tmp_path,
            "gaming",
            {"title_length_chars": 0.2},
            days_old=10,
        )
        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=gaming")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["data"] is None
        assert "in last 8 days" in body["message"]

    def test_no_artifact_returns_null(self, client):
        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=gaming")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_invalid_niche_returns_400(self, client):
        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=nonexistent")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["status"] == "error"
        assert "Invalid niche_id" in body["message"]

    def test_malformed_json_returns_null(self, client, tmp_path):
        """Corrupted artifact today shouldn't shadow a good older
        one — the loop tries older stamps."""
        _write_priors_artifact(
            tmp_path,
            "gaming",
            {"title_length_chars": 0.2},
            days_old=3,
        )
        # Corrupt today's file
        today_stamp = datetime.now(UTC).strftime("%Y%m%d")
        dest = tmp_path / "top-creator-priors"
        (dest / f"{today_stamp}-gaming.json").write_text("not json {[")

        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=gaming")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        # Falls back to the 3-day-old good file
        assert data is not None
        assert data["correlations"] == {"title_length_chars": 0.2}

    def test_flag_enabled_reflects_env(self, client, tmp_path, monkeypatch):
        _write_priors_artifact(
            tmp_path,
            "gaming",
            {"title_length_chars": 0.2},
        )
        # Off state
        monkeypatch.delenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", raising=False)
        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=gaming")
        assert resp.get_json()["data"]["flag_enabled"] is False

        # On state
        monkeypatch.setenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", "true")
        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=gaming")
        assert resp.get_json()["data"]["flag_enabled"] is True


class TestGetUploads:
    def test_returns_todays_uploads(self, client, tmp_path):
        _write_uploads_artifact(
            tmp_path,
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "MKBHD",
                    "uploads": [
                        {
                            "video_id": "abc123",
                            "title": "Vision Pro Review",
                            "published_at": "2026-07-08T12:00:00Z",
                            "hours_since_publish": 2.0,
                        }
                    ],
                }
            ],
        )
        resp = client.get("/api/v1/top-creator-priors/uploads?niche_id=gaming")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data is not None
        assert data["niche_id"] == "gaming"
        assert len(data["creators"]) == 1
        assert data["creators"][0]["label"] == "MKBHD"
        assert "flag_enabled" in data
        assert data["artifact_stamp"] == datetime.now(UTC).strftime("%Y%m%d")

    def test_falls_back_to_yesterday(self, client, tmp_path):
        """A.2 fires 4× daily; uploads endpoint's fallback is just
        today or yesterday (older is stale enough that A.3's recency
        weight zeroes it)."""
        _write_uploads_artifact(
            tmp_path,
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "MKBHD",
                    "uploads": [],
                }
            ],
            days_old=1,
        )
        resp = client.get("/api/v1/top-creator-priors/uploads?niche_id=gaming")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data is not None
        expected_stamp = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y%m%d")
        assert data["artifact_stamp"] == expected_stamp

    def test_2_day_old_upload_returns_null(self, client, tmp_path):
        """A 2-day-old upload artifact intentionally doesn't surface
        — A.3's recency weight zeroes it, so showing it would
        mislead the operator about active-signal state."""
        _write_uploads_artifact(
            tmp_path,
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "MKBHD",
                    "uploads": [],
                }
            ],
            days_old=2,
        )
        resp = client.get("/api/v1/top-creator-priors/uploads?niche_id=gaming")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_invalid_niche_returns_400(self, client):
        resp = client.get("/api/v1/top-creator-priors/uploads?niche_id=bogus")
        assert resp.status_code == 400

    def test_uploads_flag_uses_different_env_var(self, client, tmp_path, monkeypatch):
        """Uploads flag_enabled reflects ``GENLAB_TOP_CREATORS_ENABLED``
        (A.2's flag), NOT ``GENLAB_TOP_CREATOR_PRIORS_ENABLED``
        (B.2's flag) — they're independent activation gates."""
        _write_uploads_artifact(
            tmp_path,
            "gaming",
            [
                {
                    "channel_id": "UC1234567890123456789012",
                    "label": "MKBHD",
                    "uploads": [],
                }
            ],
        )
        # A.2 on, B.2 off
        monkeypatch.setenv("GENLAB_TOP_CREATORS_ENABLED", "true")
        monkeypatch.delenv("GENLAB_TOP_CREATOR_PRIORS_ENABLED", raising=False)

        resp = client.get("/api/v1/top-creator-priors/uploads?niche_id=gaming")
        # Uploads endpoint reads GENLAB_TOP_CREATORS_ENABLED
        assert resp.get_json()["data"]["flag_enabled"] is True

        resp = client.get("/api/v1/top-creator-priors/latest?niche_id=gaming")
        # Priors endpoint reads GENLAB_TOP_CREATOR_PRIORS_ENABLED (off)
        # But there's no priors artifact so data is None — check just
        # that this works without cross-contamination:
        assert resp.status_code == 200


class TestEndpointRegistration:
    """Pin that both routes are actually registered on the app —
    prevents silent misregistration from breaking Mission Control."""

    def test_latest_route_registered(self):
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/v1/top-creator-priors/latest" in rules

    def test_uploads_route_registered(self):
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/v1/top-creator-priors/uploads" in rules
