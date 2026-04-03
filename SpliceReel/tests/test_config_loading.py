"""Tests for SpliceReel config loading."""

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
    @pytest.mark.parametrize("config_file", CONFIG_FILES)
    def test_config_file_loads(self, config_file):
        data = _load(config_file)
        assert isinstance(data, dict), f"{config_file} did not parse as dict"

    def test_niche_yaml_required_keys(self):
        data = _load("niche.yaml")
        assert data["niche_id"] == "movies"
        assert data["display_name"] == "SpliceReel"
        assert data["accent_color"] == "#C9A84C"
        assert "brand_voice" in data
        assert "feature_flags" in data

    def test_sources_has_tmdb(self):
        data = _load("sources.yaml")
        tier_1_names = [s["name"] for s in data["tier_1"]["sources"]]
        assert any("TMDB" in n for n in tier_1_names), "TMDB not found in tier_1 sources"

    def test_sources_has_three_tiers(self):
        data = _load("sources.yaml")
        assert "tier_1" in data
        assert "tier_2" in data
        assert "tier_3" in data

    def test_scoring_weights_sum(self):
        data = _load("scoring_weights.yaml")
        weights = data["scoring_dimensions"]["weights"]
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01, f"Weights sum to {total}, expected 1.0"

    def test_franchise_multipliers_present(self):
        data = _load("scoring_weights.yaml")
        mults = data["franchise_multipliers"]
        assert "MCU" in mults
        assert "DC" in mults
        assert mults["MCU"] > mults["default"]

    def test_publishing_has_all_platforms(self):
        data = _load("publishing.yaml")
        platforms = data["platforms"]
        for p in ["instagram", "youtube", "x", "facebook", "tiktok", "threads"]:
            assert p in platforms, f"Missing platform: {p}"
