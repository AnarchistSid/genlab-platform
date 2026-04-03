"""Tests for ClutchWire config loading."""

from pathlib import Path

import pytest
import yaml

NICHE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = NICHE_ROOT / "config"

CONFIG_FILES = [
    "niche.yaml",
    "sources.yaml",
    "scoring_weights.yaml",
    "templates.yaml",
    "schedule.yaml",
    "monetization.yaml",
    "publishing.yaml",
]


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


class TestConfigLoading:
    """Verify all YAML configs load without error and have required keys."""

    @pytest.mark.parametrize("config_file", CONFIG_FILES)
    def test_config_file_loads(self, config_file):
        data = _load(config_file)
        assert isinstance(data, dict), f"{config_file} did not parse as dict"

    def test_niche_yaml_required_keys(self):
        data = _load("niche.yaml")
        assert data["niche_id"] == "sports"
        assert data["display_name"] == "ClutchWire"
        assert data["accent_color"] == "#FF2040"
        assert "brand_voice" in data
        assert "feature_flags" in data

    def test_sources_yaml_has_tiers(self):
        data = _load("sources.yaml")
        assert "tier_1" in data
        assert "tier_2" in data
        assert "tier_3" in data
        assert len(data["tier_1"]["sources"]) >= 1

    def test_scoring_weights_sum(self):
        data = _load("scoring_weights.yaml")
        weights = data["clip_scoring"]["weights"]
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01, f"Weights sum to {total}, expected 1.0"

    def test_templates_has_hook_formulas(self):
        data = _load("templates.yaml")
        formulas = data["hooks"]["formulas"]
        assert len(formulas) >= 3

    def test_schedule_has_publishing_windows(self):
        data = _load("schedule.yaml")
        assert len(data["publishing_windows"]) >= 1

    def test_publishing_has_all_platforms(self):
        data = _load("publishing.yaml")
        platforms = data["platforms"]
        for p in ["instagram", "youtube", "x", "facebook", "tiktok", "threads"]:
            assert p in platforms, f"Missing platform: {p}"
