"""Tests for the gaming Prefect flow structure."""

import inspect

from niches.gaming.flows.gaming_flow import _load_stages_from_config, gaming_pipeline


class TestGamingFlowStructure:
    def test_flow_is_decorated(self):
        """gaming_pipeline should be a Prefect flow."""
        # Prefect flows have a .fn attribute pointing to the original function
        assert hasattr(gaming_pipeline, "fn")

    def test_flow_name(self):
        """Flow name should be 'gaming-pipeline'."""
        assert gaming_pipeline.name == "gaming-pipeline"

    def test_flow_accepts_parameters(self):
        """Flow should accept dry_run, verbose, and trigger parameters."""
        sig = inspect.signature(gaming_pipeline.fn)
        params = list(sig.parameters.keys())
        assert "dry_run" in params
        assert "verbose" in params
        assert "trigger" in params

    def test_flow_defaults(self):
        """Default values should be sensible."""
        sig = inspect.signature(gaming_pipeline.fn)
        assert sig.parameters["dry_run"].default is False
        assert sig.parameters["verbose"].default is False
        assert sig.parameters["trigger"].default == "scheduled"

    def test_all_26_stages_loaded_from_niche_yaml(self):
        """Pipeline should have 26 stages (25 enabled + 1 disabled)."""
        stages = _load_stages_from_config()
        # 26 total in YAML, 1 disabled (RenderWhisperCaptions) → 25 enabled
        enabled = [s for s in stages if s.get("enabled", True)]
        assert len(enabled) == 25, f"Expected 25 enabled stages, got {len(enabled)}"

        # Verify key stage classes are present
        class_names = [s["class"].rsplit(".", 1)[1] for s in enabled]
        expected_subset = [
            "ExpressLane", "FetchTrendingVideos",
            "FetchTwitchClips", "FetchSteamTrailers",
            "FetchGamingStories", "FilterGamingStories", "EnrichWithIGDB",
            "ExtractGamingMedia", "ScoreGamingClips", "VideoGate",
            "GamingWritingStrategy", "GamingHookStrategy",
            "GamingPlatformAdaptationStrategy",
            "AffiliateMatch", "QCGates", "ViralityScoring",
            "RenderGamingVideo", "RenderTextOverlays", "GenerateAudio",
            "GenerateGamingAudio", "ValidateVideos",
            "PushToBacklog",
            "FetchInsights", "PerformanceLearner", "RunReport",
        ]
        assert class_names == expected_subset
