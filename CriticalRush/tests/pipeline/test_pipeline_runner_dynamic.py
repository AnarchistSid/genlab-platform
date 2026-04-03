"""Tests for dynamic stage loading in pipeline_runner (C-01)."""

import inspect
from pathlib import Path

import pytest
from core.pipeline_runner import PipelineRunner
from genlab_core.exceptions import NicheConfigError
from genlab_core.niche_loader import load_niche_config

CR_ROOT = Path(__file__).resolve().parent.parent.parent


def _gaming_config():
    return load_niche_config("gaming", CR_ROOT)


class TestDynamicStageLoading:

    def test_gaming_stages_load_correctly(self):
        """Gaming niche loads its 25 enabled stages from niche.yaml."""
        runner = PipelineRunner()
        stages, _ = runner._load_stages("gaming", _gaming_config())
        assert len(stages) == 25
        assert stages[0].__class__.__name__ == "ExpressLane"
        assert stages[-1].__class__.__name__ == "RunReport"

    def test_gaming_stage_order_matches_config(self):
        """Stage order must match the niche.yaml declaration."""
        expected_order = [
            "ExpressLane",
            "FetchTrendingVideos",
            "FetchTwitchClips",
            "FetchSteamTrailers",
            "FetchGamingStories",
            "FilterGamingStories",
            "EnrichWithIGDB",
            "ExtractGamingMedia",
            "ScoreGamingClips",
            "VideoGate",
            "GamingWritingStrategy",
            "GamingHookStrategy",
            "GamingPlatformAdaptationStrategy",
            "AffiliateMatch",
            "QCGates",
            "ViralityScoring",
            "RenderGamingVideo",
            "RenderTextOverlays",
            "GenerateAudio",
            "GenerateGamingAudio",
            "ValidateVideos",
            "PushToBacklog",
            "FetchInsights",
            "PerformanceLearner",
            "RunReport",
        ]
        runner = PipelineRunner()
        stages, _ = runner._load_stages("gaming", _gaming_config())
        actual_order = [s.__class__.__name__ for s in stages]
        assert actual_order == expected_order

    def test_missing_pipeline_stages_raises_niche_config_error(self):
        """Niche config without pipeline.stages raises NicheConfigError."""
        runner = PipelineRunner()
        bad_config = {"niche_id": "test", "brand_name": "Test"}
        with pytest.raises(NicheConfigError, match="missing pipeline.stages"):
            runner._load_stages("test_niche", bad_config)

    def test_empty_stages_raises_niche_config_error(self):
        """Empty stages list raises NicheConfigError."""
        runner = PipelineRunner()
        config = {"pipeline": {"stages": []}}
        with pytest.raises(NicheConfigError, match="is empty"):
            runner._load_stages("empty_niche", config)

    def test_disabled_stage_is_skipped(self):
        """Stage with enabled=false is not instantiated."""
        runner = PipelineRunner()
        config = {
            "pipeline": {
                "stages": [
                    {"class": "niches.gaming.stages.fetch_gaming_stories.FetchGamingStories",
                     "enabled": True},
                    {"class": "niches.gaming.stages.score_gaming_clips.ScoreGamingClips",
                     "enabled": False},
                ]
            }
        }
        stages, _ = runner._load_stages("gaming", config)
        assert len(stages) == 1
        assert stages[0].__class__.__name__ == "FetchGamingStories"

    def test_invalid_module_raises_import_error(self):
        """Nonexistent module raises ImportError with clear message."""
        runner = PipelineRunner()
        config = {
            "pipeline": {
                "stages": [
                    {"class": "nonexistent.module.FakeStage", "enabled": True}
                ]
            }
        }
        with pytest.raises(ImportError, match="nonexistent.module"):
            runner._load_stages("test", config)

    def test_invalid_class_name_raises_attribute_error(self):
        """Valid module but wrong class name raises AttributeError."""
        runner = PipelineRunner()
        config = {
            "pipeline": {
                "stages": [
                    {"class": "niches.gaming.stages.fetch_gaming_stories.NonExistentClass"}
                ]
            }
        }
        with pytest.raises(AttributeError, match="NonExistentClass"):
            runner._load_stages("test", config)

    def test_no_gaming_specific_imports_in_runner(self):
        """pipeline_runner.py contains no hardcoded gaming stage imports."""
        from core import pipeline_runner
        source = inspect.getsource(pipeline_runner)
        for stage_name in [
            "FetchGamingStories", "FilterGamingStories", "ScoreGamingClips",
            "EnrichWithIGDB", "ExtractGamingMedia", "WriteGamingContent",
            "AdaptGamingContent", "RenderGamingVideo", "GenerateGamingAudio",
            "RenderTextOverlays", "PublishGamingContent", "PushToBacklog",
            "WriteRunReport",
        ]:
            # Stage names may appear in comments/docs but not as imports
            import_pattern = "from niches.gaming"
            assert import_pattern not in source, (
                f"pipeline_runner.py still has gaming import: {import_pattern}"
            )

    def test_all_stages_have_execute_method(self):
        """Every loaded stage must have an execute() method."""
        runner = PipelineRunner()
        stages, _ = runner._load_stages("gaming", _gaming_config())
        for stage in stages:
            assert hasattr(stage, "execute"), (
                f"{stage.__class__.__name__} missing execute() method"
            )
            assert callable(stage.execute)
