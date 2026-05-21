"""Tests for genlab_core.cost.model_router."""

import os
import tempfile
from unittest.mock import patch

import pytest
import yaml
from genlab_core.cost.model_router import _load_routing_config, get_model


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear lru_cache between tests."""
    _load_routing_config.cache_clear()
    yield
    _load_routing_config.cache_clear()


class TestGetModel:
    def test_hook_generation_returns_valid_model(self):
        model = get_model("generate_hooks")
        # Hook generation uses Haiku for cost efficiency
        assert "haiku" in model.lower() or "sonnet" in model.lower()

    def test_script_generation_returns_haiku(self):
        assert "haiku" in get_model("generate_script").lower()

    def test_utility_task_returns_haiku(self):
        assert "haiku" in get_model("extract_game_name").lower()

    def test_unknown_task_returns_default(self):
        model = get_model("nonexistent_task_xyz")
        assert "haiku" in model.lower()  # default_model from YAML

    def test_budget_10pct_downgrades(self):
        model = get_model("generate_hooks", budget_ratio=0.15)
        assert "haiku" in model.lower()  # expensive_to_mid fallback

    def test_budget_25pct_uses_cheapest(self):
        model = get_model("generate_hooks", budget_ratio=0.30)
        assert "haiku" in model.lower()  # mid_to_cheapest fallback

    def test_config_not_found_uses_hardcoded_default(self):
        with patch.dict(os.environ, {"MODEL_ROUTING_CONFIG": "/nonexistent/path.yaml"}):
            _load_routing_config.cache_clear()
            model = get_model("generate_hooks")
            assert model is not None  # returns hardcoded default

    def test_yaml_override_respected(self):
        custom_config = {
            "default_model": "custom-model",
            "task_routing": {"my_task": "special-model-v2"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(custom_config, f)
            f.flush()
            with patch.dict(os.environ, {"MODEL_ROUTING_CONFIG": f.name}):
                _load_routing_config.cache_clear()
                assert get_model("my_task") == "special-model-v2"
                assert get_model("unknown") == "custom-model"
        os.unlink(f.name)
