"""Tests for /api/v1/learning endpoints — bandit state and hook classifier status."""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestBanditState:
    """GET /api/v1/learning/status — comprehensive learning loop status.

    Replaces the older /bandit-state endpoint. The dashboard learning view
    now consumes a single endpoint that bundles bandit arms with the rest
    of the feedback-pipeline metrics, so tests target /status directly.
    """

    def _fake_sync_client(self, arms_data):
        """Build a fake SyncBacklogClient whose .bandit_arms.all() yields
        the given arm dicts (alpha/beta/niche_id/arm_id shape)."""
        client = MagicMock()
        client.bandit_arms.all = MagicMock(return_value=arms_data)
        return client

    def test_bandit_state_returns_json(self, client):
        """Bandit state surfaces alpha/beta/n_plays/mean per niche."""
        import server.api.learning as lmod

        # Clear cache
        lmod._status_cache["data"] = None
        lmod._status_cache["ts"] = 0.0

        fake_arms = [
            {"fields": {"niche_id": "gaming", "arm_id": "hook_dramatic", "alpha": 10.0, "beta": 5.0}},
            {"fields": {"niche_id": "gaming", "arm_id": "hook_question", "alpha": 3.0, "beta": 7.0}},
        ]

        with (
            patch("server.core.graph_sync.get_sync_client",
                  return_value=self._fake_sync_client(fake_arms)),
            patch("server.api.learning._learning_aggregates", return_value={
                "feedback_by_status": {}, "rewards_count": 0, "avg_reward": 0.0,
                "max_reward": 0.0, "analytics_count": 0,
                "config_update_threshold": 100,
                "config_update_progress": 0,
                "niches_at_config_quota": [],
            }),
        ):
            resp = client.get("/api/v1/learning/status")

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "bandit_arms" in data
        assert "gaming" in data["bandit_arms"]
        arms = {a["arm_id"]: a for a in data["bandit_arms"]["gaming"]}
        assert arms["hook_dramatic"]["alpha"] == 10.0
        assert arms["hook_dramatic"]["beta"] == 5.0
        assert arms["hook_dramatic"]["mean"] == round(10.0 / 15.0, 4)
        assert arms["hook_question"]["alpha"] == 3.0
        assert arms["hook_question"]["beta"] == 7.0

    def test_bandit_state_uses_cache(self, client):
        """Second call within TTL returns cached data without re-querying."""
        import server.api.learning as lmod

        fake_arms = [
            {"fields": {"niche_id": "gaming", "arm_id": "cached_arm", "alpha": 5.0, "beta": 5.0}},
        ]
        fake_client = self._fake_sync_client(fake_arms)

        # Clear cache
        lmod._status_cache["data"] = None
        lmod._status_cache["ts"] = 0.0

        with (
            patch("server.core.graph_sync.get_sync_client", return_value=fake_client),
            patch("server.api.learning._learning_aggregates", return_value={
                "feedback_by_status": {}, "rewards_count": 0, "avg_reward": 0.0,
                "max_reward": 0.0, "analytics_count": 0,
                "config_update_threshold": 100,
                "config_update_progress": 0,
                "niches_at_config_quota": [],
            }),
        ):
            resp1 = client.get("/api/v1/learning/status")
            resp2 = client.get("/api/v1/learning/status")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # bandit_arms.all should only be called once (cached on second call)
        assert fake_client.bandit_arms.all.call_count == 1

    def test_bandit_state_handles_error_gracefully(self, client):
        """When the sync client raises, endpoint returns 500 with error message."""
        import server.api.learning as lmod

        # Clear cache so error path is tested
        lmod._status_cache["data"] = None
        lmod._status_cache["ts"] = 0.0

        with patch("server.core.graph_sync.get_sync_client",
                   side_effect=RuntimeError("backlog unreachable")):
            resp = client.get("/api/v1/learning/status")

        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data or data.get("status") == "error"


class TestHookClassifierStatus:
    """GET /api/v1/learning/hook-classifier-status"""

    def test_hook_classifier_status_returns_all_niches(self, client):
        """Returns trained status for all expected niches (all untrained when no models dir)."""
        import server.api.learning as lmod

        # Point _MODEL_DIR at a non-existent path so all niches report untrained
        with patch.object(lmod, "_MODEL_DIR", Path("/tmp/nonexistent_models_dir")):
            resp = client.get("/api/v1/learning/hook-classifier-status")

        assert resp.status_code == 200
        data = resp.get_json()["data"]

        expected_niches = ["ai_creators", "gaming", "sports", "movies", "anime"]
        for niche_id in expected_niches:
            assert niche_id in data, f"Missing niche: {niche_id}"
            assert data[niche_id]["trained"] is False

    def test_hook_classifier_trained_niche(self, client, tmp_path):
        """When a meta file exists, niche reports trained=True with stats."""
        meta = {
            "n_examples": 200,
            "pos_rate": 0.35,
            "feature_names": ["f1", "f2", "f3"],
        }
        meta_file = tmp_path / "hook_classifier_gaming.meta.json"
        meta_file.write_text(json.dumps(meta))

        import server.api.learning as lmod

        with patch.object(lmod, "_MODEL_DIR", tmp_path):
            resp = client.get("/api/v1/learning/hook-classifier-status")

        assert resp.status_code == 200
        data = resp.get_json()["data"]

        assert data["gaming"]["trained"] is True
        assert data["gaming"]["n_examples"] == 200
        assert data["gaming"]["pos_rate"] == 0.35
        assert data["gaming"]["n_features"] == 3

        # Untrained niches
        assert data["sports"]["trained"] is False
        assert data["ai_creators"]["trained"] is False
