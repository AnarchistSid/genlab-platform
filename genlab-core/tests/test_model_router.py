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

    def test_script_generation_routes_to_bulk_model(self):
        # Bulk/internal tasks route to GPT-4o-mini (≈85% cheaper than
        # Haiku) per the cost-optimization routing config.
        assert get_model("generate_script") == "gpt-4o-mini"

    def test_utility_task_routes_to_cheapest(self):
        # Utility extraction routes to the cheapest tier (gpt-4.1-nano).
        assert get_model("extract_game_name") == "gpt-4.1-nano"

    def test_unknown_task_returns_default(self):
        model = get_model("nonexistent_task_xyz")
        assert "haiku" in model.lower()  # default_model from YAML

    def test_budget_10pct_downgrades_to_mid(self):
        # At low budget, the creative-tier Haiku task downgrades to the
        # mid (bulk) model rather than burning premium spend.
        model = get_model("generate_hooks", budget_ratio=0.15)
        assert model == "gpt-4o-mini"

    def test_budget_25pct_uses_cheapest(self):
        # Even tighter budget → cheapest model.
        model = get_model("generate_hooks", budget_ratio=0.30)
        assert model == "gpt-4.1-nano"

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
