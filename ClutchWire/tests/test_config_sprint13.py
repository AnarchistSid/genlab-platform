"""Tests for Sprint 13 YAML config updates."""

from pathlib import Path

import yaml

NICHE_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = NICHE_ROOT / "config"


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


class TestNichePipelineStages:
    def test_pipeline_stages_not_empty(self):
        data = _load("niche.yaml")
        stages = data["pipeline"]["stages"]
        assert len(stages) >= 6

    def test_all_stages_have_class(self):
        data = _load("niche.yaml")
        for stage in data["pipeline"]["stages"]:
            assert "class" in stage

    def test_enabled_stages_reference_allowed_packages(self):
        data = _load("niche.yaml")
        enabled = [s for s in data["pipeline"]["stages"] if s.get("enabled", True)]
        allowed_prefixes = ("cw_strategies.", "genlab_core.")
        for stage in enabled:
            assert stage["class"].startswith(allowed_prefixes), f"Bad stage: {stage['class']}"


class TestSourcesESPNAPI:
    def test_espn_api_scoreboard_removed(self):
        """Scoreboard removed in Sprint 61 (0% conversion rate)."""
        data = _load("sources.yaml")
        assert "scoreboard" not in data["espn_api"]

    def test_espn_api_news_endpoints(self):
        data = _load("sources.yaml")
        news = data["espn_api"]["news"]
        assert "nba" in news
        assert "nfl" in news


class TestScoringDecay:
    def test_half_life_is_two_hours(self):
        data = _load("scoring_weights.yaml")
        half_life = data["clip_scoring"]["recency"]["half_life_hours"]
        assert half_life == 2.0

    def test_live_event_bonus_present(self):
        data = _load("scoring_weights.yaml")
        assert data["clip_scoring"]["live_event_bonus"] == 1.5

    def test_upset_multiplier_present(self):
        data = _load("scoring_weights.yaml")
        assert data["clip_scoring"]["upset_multiplier"] == 1.4

    def test_record_multiplier_present(self):
        data = _load("scoring_weights.yaml")
        assert data["clip_scoring"]["record_multiplier"] == 1.3


class TestScheduleSlots:
    def test_publishing_windows(self):
        data = _load("schedule.yaml")
        assert len(data["publishing_windows"]) == 2

    def test_window_labels(self):
        data = _load("schedule.yaml")
        labels = [w["label"] for w in data["publishing_windows"]]
        assert "12:00 IST" in labels


class TestTemplatesStoryCategories:
    def test_story_categories_exist(self):
        data = _load("templates.yaml")
        categories = data["hooks"]["story_categories"]
        for cat in ["upset", "record", "injury", "trade", "clutch", "default"]:
            assert cat in categories

    def test_categories_have_formulas(self):
        data = _load("templates.yaml")
        for _cat_name, cat_config in data["hooks"]["story_categories"].items():
            assert "formulas" in cat_config
            assert len(cat_config["formulas"]) >= 1
