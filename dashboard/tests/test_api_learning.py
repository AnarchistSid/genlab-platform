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
    """GET /api/v1/learning/bandit-state"""

    def test_bandit_state_returns_json(self, client):
        """Bandit state returns arm alpha/beta/expected_reward per niche."""
        import server.api.learning as lmod

        # Clear cache
        lmod._bandit_cache["data"] = None
        lmod._bandit_cache["ts"] = 0.0

        fake_arms = {
            "hook_dramatic": (10.0, 5.0),
            "hook_question": (3.0, 7.0),
        }

        # Build fake modules for lazy imports inside the endpoint
        fake_arm_loader = types.ModuleType("genlab_core.learning.arm_loader")
        fake_arm_loader.load_all_arms = MagicMock(return_value=fake_arms)
        fake_arm_loader.BANDIT_LIST_NAMES = ["gaming"]

        fake_backlog_mod = types.ModuleType("genlab_core.http.backlog_client")
        fake_backlog_mod.BacklogClient = MagicMock

        with patch.dict("sys.modules", {
            "genlab_core.learning.arm_loader": fake_arm_loader,
            "genlab_core.http.backlog_client": fake_backlog_mod,
        }), patch("server.core.graph_sync.get_sync_client", return_value=MagicMock()):
            resp = client.get("/api/v1/learning/bandit-state")

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "gaming" in data
        assert "hook_dramatic" in data["gaming"]
        arm = data["gaming"]["hook_dramatic"]
        assert arm["alpha"] == 10.0
        assert arm["beta"] == 5.0
        assert arm["expected_reward"] == round(10.0 / 15.0, 4)

        # Check second arm too
        q_arm = data["gaming"]["hook_question"]
        assert q_arm["alpha"] == 3.0
        assert q_arm["beta"] == 7.0

    def test_bandit_state_uses_cache(self, client):
        """E1: Second call within TTL returns cached data (no extra SP roundtrips)."""
        import server.api.learning as lmod

        fake_arms = {"cached_arm": (5.0, 5.0)}
        fake_arm_loader = types.ModuleType("genlab_core.learning.arm_loader")
        fake_arm_loader.load_all_arms = MagicMock(return_value=fake_arms)
        fake_arm_loader.BANDIT_LIST_NAMES = ["gaming"]

        fake_backlog_mod = types.ModuleType("genlab_core.http.backlog_client")
        fake_backlog_mod.BacklogClient = MagicMock

        # Clear cache
        lmod._bandit_cache["data"] = None
        lmod._bandit_cache["ts"] = 0.0

        with patch.dict("sys.modules", {
            "genlab_core.learning.arm_loader": fake_arm_loader,
            "genlab_core.http.backlog_client": fake_backlog_mod,
        }), patch("server.core.graph_sync.get_sync_client", return_value=MagicMock()):
            resp1 = client.get("/api/v1/learning/bandit-state")
            resp2 = client.get("/api/v1/learning/bandit-state")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # load_all_arms should only be called once (cached on second call)
        assert fake_arm_loader.load_all_arms.call_count == 1

    def test_bandit_state_handles_error_gracefully(self, client):
        """When arm_loader import fails, endpoint returns 500 with error message."""
        import server.api.learning as lmod

        # Clear cache so error path is tested
        lmod._bandit_cache["data"] = None
        lmod._bandit_cache["ts"] = 0.0

        # Setting module to None in sys.modules causes ImportError on import
        with patch.dict("sys.modules", {
            "genlab_core.learning.arm_loader": None,
        }):
            resp = client.get("/api/v1/learning/bandit-state")

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
