"""Integration pin: /api/v1/counterfactual-replay/latest.

Intervention 7 observability (2026-07-01). Endpoint globs
``replay-*-<niche>.json`` in ``$GENLAB_TMP/counterfactual-replay/``,
picks the newest by mtime, returns its JSON verbatim.

Pins:
1. Payload returned when a matching artifact exists
2. Newest artifact wins when multiple exist (mtime sort)
3. Niche suffix isolation — a ``replay-*-all.json`` doesn't leak
   into a specific-niche query
4. data=null when no artifact matches
5. 400 on invalid niche_id
6. Malformed JSON returns null, not 500
"""

from __future__ import annotations

import json
import os
import sys
import time
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


def _write_artifact(root: Path, filename: str, payload: dict) -> Path:
    dest = root / "counterfactual-replay"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / filename
    path.write_text(json.dumps(payload))
    return path


class TestGetLatest:
    def test_returns_payload(self, client, tmp_path):
        payload = {
            "generated_at": "2026-07-01T04:30:00+00:00",
            "niche": "gaming",
            "window_days": 30,
            "n_decisions_total": 42,
            "dr_enabled": False,
            "per_arm": [
                {
                    "arm_id": "style:gaming:revelation",
                    "n_decisions": 20,
                    "n_with_reward": 18,
                    "ips_reward": 0.32,
                    "naive_reward": 0.30,
                    "relative_lift": 0.07,
                    "ess": 15.2,
                    "dr_reward": None,
                    "dr_note": "flag off (default stub)",
                }
            ],
        }
        _write_artifact(tmp_path, "replay-20260701-043000-gaming.json", payload)
        resp = client.get(
            "/api/v1/counterfactual-replay/latest?niche_id=gaming"
        )
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data is not None
        assert data["niche"] == "gaming"
        assert len(data["per_arm"]) == 1

    def test_newest_wins_by_mtime(self, client, tmp_path):
        # Two artifacts for the same niche; touch the newer one's mtime
        # so we can assert deterministically which comes back.
        p_old = _write_artifact(
            tmp_path,
            "replay-20260601-043000-gaming.json",
            {"generated_at": "2026-06-01", "niche": "gaming", "n_decisions_total": 1},
        )
        p_new = _write_artifact(
            tmp_path,
            "replay-20260701-043000-gaming.json",
            {"generated_at": "2026-07-01", "niche": "gaming", "n_decisions_total": 99},
        )
        # Force chronological mtime ordering — the filesystem may
        # otherwise write with the same second on fast machines.
        os.utime(p_old, (time.time() - 3600, time.time() - 3600))
        os.utime(p_new, (time.time(), time.time()))

        resp = client.get(
            "/api/v1/counterfactual-replay/latest?niche_id=gaming"
        )
        data = resp.get_json()["data"]
        assert data["n_decisions_total"] == 99

    def test_niche_suffix_isolated(self, client, tmp_path):
        """A ``replay-*-all.json`` must NOT surface for a specific
        niche query. Otherwise ``niche_id=gaming`` would return a
        cross-niche aggregate on top of a real gaming-only run."""
        _write_artifact(
            tmp_path,
            "replay-20260701-043000-all.json",
            {"niche": "all", "n_decisions_total": 500},
        )
        # No gaming-specific artifact
        resp = client.get(
            "/api/v1/counterfactual-replay/latest?niche_id=gaming"
        )
        assert resp.get_json()["data"] is None

    def test_data_null_when_no_artifact(self, client):
        resp = client.get(
            "/api/v1/counterfactual-replay/latest?niche_id=movies"
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None

    def test_400_on_invalid_niche(self, client):
        resp = client.get(
            "/api/v1/counterfactual-replay/latest?niche_id=notaniche"
        )
        assert resp.status_code == 400

    def test_malformed_json_returns_null_not_500(self, client, tmp_path):
        dest = tmp_path / "counterfactual-replay"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "replay-20260701-043000-gaming.json").write_text("{corrupt")
        resp = client.get(
            "/api/v1/counterfactual-replay/latest?niche_id=gaming"
        )
        assert resp.status_code == 200
        assert resp.get_json()["data"] is None
