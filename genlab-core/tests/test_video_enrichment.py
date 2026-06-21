"""Pin: video_enrichment activates dormant VideoAnalyzer (Lever G1).

Pre-2026-06-21 ``llm/video_analyzer.py`` had full Gemini 2.5 Flash
video reasoning (description, key_entities, visual_quality,
suggested_hook, detected_products, best_moment_seconds) — and ZERO
production callers. Multi-modal infrastructure built but never wired.

Lever G1 ships the wrapper. These pins lock in:

  1. Default disabled — story unchanged when env unset
  2. Enabled but no API key → story unchanged (graceful degradation)
  3. Enabled + analyzer succeeds → story gets ``video_analysis`` key
  4. Enabled + analyzer returns empty → story unchanged
  5. Any exception in the analyzer path → story unchanged (never raises)
  6. ``get_visual_quality()`` accessor returns float | None correctly
  7. ``is_enabled()`` reads the env flag correctly
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _bare_story() -> dict:
    """Minimal story dict for enrichment testing."""
    return {
        "story_id": "test_001",
        "title": "Test story",
        "summary": "Test summary",
        "visual_paths": ["/tmp/fake_video.mp4"],
    }


class TestEnabledFlag:
    def test_disabled_by_default(self, monkeypatch):
        from genlab_core.media.video_enrichment import is_enabled

        monkeypatch.delenv("GENLAB_VIDEO_ANALYSIS_ENABLED", raising=False)
        assert is_enabled() is False

    def test_zero_treated_as_disabled(self, monkeypatch):
        from genlab_core.media.video_enrichment import is_enabled

        monkeypatch.setenv("GENLAB_VIDEO_ANALYSIS_ENABLED", "0")
        assert is_enabled() is False

    def test_one_treated_as_enabled(self, monkeypatch):
        from genlab_core.media.video_enrichment import is_enabled

        monkeypatch.setenv("GENLAB_VIDEO_ANALYSIS_ENABLED", "1")
        assert is_enabled() is True


class TestEnrichmentDisabledByDefault:
    def test_returns_story_unchanged_when_env_unset(self, monkeypatch):
        from genlab_core.media.video_enrichment import enrich_story_with_video_analysis

        monkeypatch.delenv("GENLAB_VIDEO_ANALYSIS_ENABLED", raising=False)
        story = _bare_story()
        original_keys = set(story.keys())

        result = enrich_story_with_video_analysis(story, "/tmp/video.mp4", niche_id="gaming")

        # Story returned unchanged — no new keys
        assert set(result.keys()) == original_keys
        assert "video_analysis" not in result


class TestEnrichmentEnabled:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_VIDEO_ANALYSIS_ENABLED", "1")

    def test_no_api_key_returns_story_unchanged(self, monkeypatch):
        """When env flag is on but GOOGLE_API_KEY missing, VideoAnalyzer
        is unavailable. Story returns unchanged — no exceptions."""
        from genlab_core.media.video_enrichment import enrich_story_with_video_analysis

        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        story = _bare_story()
        result = enrich_story_with_video_analysis(story, "/tmp/video.mp4")
        assert "video_analysis" not in result

    def test_successful_analysis_enriches_story(self, monkeypatch):
        """The happy path: analyzer returns structured data, story dict
        gets a ``video_analysis`` key with the result."""
        from genlab_core.media import video_enrichment

        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        fake_analyzer = MagicMock()
        fake_analyzer.available = True
        fake_analyzer.analyze.return_value = {
            "description": "Fast-paced gaming highlight",
            "key_entities": ["Counter-Strike", "ESL One"],
            "visual_quality": 0.87,
            "suggested_hook": "Insane 1v5 clutch from ESL One",
            "detected_products": [],
            "best_moment_seconds": 12.5,
        }

        with patch("genlab_core.llm.video_analyzer.VideoAnalyzer", return_value=fake_analyzer):
            story = _bare_story()
            result = video_enrichment.enrich_story_with_video_analysis(
                story, "/tmp/video.mp4", niche_id="gaming"
            )

        assert "video_analysis" in result
        analysis = result["video_analysis"]
        assert analysis["visual_quality"] == 0.87
        assert "Counter-Strike" in analysis["key_entities"]

    def test_empty_analyzer_result_returns_story_unchanged(self, monkeypatch):
        """VideoAnalyzer returns {} on internal failures (file missing,
        too large, upload timeout). Don't pollute the story dict."""
        from genlab_core.media import video_enrichment

        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        fake_analyzer = MagicMock()
        fake_analyzer.available = True
        fake_analyzer.analyze.return_value = {}

        with patch("genlab_core.llm.video_analyzer.VideoAnalyzer", return_value=fake_analyzer):
            story = _bare_story()
            result = video_enrichment.enrich_story_with_video_analysis(story, "/tmp/video.mp4")

        assert "video_analysis" not in result

    def test_exception_in_analyzer_returns_story_unchanged(self, monkeypatch):
        """If VideoAnalyzer raises (import error, network error, etc.),
        the wrapper catches and returns story unchanged. Pipeline
        stages MUST NOT crash on multi-modal infra failures."""
        from genlab_core.media import video_enrichment

        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        fake_analyzer = MagicMock()
        fake_analyzer.available = True
        fake_analyzer.analyze.side_effect = RuntimeError("simulated Gemini outage")

        with patch("genlab_core.llm.video_analyzer.VideoAnalyzer", return_value=fake_analyzer):
            story = _bare_story()
            result = video_enrichment.enrich_story_with_video_analysis(story, "/tmp/video.mp4")

        assert "video_analysis" not in result
        # Story still valid for downstream stages
        assert result["title"] == "Test story"


class TestVisualQualityAccessor:
    def test_returns_none_when_no_analysis(self):
        from genlab_core.media.video_enrichment import get_visual_quality

        assert get_visual_quality({}) is None
        assert get_visual_quality({"title": "x"}) is None

    def test_returns_none_when_analysis_is_not_dict(self):
        from genlab_core.media.video_enrichment import get_visual_quality

        assert get_visual_quality({"video_analysis": None}) is None
        assert get_visual_quality({"video_analysis": "not a dict"}) is None

    def test_returns_float_when_present(self):
        from genlab_core.media.video_enrichment import get_visual_quality

        story = {"video_analysis": {"visual_quality": 0.75}}
        assert get_visual_quality(story) == 0.75

    def test_returns_none_when_visual_quality_missing(self):
        from genlab_core.media.video_enrichment import get_visual_quality

        story = {"video_analysis": {"description": "x"}}
        assert get_visual_quality(story) is None

    def test_coerces_string_value_to_float(self):
        from genlab_core.media.video_enrichment import get_visual_quality

        # JSONB sometimes stores numbers as strings — be tolerant
        story = {"video_analysis": {"visual_quality": "0.62"}}
        assert get_visual_quality(story) == 0.62

    def test_returns_none_on_uncoerceable_value(self):
        from genlab_core.media.video_enrichment import get_visual_quality

        story = {"video_analysis": {"visual_quality": "not-a-number"}}
        assert get_visual_quality(story) is None
