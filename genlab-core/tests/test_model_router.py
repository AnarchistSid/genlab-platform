"""Tests for genlab_core.cost.model_router."""
import os
import tempfile
from unittest.mock import patch

import pytest
import yaml

from genlab_core.cost.model_router import get_model, _load_routing_config


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear lru_cache between tests."""
    _load_routing_config.cache_clear()
    yield
    _load_routing_config.cache_clear()


class TestGetModel:
    def test_hook_generation_returns_sonnet(self):
        assert "sonnet" in get_model("generate_hooks").lower()

    def test_script_generation_returns_gpt4o_mini(self):
        assert get_model("generate_script") == "gpt-4o-mini"

    def test_utility_task_returns_haiku(self):
        assert "haiku" in get_model("extract_game_name").lower()

    def test_unknown_task_returns_default(self):
        model = get_model("nonexistent_task_xyz")
        assert model == "gpt-4o-mini"  # default_model from YAML

    def test_budget_10pct_downgrades(self):
        model = get_model("generate_hooks", budget_ratio=0.15)
        assert model == "gpt-4o-mini"  # expensive_to_mid fallback

    def test_budget_25pct_uses_cheapest(self):
        model = get_model("generate_hooks", budget_ratio=0.30)
        assert "haiku" in model.lower()  # mid_to_cheapest fallback

    def test_config_not_found_uses_hardcoded_default(self):
        with patch.dict(os.environ, {"MODEL_ROUTING_CONFIG": "/nonexistent/path.yaml"}):
            _load_routing_config.cache_clear()
            model = get_model("generate_hooks")
            assert model == "gpt-4o-mini"

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
