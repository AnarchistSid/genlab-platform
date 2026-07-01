"""Integration pin: /api/v1/cross-niche-transfer/priors.

Intervention 2 observability (2026-07-01). Endpoint reads the artifact
written by the weekly ``scripts/refit_cross_niche_priors.py`` runner
and enriches with the current flag state so the frontend can badge
"observation only" vs "active".

Pins:
1. Payload returned when artifact exists
2. data=null when no artifact (cold-start)
3. Malformed JSON on disk → 200 + data=null (never 500s)
4. ``flag_enabled`` field reflects the runtime env var
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _write_priors(root: Path, payload: dict) -> Path:
    dest = root / "cross-niche-transfer"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "priors.json"
    path.write_text(json.dumps(payload))
    return path


class TestGetPriors:
    def test_returns_priors_when_artifact_exists(self, client, tmp_path):
        payload = {
            "generated_at": "2026-07-07T05:30:00+00:00",
            "priors": {
                "revelation": {
                    "style": "revelation",
                    "alpha": 2.4,
                    "beta": 13.6,
                    "observed_rate_mean": 0.15,
                    "observed_rate_variance": 0.02,
                    "n_niches": 3,
                    "reasons": ["effective_n=16.0"],
                }
            },
        }
        _write_priors(tmp_path, payload)
        resp = client.get("/api/v1/cross-niche-transfer/priors")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        data = body["data"]
        assert data is not None
        assert "revelation" in data["priors"]
        assert data["priors"]["revelation"]["alpha"] == pytest.approx(2.4)

    def test_data_null_when_no_artifact(self, client):
        resp = client.get("/api/v1/cross-niche-transfer/priors")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"] is None

    def test_malformed_json_returns_null_not_500(self, client, tmp_path):
        dest = tmp_path / "cross-niche-transfer"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "priors.json").write_text("{not valid")
        resp = client.get("/api/v1/cross-niche-transfer/priors")
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_flag_enabled_reflects_env_var(self, client, tmp_path, monkeypatch):
        """The endpoint enriches the payload with the CURRENT flag
        state so the frontend can badge "observation only" vs "active".
        This is what lets the operator see priors during observation
        mode without misreading them as "already in effect"."""
        _write_priors(
            tmp_path, {"generated_at": "now", "priors": {}}
        )

        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        resp = client.get("/api/v1/cross-niche-transfer/priors")
        assert resp.get_json()["data"]["flag_enabled"] is True

        monkeypatch.delenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", raising=False)
        resp = client.get("/api/v1/cross-niche-transfer/priors")
        assert resp.get_json()["data"]["flag_enabled"] is False
