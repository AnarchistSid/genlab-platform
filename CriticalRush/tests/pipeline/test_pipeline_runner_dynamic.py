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
    def test_gaming_loads_all_required_stages(self):
        """Gaming niche.yaml must wire every load-bearing stage.

        Replaces a hardcoded ``len(stages) == 27`` assert: that pattern
        forced a count-bump on every unrelated PR that added or re-enabled
        a stage. Asking *which* stages are present catches the real bug
        (missing/disabled load-bearing stage) while letting additions
        through silently.
        """
        runner = PipelineRunner()
        stages, _ = runner._load_stages("gaming", _gaming_config())
        loaded_names = {s.__class__.__name__ for s in stages}

        required = {
            "ExpressLane",
            "FetchTrendingVideos",
            "FetchGamingStories",
            "ScoreGamingClips",
            "VideoGate",
            "GamingWritingStrategy",
            "GamingHookStrategy",
            "RenderGamingVideo",
            "RenderWhisperCaptions",
            "ValidateVideos",
            "PushToBacklog",
            "FetchInsights",
            "PerformanceLearner",
            "RunReport",
        }
        missing = required - loaded_names
        assert not missing, f"required stages missing/disabled: {missing}"

        # Positional invariants that the data-flow depends on.
        assert stages[0].__class__.__name__ == "ExpressLane"
        assert stages[-1].__class__.__name__ == "RunReport"

    def test_gaming_stage_order_invariants(self):
        """Data-flow ordering constraints (replaces a 27-item exact-match
        ``expected_order`` list).

        Encodes the actual semantics: writing needs a fetched + scored
        story; rendering needs writing; validation needs rendering; push
        needs validation; analytics tail needs push. Order within each
        phase doesn't matter — anything that does matter is asserted as
        a pairwise ``A before B`` constraint.
        """
        runner = PipelineRunner()
        stages, _ = runner._load_stages("gaming", _gaming_config())
        order = [s.__class__.__name__ for s in stages]

        def pos(name: str) -> int:
            assert name in order, f"{name} not in loaded stages"
            return order.index(name)

        # Phase 1: Fetch → score → gate
        assert pos("FetchTrendingVideos") < pos("ScoreGamingClips")
        assert pos("ScoreGamingClips") < pos("VideoGate")
        # Phase 2: Gate → writing → hooks (writing must run on cleared clips)
        assert pos("VideoGate") < pos("GamingWritingStrategy")
        assert pos("GamingWritingStrategy") < pos("GamingHookStrategy")
        # Phase 3: Writing → render → validate
        assert pos("GamingHookStrategy") < pos("RenderGamingVideo")
        assert pos("RenderGamingVideo") < pos("ValidateVideos")
        assert pos("RenderWhisperCaptions") < pos("ValidateVideos")
        # Phase 4: Validate → push → analytics tail
        assert pos("ValidateVideos") < pos("PushToBacklog")
        assert pos("PushToBacklog") < pos("FetchInsights")
        assert pos("FetchInsights") < pos("PerformanceLearner")
        assert pos("PerformanceLearner") < pos("RunReport")

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
                    {
                        "class": "niches.gaming.stages.fetch_gaming_stories.FetchGamingStories",
                        "enabled": True,
                    },
                    {
                        "class": "niches.gaming.stages.score_gaming_clips.ScoreGamingClips",
                        "enabled": False,
                    },
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
            "pipeline": {"stages": [{"class": "nonexistent.module.FakeStage", "enabled": True}]}
        }
        with pytest.raises(ImportError, match="nonexistent.module"):
            runner._load_stages("test", config)

    def test_invalid_class_name_raises_attribute_error(self):
        """Valid module but wrong class name raises AttributeError."""
        runner = PipelineRunner()
        config = {
            "pipeline": {
                "stages": [{"class": "niches.gaming.stages.fetch_gaming_stories.NonExistentClass"}]
            }
        }
        with pytest.raises(AttributeError, match="NonExistentClass"):
            runner._load_stages("test", config)

    def test_no_gaming_specific_imports_in_runner(self):
        """pipeline_runner.py contains no hardcoded gaming stage imports."""
        from core import pipeline_runner

        source = inspect.getsource(pipeline_runner)
        for _stage_name in [
            "FetchGamingStories",
            "FilterGamingStories",
            "ScoreGamingClips",
            "EnrichWithIGDB",
            "ExtractGamingMedia",
            "WriteGamingContent",
            "AdaptGamingContent",
            "RenderGamingVideo",
            "GenerateGamingAudio",
            "RenderTextOverlays",
            "PublishGamingContent",
            "PushToBacklog",
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
            assert hasattr(stage, "execute"), f"{stage.__class__.__name__} missing execute() method"
            assert callable(stage.execute)
