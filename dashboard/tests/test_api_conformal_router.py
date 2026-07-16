"""Integration pin: /api/v1/conformal-router/state.

L4 audit item (2026-07-08). Endpoint reads the conformal router's
per-niche state file and returns a card-friendly summary + current
flag state.

Pins:
1. Payload returned when state file exists (top-level shape has
   ``alpha`` + ``niches`` keys — the endpoint unwraps the nested
   dict)
2. ``ready`` badge derived from sample_count >= MIN_NICHE_SAMPLES
3. Weights + intercept NOT leaked (payload-size guard)
4. data.niches=[] when no state file (cold-start)
5. Malformed JSON on disk → 200 + niches=[] (never 500)
6. `flag_enabled` reflects the runtime env var (strict "1" check)
7. Niches returned alphabetically (deterministic UI)
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
    monkeypatch.setenv(
        "GENLAB_CONFORMAL_STATE_PATH",
        str(tmp_path / "conformal_router_state.json"),
    )
    monkeypatch.delenv("GENLAB_CONFORMAL_ROUTER_ENABLED", raising=False)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


# Feature-list mirrors what `fit_niche_state` writes at
# ``conformal_router.py:590-598``. Kept locally so the test doesn't
# import from genlab_core.
_EXPECTED_FEATURES = [
    "composite_score",
    "virality_score",
    "hook_classifier_score",
    "hook_text_len",
    "n_passed_checks",
    "n_failed_checks",
    "gate_confidence",
]


class TestGetState:
    def test_returns_summary_when_state_exists(self, client, tmp_path):
        state_path = tmp_path / "conformal_router_state.json"
        _write_state(
            state_path,
            {
                "alpha": 0.10,
                "niches": {
                    "gaming": {
                        "sample_count": 87,
                        "n_train": 45,
                        "n_calib": 42,
                        "alpha": 0.10,
                        "q_hat": 0.234,
                        "weights": [0.42, -0.11, 0.3, 0.05, 0.0, 0.1, 0.15],
                        "intercept": -0.5,
                        "feature_names": _EXPECTED_FEATURES,
                    }
                },
            },
        )
        resp = client.get("/api/v1/conformal-router/state")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["niches"]
        gaming = data["niches"][0]
        assert gaming["niche_id"] == "gaming"
        assert gaming["sample_count"] == 87
        assert gaming["q_hat"] == pytest.approx(0.234)
        assert gaming["ready"] is True

    def test_ready_badge_derived_from_min_samples(self, client, tmp_path):
        """Niches under the min-samples threshold are not "ready" —
        the router falls back to routing to operator regardless of
        q_hat. Pinning this so the card badge tells the truth."""
        state_path = tmp_path / "conformal_router_state.json"
        _write_state(
            state_path,
            {
                "alpha": 0.10,
                "niches": {
                    "gaming": {
                        "sample_count": 49,  # below MIN_NICHE_SAMPLES=50
                        "n_train": 25,
                        "n_calib": 24,
                        "alpha": 0.10,
                        "q_hat": 0.5,
                        "feature_names": _EXPECTED_FEATURES,
                    },
                    "movies": {
                        "sample_count": 50,  # exactly at threshold
                        "n_train": 25,
                        "n_calib": 25,
                        "alpha": 0.10,
                        "q_hat": 0.3,
                        "feature_names": _EXPECTED_FEATURES,
                    },
                },
            },
        )
        resp = client.get("/api/v1/conformal-router/state")
        by_id = {n["niche_id"]: n for n in resp.get_json()["data"]["niches"]}
        assert by_id["gaming"]["ready"] is False
        assert by_id["movies"]["ready"] is True

    def test_min_niche_samples_surfaced_in_payload(self, client):
        """The threshold used by the ``ready`` computation is surfaced
        so the frontend can render `sample_count/min_niche_samples`
        as a countdown without hardcoding the value."""
        resp = client.get("/api/v1/conformal-router/state")
        assert resp.get_json()["data"]["min_niche_samples"] == 50

    def test_weights_and_intercept_not_leaked(self, client, tmp_path):
        """Payload-size guard: weights + intercept are useful for a
        drill-down but bloat the summary. Not exposing them keeps the
        polled-every-60s card cheap."""
        state_path = tmp_path / "conformal_router_state.json"
        _write_state(
            state_path,
            {
                "alpha": 0.10,
                "niches": {
                    "gaming": {
                        "sample_count": 100,
                        "n_train": 50,
                        "n_calib": 50,
                        "alpha": 0.10,
                        "q_hat": 0.3,
                        "weights": [42.0],  # sentinel
                        "intercept": 999.0,
                        "feature_names": _EXPECTED_FEATURES,
                    }
                },
            },
        )
        gaming = client.get("/api/v1/conformal-router/state").get_json()["data"]["niches"][0]
        assert "weights" not in gaming
        assert "intercept" not in gaming

    def test_empty_niches_when_no_state(self, client):
        resp = client.get("/api/v1/conformal-router/state")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["niches"] == []

    def test_malformed_json_returns_empty_not_500(self, client, tmp_path):
        (tmp_path / "conformal_router_state.json").write_text("{not valid")
        resp = client.get("/api/v1/conformal-router/state")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["niches"] == []

    def test_flag_enabled_reflects_env_var_strict(self, client, monkeypatch):
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        resp = client.get("/api/v1/conformal-router/state")
        assert resp.get_json()["data"]["flag_enabled"] is True

        # Same strict "1" check as the module. "true" is disabled.
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "true")
        resp = client.get("/api/v1/conformal-router/state")
        assert resp.get_json()["data"]["flag_enabled"] is False

    def test_niches_returned_alphabetically(self, client, tmp_path):
        state_path = tmp_path / "conformal_router_state.json"
        _write_state(
            state_path,
            {
                "alpha": 0.10,
                "niches": {
                    "movies": {
                        "sample_count": 60,
                        "n_train": 30,
                        "n_calib": 30,
                        "alpha": 0.10,
                        "q_hat": 0.1,
                        "feature_names": [],
                    },
                    "anime": {
                        "sample_count": 60,
                        "n_train": 30,
                        "n_calib": 30,
                        "alpha": 0.10,
                        "q_hat": 0.1,
                        "feature_names": [],
                    },
                    "gaming": {
                        "sample_count": 60,
                        "n_train": 30,
                        "n_calib": 30,
                        "alpha": 0.10,
                        "q_hat": 0.1,
                        "feature_names": [],
                    },
                },
            },
        )
        niche_ids = [
            n["niche_id"]
            for n in client.get("/api/v1/conformal-router/state").get_json()["data"]["niches"]
        ]
        assert niche_ids == ["anime", "gaming", "movies"]
