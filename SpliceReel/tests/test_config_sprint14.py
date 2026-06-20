"""Tests for SpliceReel Sprint 14 YAML configs."""

from pathlib import Path

import yaml
from genlab_core.niche_loader import load_niche_config

NICHE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = NICHE_ROOT / "config"


def _load(name: str) -> dict:
    """Raw YAML loader for non-niche.yaml files (sources/visuals/templates etc.)"""
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def _load_niche() -> dict:
    """Load niche.yaml via the niche_loader so the P4 pipeline_template
    merge (pipeline.inject → pipeline.stages) runs. Direct yaml.safe_load
    of the raw niche.yaml file no longer contains pipeline.stages after
    the P4 migration — only pipeline.inject + pipeline_template: backbone.
    """
    return load_niche_config("movies", NICHE_ROOT)


class TestNicheConfig:
    def test_niche_id_is_movies(self):
        cfg = _load_niche()
        assert cfg["niche_id"] == "movies"

    def test_required_stages_are_enabled(self):
        """The movie pipeline must have its load-bearing stages enabled.

        Asserting *which* stages are present (rather than *how many*) means
        adding a new stage doesn't break this test — only removing or
        disabling a required one does. The reverse used to bite hard: 4
        unrelated PRs in one week needed a count-bump just because a stage
        was added or re-enabled.
        """
        cfg = _load_niche()
        enabled_classes = {s["class"] for s in cfg["pipeline"]["stages"] if s.get("enabled", True)}
        required = {
            # Fetch + score
            "genlab_core.pipeline.stages.express_lane.ExpressLane",
            "genlab_core.media.trending_video_fetcher.FetchTrendingVideos",
            "sr_strategies.scoring.MovieScoringStrategy",
            # Video gate + content
            "genlab_core.pipeline.stages.video_gate.VideoGate",
            "sr_strategies.writing.MovieWritingStrategy",
            "sr_strategies.hooks.MovieHookStrategy",
            # Render → validate → push
            "sr_strategies.visual_render.MovieVisualRenderStrategy",
            "genlab_core.pipeline.stages.render_whisper_captions.RenderWhisperCaptions",
            "genlab_core.pipeline.stages.validate_videos.ValidateVideos",
            "genlab_core.pipeline.stages.push_to_backlog.PushToBacklog",
            # Analytics tail
            "genlab_core.pipeline.stages.fetch_insights.FetchInsights",
            "genlab_core.pipeline.stages.run_report.RunReport",
        }
        missing = required - enabled_classes
        assert not missing, f"required stages missing/disabled in niche.yaml: {missing}"

    def test_enabled_stages_reference_allowed_packages(self):
        cfg = _load_niche()
        enabled = [s for s in cfg["pipeline"]["stages"] if s.get("enabled", True)]
        allowed_prefixes = ("sr_strategies.", "genlab_core.")
        for stage in enabled:
            assert stage["class"].startswith(allowed_prefixes), f"Bad stage: {stage['class']}"

    def test_lifecycle_config_present(self):
        cfg = _load_niche()
        lc = cfg["freshness"]["film_lifecycle_modes"]
        assert lc["pre_release_days"] == 30
        assert lc["opening_weekend_hours"] == 72
        assert lc["long_tail_days"] == 21

    def test_decay_half_life_is_48h(self):
        cfg = _load_niche()
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
