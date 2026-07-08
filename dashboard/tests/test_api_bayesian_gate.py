"""Integration pin: /api/v1/bayesian-gate/state.

L4 audit item (2026-07-08). Endpoint reads the Bayesian gate's per-
niche posterior state file and returns a card-friendly summary +
current flag state.

Pins:
1. Payload returned when state file exists
2. data.niches=[] when no state file (cold-start)
3. Malformed JSON on disk → 200 + niches=[] (never 500)
4. `flag_enabled` reflects the runtime env var
5. Covariance matrix is NOT returned (payload-size guard)
6. Length-mismatch (len(mean) != len(feature_names)) is filtered
7. Niches are returned in alphabetical order (deterministic UI)
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
    # BAYESIAN_GATE_STATE_PATH is honoured by both the endpoint and
    # the module — safe to point at a scratch file.
    monkeypatch.setenv(
        "BAYESIAN_GATE_STATE_PATH", str(tmp_path / "bayesian_gate_state.json")
    )
    # Ensure a clean env for flag checks; individual tests set as needed.
    monkeypatch.delenv("GENLAB_BAYESIAN_GATE_ENABLED", raising=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


class TestGetState:
    def test_returns_summary_when_state_exists(self, client, tmp_path):
        state_path = tmp_path / "bayesian_gate_state.json"
        _write_state(
            state_path,
            {
                "gaming": {
                    "niche_id": "gaming",
                    "mean": [0.42, -0.11, 0.3, 0.05, 0.0, 0.1],
                    "cov": [[0.0] * 6 for _ in range(6)],  # will be dropped
                    "n_rows": 47,
                    "feature_names": [
                        "composite_score",
                        "virality_score",
                        "hook_classifier_score",
                        "hook_text_len",
                        "n_passed_checks",
                        "n_failed_checks",
                    ],
                }
            },
        )
        resp = client.get("/api/v1/bayesian-gate/state")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        data = body["data"]
        assert data is not None
        assert data["niches"], "expected at least one niche summary"
        gaming = data["niches"][0]
        assert gaming["niche_id"] == "gaming"
        assert gaming["n_rows"] == 47
        assert len(gaming["mean"]) == 6

    def test_covariance_is_not_leaked(self, client, tmp_path):
        """Payload-size guard: the covariance matrix is O(N^2) per
        niche and provides no operator value in the summary. Locking
        this in prevents a well-meaning future edit from bloating
        the response.
        """
        state_path = tmp_path / "bayesian_gate_state.json"
        _write_state(
            state_path,
            {
                "gaming": {
                    "niche_id": "gaming",
                    "mean": [0.5],
                    "cov": [[42.0]],  # sentinel: MUST NOT appear in response
                    "n_rows": 10,
                    "feature_names": ["composite_score"],
                }
            },
        )
        resp = client.get("/api/v1/bayesian-gate/state")
        gaming = resp.get_json()["data"]["niches"][0]
        assert "cov" not in gaming
        assert "covariance" not in gaming

    def test_length_mismatch_filtered(self, client, tmp_path):
        """A malformed niche entry (mean length != feature_names
        length) is silently filtered — the endpoint MUST NOT expose
        broken shapes that would render as unlabeled weight numbers."""
        state_path = tmp_path / "bayesian_gate_state.json"
        _write_state(
            state_path,
            {
                "gaming": {
                    "niche_id": "gaming",
                    "mean": [0.5, 0.3, 0.1],  # 3 weights...
                    "cov": [],
                    "n_rows": 10,
                    "feature_names": ["only_one"],  # ...for 1 name
                },
                "movies": {
                    "niche_id": "movies",
                    "mean": [0.2],
                    "cov": [],
                    "n_rows": 5,
                    "feature_names": ["composite_score"],
                },
            },
        )
        resp = client.get("/api/v1/bayesian-gate/state")
        niches = resp.get_json()["data"]["niches"]
        niche_ids = [n["niche_id"] for n in niches]
        assert "gaming" not in niche_ids
        assert "movies" in niche_ids

    def test_empty_niches_when_no_state(self, client):
        resp = client.get("/api/v1/bayesian-gate/state")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["niches"] == []

    def test_malformed_json_returns_empty_not_500(self, client, tmp_path):
        (tmp_path / "bayesian_gate_state.json").write_text("{not valid")
        resp = client.get("/api/v1/bayesian-gate/state")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["niches"] == []

    def test_flag_enabled_reflects_env_var(self, client, monkeypatch):
        monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", "1")
        resp = client.get("/api/v1/bayesian-gate/state")
        assert resp.get_json()["data"]["flag_enabled"] is True

        # Strict "1" check: "true" is NOT enabled. Mirrors module
        # behavior — locked in as a pin so a well-meaning future
        # loosening of the flag parser doesn't ship without updating
        # this test + the sibling case-sensitivity guard.
        monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", "true")
        resp = client.get("/api/v1/bayesian-gate/state")
        assert resp.get_json()["data"]["flag_enabled"] is False

        monkeypatch.delenv("GENLAB_BAYESIAN_GATE_ENABLED")
        resp = client.get("/api/v1/bayesian-gate/state")
        assert resp.get_json()["data"]["flag_enabled"] is False

    def test_niches_returned_alphabetically(self, client, tmp_path):
        """Stable ordering matters for React Query cache identity —
        if the endpoint reordered niches, the frontend would perceive
        every poll as new data and re-render even when nothing changed."""
        state_path = tmp_path / "bayesian_gate_state.json"
        _write_state(
            state_path,
            {
                "movies": {
                    "niche_id": "movies",
                    "mean": [0.1],
                    "cov": [],
                    "n_rows": 1,
                    "feature_names": ["x"],
                },
                "anime": {
                    "niche_id": "anime",
                    "mean": [0.1],
                    "cov": [],
                    "n_rows": 1,
                    "feature_names": ["x"],
                },
                "gaming": {
                    "niche_id": "gaming",
                    "mean": [0.1],
                    "cov": [],
                    "n_rows": 1,
                    "feature_names": ["x"],
                },
            },
        )
        resp = client.get("/api/v1/bayesian-gate/state")
        niche_ids = [n["niche_id"] for n in resp.get_json()["data"]["niches"]]
        assert niche_ids == ["anime", "gaming", "movies"]
