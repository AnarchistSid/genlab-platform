"""Tests for FrameDrift Sprint 15 YAML configs."""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


class TestNicheConfig:
    def test_niche_id_is_anime(self):
        cfg = _load("niche.yaml")
        assert cfg["niche_id"] == "anime"

    def test_accent_color_is_sakura_pink(self):
        cfg = _load("niche.yaml")
        assert cfg["accent_color"] == "#7B3FE4"

    def test_pipeline_has_19_enabled_stages(self):
        cfg = _load("niche.yaml")
        enabled = [s for s in cfg["pipeline"]["stages"] if s.get("enabled", True)]
        # 19 original + FetchAnimePromos (Sprint 64) + ExpressLane (Sprint 68)
        # + FetchRedditClips + AffiliateMatch (Wave 8 — source diversification)
        assert len(enabled) == 23

    def test_enabled_stages_reference_allowed_packages(self):
        cfg = _load("niche.yaml")
        enabled = [s for s in cfg["pipeline"]["stages"] if s.get("enabled", True)]
        allowed_prefixes = ("fd_strategies.", "genlab_core.")
        for stage in enabled:
            assert stage["class"].startswith(allowed_prefixes), f"Bad stage: {stage['class']}"

    def test_trend_cycle_config_present(self):
        cfg = _load("niche.yaml")
        tc = cfg["freshness"]["trend_cycle_modes"]
        assert tc["emerging_window_hours"] == 24
        assert tc["peak_window_hours"] == 72
        assert tc["declining_threshold_hours"] == 96

    def test_decay_half_life_is_24h(self):
        cfg = _load("niche.yaml")
        assert cfg["freshness"]["decay_half_life_hours"] == 24.0

    def test_brand_sensitivity_protected_brands(self):
        cfg = _load("niche.yaml")
        brands = cfg["brand_sensitivity"]["protected_brands"]
        assert "Studio Ghibli" in brands
        assert "MAPPA" in brands


class TestScoringWeights:
    def test_weights_sum_to_one(self):
        cfg = _load("scoring_weights.yaml")
        weights = cfg["scoring_dimensions"]["weights"]
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01

    def test_trend_cycle_multipliers_present(self):
        cfg = _load("scoring_weights.yaml")
        tm = cfg["trend_cycle_multipliers"]
        assert tm["emerging"] == 1.4
        assert tm["declining"] == 0.5

    def test_voice_actor_multiplier(self):
        cfg = _load("scoring_weights.yaml")
        assert cfg["scoring_dimensions"]["magnitude"]["voice_actor_multiplier"] == 1.9


class TestSources:
    def test_has_three_tiers(self):
        cfg = _load("sources.yaml")
        assert "tier_1" in cfg
        assert "tier_2" in cfg
        assert "tier_3" in cfg

    def test_all_sources_are_rss(self):
        cfg = _load("sources.yaml")
        for tier_name in ["tier_1", "tier_2", "tier_3"]:
            for source in cfg[tier_name]["sources"]:
                assert source["type"] == "rss"

    def test_pexels_queries_have_no_brand_names(self):
        cfg = _load("sources.yaml")
        queries = cfg["media"]["pexels"]["anime_queries"]
        protected = {"nike", "adidas", "gucci", "louis vuitton", "chanel", "prada", "supreme"}
        for q in queries:
            for brand in protected:
                assert brand not in q.lower(), f"Brand '{brand}' found in query: {q}"
