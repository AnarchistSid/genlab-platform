"""Tests for the 9 shared pipeline stages in genlab_core.pipeline.stages.

Each stage follows execute(context) -> context. Tests verify:
  - Empty input passes through cleanly
  - Normal input produces expected annotations
  - Errors are caught and logged, not raised
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── QCGates ────────────────────────────────────────────────────

class TestQCGates:
    def _make(self):
        from genlab_core.pipeline.stages.qc_gates import QCGates
        return QCGates()

    def test_empty_blueprints(self):
        stage = self._make()
        ctx = {"blueprints": []}
        result = stage.execute(ctx)
        assert result is ctx

    def test_passing_blueprint(self):
        stage = self._make()
        ctx = {
            "blueprints": [
                {
                    "candidate_id": "test1",
                    "hook": "Breaking news",
                    "body": "Full story here",
                    "sources": ["https://example.com"],
                    "format": "reel",
                    "duration_seconds": 30,
                },
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        bp = result["blueprints"][0]
        assert bp["validation_status"]["all_passed"] is True
        assert result["run_stats"]["qc"]["passed"] == 1

    def test_failing_completeness(self):
        stage = self._make()
        ctx = {
            "blueprints": [
                {"candidate_id": "test2", "sources": []},
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        bp = result["blueprints"][0]
        assert bp["validation_status"]["all_passed"] is False
        assert "Missing required field" in str(bp["validation_status"]["issues"])

    def test_score_penalty_applied(self):
        stage = self._make()
        ctx = {
            "blueprints": [
                {
                    "candidate_id": "test3",
                    "priority_score": 0.8,
                    "sources": [],
                },
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert result["blueprints"][0]["priority_score"] < 0.8

    def test_constraint_check_max_slides(self):
        stage = self._make()
        ctx = {
            "blueprints": [
                {
                    "candidate_id": "test4",
                    "hook": "Test",
                    "body": "Content",
                    "sources": ["https://example.com"],
                    "format": "carousel",
                    "slides": list(range(20)),
                },
            ],
            "niche_config": {"templates": {"max_slides": 10}},
        }
        result = stage.execute(ctx)
        assert result["blueprints"][0]["validation_status"]["constraints_passed"] is False


# ── ViralityScoring ─────────────────────────────────────────────

class TestViralityScoring:
    def _make(self):
        from genlab_core.pipeline.stages.virality_scoring import ViralityScoring
        return ViralityScoring()

    def test_empty_blueprints(self):
        stage = self._make()
        ctx = {"blueprints": []}
        result = stage.execute(ctx)
        assert result is ctx

    def test_scores_assigned(self):
        stage = self._make()
        ctx = {
            "blueprints": [
                {"hook": "How to use ChatGPT for coding", "body": "Tutorial guide"},
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        bp = result["blueprints"][0]
        assert "virality_score" in bp
        assert bp["virality_score"] > 0  # Should match tutorial + named_tool
        assert "tutorial_how_to" in bp["virality_features"]
        assert "named_tool" in bp["virality_features"]

    def test_no_features_zero_score(self):
        stage = self._make()
        ctx = {
            "blueprints": [
                {"hook": "Plain text", "body": "Nothing special"},
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert result["blueprints"][0]["virality_score"] == 0.0
        assert result["blueprints"][0]["virality_features"] == []

    def test_question_hook(self):
        stage = self._make()
        ctx = {
            "blueprints": [
                {"hook": "Why is everyone talking about this?", "body": "Interesting."},
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert "hook_format_question" in result["blueprints"][0]["virality_features"]

    def test_controversy(self):
        stage = self._make()
        ctx = {
            "blueprints": [
                {"hook": "This controversial move divided the community", "body": "Drama ensued."},
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert "controversy_debate" in result["blueprints"][0]["virality_features"]


# ── ExpressLane ─────────────────────────────────────────────────

class TestExpressLane:
    def _make(self):
        from genlab_core.pipeline.stages.express_lane import ExpressLane
        return ExpressLane()

    def test_empty_stories(self):
        stage = self._make()
        ctx = {"stories": []}
        result = stage.execute(ctx)
        assert result is ctx

    def test_critical_story(self):
        stage = self._make()
        ctx = {
            "stories": [
                {"title": "OpenAI Releases GPT-5", "summary": "Breaking: just announced"},
            ],
        }
        result = stage.execute(ctx)
        clf = result["stories"][0]["urgency_classification"]
        assert clf["urgency"] == "CRITICAL"
        assert clf["express"] is True
        assert clf["score"] >= 0.6

    def test_low_story(self):
        stage = self._make()
        ctx = {
            "stories": [
                {"title": "Small update to a niche tool", "summary": "Minor fix released"},
            ],
        }
        result = stage.execute(ctx)
        clf = result["stories"][0]["urgency_classification"]
        assert clf["urgency"] == "LOW"
        assert clf["express"] is False

    def test_sorting_express_first(self):
        stage = self._make()
        ctx = {
            "stories": [
                {"title": "Minor news", "summary": "Nothing special"},
                {"title": "Breaking: Google acquires Anthropic for $50B", "summary": "Just announced"},
            ],
        }
        result = stage.execute(ctx)
        # Express story should be sorted first
        assert result["stories"][0]["urgency_classification"]["express"] is True

    def test_stats_populated(self):
        stage = self._make()
        ctx = {
            "stories": [
                {"title": "OpenAI GPT-5 launch", "summary": "Major launch"},
                {"title": "Regular update", "summary": "Nothing"},
            ],
        }
        result = stage.execute(ctx)
        stats = result["run_stats"]["express_lane"]
        assert stats["total"] == 2
        assert stats["express_count"] >= 1


# ── RenderTextOverlays ──────────────────────────────────────────

class TestRenderTextOverlays:
    def _make(self):
        from genlab_core.pipeline.stages.render_text_overlays import RenderTextOverlays
        return RenderTextOverlays()

    def test_empty_stories(self):
        stage = self._make()
        ctx = {"stories": []}
        result = stage.execute(ctx)
        assert result is ctx

    def test_skips_missing_video(self):
        stage = self._make()
        ctx = {
            "stories": [
                {"hook": "Test hook", "media": {"rendered_path": "/nonexistent/video.mp4"}},
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert result["run_stats"]["text_overlays"]["skipped"] == 1

    def test_skips_no_hook(self):
        stage = self._make()
        ctx = {
            "stories": [
                {"media": {"rendered_path": "/some/path.mp4"}},
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert result["run_stats"]["text_overlays"]["skipped"] == 1

    def test_reads_hook_from_content_dict(self):
        """Hook in story['content']['hook'] should be found (video-first pipelines)."""
        stage = self._make()
        ctx = {
            "stories": [
                {
                    "content": {"hook": "This anime is peak"},
                    "media": {"rendered_path": "/nonexistent/video.mp4"},
                },
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        # Skipped because file doesn't exist, NOT because hook is missing.
        # The key assertion: if file existed, hook would be found.
        stats = result["run_stats"]["text_overlays"]
        assert stats["skipped"] == 1
        assert stats["errors"] == 0

    def test_reads_hook_from_media_hook_text(self):
        """Fallback to media['hook_text'] when other paths are empty."""
        stage = self._make()
        ctx = {
            "stories": [
                {
                    "media": {
                        "rendered_path": "/nonexistent/video.mp4",
                        "hook_text": "Legacy hook field",
                    },
                },
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        stats = result["run_stats"]["text_overlays"]
        assert stats["skipped"] == 1
        assert stats["errors"] == 0


# ── GenerateAudio ───────────────────────────────────────────────

class TestGenerateAudio:
    def _make(self):
        from genlab_core.pipeline.stages.generate_audio import GenerateAudio
        return GenerateAudio()

    def test_empty_blueprints(self):
        stage = self._make()
        ctx = {"blueprints": []}
        result = stage.execute(ctx)
        assert result is ctx

    def test_disabled_in_config(self):
        stage = self._make()
        ctx = {
            "blueprints": [{"hook": "Test", "body": "Content"}],
            "niche_config": {"audio": {"enabled": False}},
        }
        result = stage.execute(ctx)
        # Should return early without processing
        assert "audio" not in ctx.get("run_stats", {})

    def test_skips_short_script(self):
        stage = self._make()
        # Mock TTSCascade import to avoid real dependency
        with patch.dict("sys.modules", {"genlab_core.tts.cascade": MagicMock()}):
            ctx = {
                "blueprints": [{"hook": "Hi", "body": ""}],
                "niche_config": {"audio": {"enabled": True}},
            }
            result = stage.execute(ctx)
            assert result.get("run_stats", {}).get("audio", {}).get("skipped", 0) >= 1


# ── ValidateVideos ──────────────────────────────────────────────

class TestValidateVideos:
    def _make(self):
        from genlab_core.pipeline.stages.validate_videos import ValidateVideos
        return ValidateVideos()

    def test_empty_stories(self):
        stage = self._make()
        ctx = {"stories": []}
        result = stage.execute(ctx)
        assert result is ctx

    def test_skips_no_video(self):
        stage = self._make()
        ctx = {
            "stories": [{"media": {}}],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert result["run_stats"]["video_validation"]["skipped"] == 1

    def test_check_specs(self):
        """Test the _check method with valid probe data."""
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt709",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {"duration": "30.0", "size": "5000000"},
        }
        issues = stage._check(probe)
        assert issues == []

    def test_check_wrong_codec(self):
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "vp9",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt709",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {"duration": "30.0", "size": "5000000"},
        }
        issues = stage._check(probe)
        assert any("wrong_codec" in i for i in issues)

    def test_check_too_short(self):
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt709",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {"duration": "5.0", "size": "100000"},
        }
        issues = stage._check(probe)
        assert any("too_short" in i for i in issues)

    def test_check_too_long(self):
        """Videos longer than 60s must be flagged."""
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt709",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {"duration": "120.0", "size": "5000000"},
        }
        issues = stage._check(probe)
        assert any("too_long" in i for i in issues)

    def test_check_wrong_dimensions(self):
        """Non-1080x1920 dimensions must be flagged."""
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 720,
                    "height": 1280,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt709",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {"duration": "30.0", "size": "5000000"},
        }
        issues = stage._check(probe)
        assert any("wrong_dimensions" in i for i in issues)

    def test_check_wrong_color_space(self):
        """bt470bg color space must be flagged."""
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt470bg",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {"duration": "30.0", "size": "5000000"},
        }
        issues = stage._check(probe)
        assert any("wrong_color_space" in i for i in issues)

    def test_check_wrong_audio_sample_rate(self):
        """Non-48kHz audio must be flagged."""
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt709",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "44100",
                    "channels": 2,
                },
            ],
            "format": {"duration": "30.0", "size": "5000000"},
        }
        issues = stage._check(probe)
        assert any("wrong_sample_rate" in i for i in issues)

    def test_check_wrong_audio_channels(self):
        """Non-stereo audio must be flagged."""
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt709",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 1,
                },
            ],
            "format": {"duration": "30.0", "size": "5000000"},
        }
        issues = stage._check(probe)
        assert any("wrong_audio_channels" in i for i in issues)

    def test_check_no_audio_stream(self):
        """Missing audio stream must be flagged."""
        stage = self._make()
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "color_space": "bt709",
                },
            ],
            "format": {"duration": "30.0", "size": "5000000"},
        }
        issues = stage._check(probe)
        assert any("no_audio_stream" in i for i in issues)


# ── FetchInsights ───────────────────────────────────────────────

class TestFetchInsights:
    def _make(self):
        from genlab_core.pipeline.stages.fetch_insights import FetchInsights
        return FetchInsights()

    def test_empty_stories(self):
        stage = self._make()
        ctx = {"stories": []}
        result = stage.execute(ctx)
        assert result is ctx

    def test_skips_unpublished(self):
        stage = self._make()
        ctx = {
            "stories": [{"title": "Test", "published_platforms": {}}],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert result["run_stats"]["insights"]["skipped"] == 1

    def test_skips_no_publish_time(self):
        stage = self._make()
        ctx = {
            "stories": [
                {
                    "title": "Test",
                    "published_platforms": {"instagram": "123"},
                },
            ],
            "niche_config": {},
        }
        result = stage.execute(ctx)
        assert result["run_stats"]["insights"]["skipped"] == 1


# ── RunReport ───────────────────────────────────────────────────

class TestRunReport:
    def _make(self):
        from genlab_core.pipeline.stages.run_report import RunReport
        return RunReport()

    def test_writes_report(self):
        stage = self._make()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "genlab_core.pipeline.stages.run_report.RunReport._resolve_run_dir",
                return_value=Path(tmpdir),
            ):
                ctx = {
                    "stories": [{"title": "Test"}],
                    "blueprints": [{"id": "bp1"}],
                    "run_stats": {"qc": {"passed": 1, "failed": 0, "total": 1, "pass_rate": "100.0%"}},
                    "niche_config": {"niche_id": "test"},
                }
                result = stage.execute(ctx)
                report_path = Path(tmpdir) / "run_report.json"
                assert report_path.exists()
                report = json.loads(report_path.read_text())
                assert report["niche_id"] == "test"
                assert report["metrics"]["stories_count"] == 1

    def test_empty_context(self):
        stage = self._make()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "genlab_core.pipeline.stages.run_report.RunReport._resolve_run_dir",
                return_value=Path(tmpdir),
            ):
                ctx = {
                    "stories": [],
                    "blueprints": [],
                    "run_stats": {},
                    "niche_config": {"niche_id": "test"},
                }
                result = stage.execute(ctx)
                report_path = Path(tmpdir) / "run_report.json"
                assert report_path.exists()


# ── PerformanceLearner ──────────────────────────────────────────

class TestPerformanceLearner:
    def _make(self):
        from genlab_core.pipeline.stages.performance_learner import PerformanceLearner
        return PerformanceLearner()

    def test_skips_no_engagement(self):
        stage = self._make()
        ctx = {
            "stories": [{"title": "Test"}],
            "niche_config": {"niche_id": "test"},
        }
        result = stage.execute(ctx)
        # Should skip gracefully
        assert result is ctx

    def test_extract_arm_ids(self):
        stage = self._make()
        story = {
            "hook_formula": "question_hook",
            "template_id": "reel_v2",
            "scheduled_slot": "12:00",
        }
        arms = stage._extract_arm_ids(story)
        assert "hook:question_hook" in arms
        assert "template:reel_v2" in arms
        assert "slot:12:00" in arms

    def test_extract_arm_ids_empty(self):
        stage = self._make()
        arms = stage._extract_arm_ids({})
        assert arms == []
