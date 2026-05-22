"""Tests for SpliceReel Sprint 14 YAML configs."""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


class TestNicheConfig:
    def test_niche_id_is_movies(self):
        cfg = _load("niche.yaml")
        assert cfg["niche_id"] == "movies"

    def test_pipeline_has_19_enabled_stages(self):
        cfg = _load("niche.yaml")
        enabled = [s for s in cfg["pipeline"]["stages"] if s.get("enabled", True)]
        # 19 original + FetchTMDBTrailers (Sprint 64) + ExpressLane (Sprint 68)
        # + FetchRedditClips + AffiliateMatch (Wave 8 — source diversification)
        assert len(enabled) == 23

    def test_enabled_stages_reference_allowed_packages(self):
        cfg = _load("niche.yaml")
        enabled = [s for s in cfg["pipeline"]["stages"] if s.get("enabled", True)]
        allowed_prefixes = ("sr_strategies.", "genlab_core.")
        for stage in enabled:
            assert stage["class"].startswith(allowed_prefixes), f"Bad stage: {stage['class']}"

    def test_lifecycle_config_present(self):
        cfg = _load("niche.yaml")
        lc = cfg["freshness"]["film_lifecycle_modes"]
        assert lc["pre_release_days"] == 30
        assert lc["opening_weekend_hours"] == 72
        assert lc["long_tail_days"] == 21

    def test_decay_half_life_is_48h(self):
        cfg = _load("niche.yaml")
        assert cfg["freshness"]["decay_half_life_hours"] == 48.0


class TestScoringWeights:
    def test_weights_sum_to_one(self):
        cfg = _load("scoring_weights.yaml")
        weights = cfg["scoring_dimensions"]["weights"]
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01

    def test_franchise_multipliers_from_yaml_not_hardcoded(self):
        cfg = _load("scoring_weights.yaml")
        fm = cfg["franchise_multipliers"]
        assert "MCU" in fm
        assert fm["MCU"] == 1.5
        assert fm["default"] == 1.0

    def test_lifecycle_multipliers_present(self):
        cfg = _load("scoring_weights.yaml")
        lm = cfg["film_lifecycle_multipliers"]
        assert lm["opening_weekend"] == 1.6
        assert lm["long_tail"] == 0.7
        assert (
            lm["unknown"] == 1.0
        )  # Neutral — RSS items without lifecycle data shouldn't be penalized


class TestTemplates:
    def test_story_categories_have_formulas(self):
        cfg = _load("templates.yaml")
        cats = cfg["hooks"]["story_categories"]
        for name, cat in cats.items():
            assert "formulas" in cat, f"Category '{name}' missing formulas"
            assert len(cat["formulas"]) > 0

    def test_franchise_hashtags_defined(self):
        cfg = _load("templates.yaml")
        fh = cfg["captions"]["franchise_hashtags"]
        assert fh["MCU"] == "#MCU"
        assert fh["Star_Wars"] == "#StarWars"
