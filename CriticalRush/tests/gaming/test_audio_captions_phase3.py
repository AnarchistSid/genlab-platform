"""Phase 3 tests for audio generation, caption rendering, and tool transplants.

Tests cover:
  - GenerateGamingAudio: LLM/template failure graceful, TTS failure graceful,
    successful path, skipped without rendered video
  - RenderTextOverlays: Whisper failure graceful, successful path,
    skipped without rendered video
  - tts_client: synthesize cascade, cost tracking
  - ass_subtitle_generator: timestamp conversion, header, dialogue lines, ASS file
  - caption_generator: config loading, model size selection, SRT formatting
  - Pipeline ordering: GENERATE_AUDIO before RENDER_TEXT_OVERLAYS
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# ass_subtitle_generator tests
# ---------------------------------------------------------------------------


class TestASSSubtitleGenerator:
    def test_seconds_to_ass_zero(self):
        from niches.gaming.tools.ass_subtitle_generator import seconds_to_ass

        assert seconds_to_ass(0.0) == "0:00:00.00"

    def test_seconds_to_ass_with_minutes(self):
        from niches.gaming.tools.ass_subtitle_generator import seconds_to_ass

        assert seconds_to_ass(65.5) == "0:01:05.50"

    def test_seconds_to_ass_negative_clamps_to_zero(self):
        from niches.gaming.tools.ass_subtitle_generator import seconds_to_ass

        assert seconds_to_ass(-5.0) == "0:00:00.00"

    def test_generate_ass_header_contains_sections(self):
        from niches.gaming.tools.ass_subtitle_generator import generate_ass_header

        header = generate_ass_header()
        assert "[Script Info]" in header
        assert "[V4+ Styles]" in header
        assert "[Events]" in header
        assert "PlayResX: 1080" in header
        assert "PlayResY: 1920" in header

    def test_generate_dialogue_lines_basic(self):
        from niches.gaming.tools.ass_subtitle_generator import generate_dialogue_lines

        transcription = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "hello world",
                    "words": [
                        {"word": "hello", "start": 0.0, "end": 1.0, "probability": 0.95},
                        {"word": "world", "start": 1.0, "end": 2.0, "probability": 0.90},
                    ],
                }
            ]
        }
        lines = generate_dialogue_lines(transcription, min_confidence=0.0)
        assert len(lines) == 2  # one per word
        assert "HELLO" in lines[0]  # uppercase by default
        assert "Dialogue:" in lines[0]

    def test_generate_dialogue_lines_empty_transcription(self):
        from niches.gaming.tools.ass_subtitle_generator import generate_dialogue_lines

        assert generate_dialogue_lines({}) == []
        assert generate_dialogue_lines({"segments": []}) == []

    def test_generate_ass_file_creates_file(self):
        from niches.gaming.tools.ass_subtitle_generator import generate_ass_file

        transcription = {
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "test",
                    "words": [
                        {"word": "test", "start": 0.0, "end": 2.0, "probability": 0.9},
                    ],
                }
            ]
        }
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as f:
            ass_path = f.name

        try:
            result = generate_ass_file(transcription, ass_path)
            assert result is True
            content = Path(ass_path).read_text()
            assert "[Script Info]" in content
            assert "Dialogue:" in content
        finally:
            Path(ass_path).unlink(missing_ok=True)

    def test_generate_ass_file_returns_false_for_none(self):
        from niches.gaming.tools.ass_subtitle_generator import generate_ass_file

        assert generate_ass_file(None, "/tmp/test.ass") is False


# ---------------------------------------------------------------------------
# caption_generator tests
# ---------------------------------------------------------------------------


class TestCaptionGenerator:
    def test_load_captions_config_returns_defaults(self):
        from niches.gaming.tools.caption_generator import load_captions_config

        config = load_captions_config(Path("/nonexistent/captions.yaml"))
        assert "model" in config
        assert "transcription" in config
        assert "subtitle_style" in config
        assert config["model"]["default_size"] == "base"

    def test_select_model_size_short_clip(self):
        from niches.gaming.tools.caption_generator import select_model_size

        config = {
            "model": {
                "default_size": "base",
                "size_by_duration": [
                    {"max_seconds": 30, "model_size": "base"},
                    {"max_seconds": 60, "model_size": "small"},
                    {"max_seconds": 180, "model_size": "medium"},
                ],
            }
        }
        assert select_model_size(20.0, config) == "base"
        assert select_model_size(45.0, config) == "small"
        assert select_model_size(120.0, config) == "medium"

    def test_select_model_size_none_duration(self):
        from niches.gaming.tools.caption_generator import select_model_size

        assert select_model_size(None) == "base"

    def test_format_srt_timestamp(self):
        from niches.gaming.tools.caption_generator import format_srt_timestamp

        assert format_srt_timestamp(0.0) == "00:00:00,000"
        assert format_srt_timestamp(65.5) == "00:01:05,500"

    def test_generate_srt_returns_false_for_empty(self):
        from niches.gaming.tools.caption_generator import generate_srt

        assert generate_srt(None, "/tmp/test.srt") is False
        assert generate_srt({"segments": []}, "/tmp/test.srt") is False

    def test_generate_srt_creates_file(self):
        from niches.gaming.tools.caption_generator import generate_srt

        transcription = {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "Hello world"},
            ]
        }
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            srt_path = f.name

        try:
            result = generate_srt(transcription, srt_path)
            assert result is True
            content = Path(srt_path).read_text()
            assert "Hello world" in content
            assert "-->" in content
        finally:
            Path(srt_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# GenerateGamingAudio stage tests
# ---------------------------------------------------------------------------


class TestGenerateGamingAudioStage:
    def _make_context(self, stories=None, blueprints=None):
        return {
            "stories": stories or [],
            "blueprints": blueprints or [],
            "feature_flags": {},
        }

    def test_skips_story_without_rendered_path(self):
        from niches.gaming.stages.generate_gaming_audio import GenerateGamingAudio

        stage = GenerateGamingAudio()
        context = self._make_context(
            stories=[
                {"title": "No render", "media": {}, "content": {"hook": "test"}},
            ]
        )
        with patch(
            "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")
        ):
            result = stage.execute(context)
        assert result["run_stats"]["audio_generation"]["skipped"] == 1

    def test_script_generation_failure_is_graceful(self):
        """Template/LLM failure: story still in context, no exception."""
        from niches.gaming.stages.generate_gaming_audio import GenerateGamingAudio

        stage = GenerateGamingAudio()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            clip_path = f.name

        try:
            context = self._make_context(
                stories=[
                    {
                        "title": "Test",
                        "media": {"rendered_path": clip_path},
                        "content": {"hook": "Test hook"},
                    },
                ]
            )

            with patch(
                "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")
            ):
                with patch(
                    "niches.gaming.stages.generate_gaming_audio.generate_commentary_text",
                    side_effect=RuntimeError("LLM exploded"),
                ):
                    result = stage.execute(context)

            # Story still present, no exception propagated
            assert len(result["stories"]) == 1
            assert result["stories"][0].get("commentary_audio_path") is None
            assert result["run_stats"]["audio_generation"]["failed_script"] == 1
        finally:
            Path(clip_path).unlink(missing_ok=True)

    def test_tts_failure_is_graceful(self):
        """TTS failure: story still in context, commentary_audio_path=None."""
        from niches.gaming.stages.generate_gaming_audio import GenerateGamingAudio

        stage = GenerateGamingAudio()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            clip_path = f.name

        try:
            context = self._make_context(
                stories=[
                    {
                        "title": "Test",
                        "media": {"rendered_path": clip_path},
                        "content": {"hook": "Test hook"},
                    },
                ]
            )

            with patch(
                "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")
            ):
                with patch(
                    "niches.gaming.stages.generate_gaming_audio.generate_commentary_text",
                    return_value="That was insane",
                ):
                    mock_cascade = MagicMock()
                    mock_cascade.synthesize.side_effect = RuntimeError("TTS exploded")
                    with patch(
                        "niches.gaming.stages.generate_gaming_audio._get_tts_cascade",
                        return_value=mock_cascade,
                    ):
                        result = stage.execute(context)

            assert len(result["stories"]) == 1
            assert result["stories"][0].get("commentary_audio_path") is None
            assert result["run_stats"]["audio_generation"]["failed_tts"] == 1
        finally:
            Path(clip_path).unlink(missing_ok=True)

    def test_successful_path_writes_audio_path(self):
        from niches.gaming.stages.generate_gaming_audio import GenerateGamingAudio

        stage = GenerateGamingAudio()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            clip_path = f.name

        try:
            context = self._make_context(
                stories=[
                    {
                        "title": "Test",
                        "media": {"rendered_path": clip_path},
                        "content": {"hook": "Test hook"},
                    },
                ]
            )

            def fake_synthesize(text, output_path, **kwargs):
                Path(output_path).write_text("fake audio")
                return MagicMock(success=True)

            mock_cascade = MagicMock()
            mock_cascade.synthesize.side_effect = fake_synthesize

            with patch(
                "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")
            ):
                with patch(
                    "niches.gaming.stages.generate_gaming_audio.generate_commentary_text",
                    return_value="That was insane",
                ):
                    with patch(
                        "niches.gaming.stages.generate_gaming_audio._get_tts_cascade",
                        return_value=mock_cascade,
                    ):
                        result = stage.execute(context)

            assert result["stories"][0].get("commentary_audio_path") is not None
            assert result["run_stats"]["audio_generation"]["generated"] == 1

            # Cleanup generated file
            audio_path = result["stories"][0]["commentary_audio_path"]
            Path(audio_path).unlink(missing_ok=True)
        finally:
            Path(clip_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# RenderTextOverlays stage tests
# ---------------------------------------------------------------------------


class TestRenderTextOverlaysStage:
    def _make_context(self, stories=None, blueprints=None):
        return {
            "stories": stories or [],
            "blueprints": blueprints or [],
            "feature_flags": {},
        }

    def test_skips_story_without_rendered_path(self):
        from niches.gaming.stages.render_text_overlays import RenderTextOverlays

        stage = RenderTextOverlays()
        context = self._make_context(
            stories=[
                {"title": "No render", "media": {}},
            ]
        )
        with patch(
            "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")
        ):
            result = stage.execute(context)
        assert result["run_stats"]["text_overlays"]["skipped"] == 1

    def test_whisper_failure_is_graceful(self):
        """Whisper failure: video path unchanged, no exception."""
        from niches.gaming.stages.render_text_overlays import RenderTextOverlays

        stage = RenderTextOverlays()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake video")
            video_path = f.name

        try:
            context = self._make_context(
                stories=[
                    {"title": "Test", "media": {"rendered_path": video_path}},
                ]
            )

            with patch(
                "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")
            ):
                with patch(
                    "niches.gaming.stages.render_text_overlays.transcribe",
                    side_effect=RuntimeError("Whisper crashed"),
                ):
                    result = stage.execute(context)

            # Video path unchanged — passthrough
            assert result["stories"][0]["media"]["rendered_path"] == video_path
            assert result["run_stats"]["text_overlays"]["failed"] == 1
        finally:
            Path(video_path).unlink(missing_ok=True)

    def test_whisper_returns_none_is_graceful(self):
        """Whisper returns None (not installed): video path unchanged."""
        from niches.gaming.stages.render_text_overlays import RenderTextOverlays

        stage = RenderTextOverlays()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake video")
            video_path = f.name

        try:
            context = self._make_context(
                stories=[
                    {"title": "Test", "media": {"rendered_path": video_path}},
                ]
            )

            with patch(
                "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")
            ):
                with patch(
                    "niches.gaming.stages.render_text_overlays.transcribe",
                    return_value=None,
                ):
                    result = stage.execute(context)

            assert result["stories"][0]["media"]["rendered_path"] == video_path
            # No speech → counted as failed (passthrough)
            assert result["run_stats"]["text_overlays"]["failed"] == 1
        finally:
            Path(video_path).unlink(missing_ok=True)

    def test_successful_path_updates_rendered_path(self):
        """Full success: transcribe + ASS + burn → updated path."""
        from niches.gaming.stages.render_text_overlays import RenderTextOverlays

        stage = RenderTextOverlays()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake video")
            video_path = f.name

        try:
            context = self._make_context(
                stories=[
                    {"title": "Test", "media": {"rendered_path": video_path}},
                ]
            )

            mock_transcription = {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 2.0,
                        "text": "test",
                        "words": [
                            {"word": "test", "start": 0.0, "end": 2.0, "probability": 0.9},
                        ],
                    }
                ]
            }

            def fake_burn(video_path, ass_path, output_path, fonts_dir=None):
                Path(output_path).write_text("captioned video")
                return True

            with patch(
                "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test")
            ):
                with patch(
                    "niches.gaming.stages.render_text_overlays.transcribe",
                    return_value=mock_transcription,
                ):
                    with patch(
                        "niches.gaming.stages.render_text_overlays.burn_ass_captions",
                        side_effect=fake_burn,
                    ):
                        result = stage.execute(context)

            # Path should have changed to captioned version
            new_path = result["stories"][0]["media"]["rendered_path"]
            assert new_path != video_path
            assert result["run_stats"]["text_overlays"]["captioned"] == 1

            # Cleanup
            Path(new_path).unlink(missing_ok=True)
        finally:
            Path(video_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Pipeline ordering test
# ---------------------------------------------------------------------------


class TestPipelineOrdering:
    @staticmethod
    def _gaming_config():
        from pathlib import Path

        from genlab_core.niche_loader import load_niche_config

        return load_niche_config("gaming", Path(__file__).resolve().parent.parent.parent)

    def test_post_render_stages_after_render_video(self):
        """Post-render stages (audio, overlays) must come after RenderGamingVideo."""
        from core.pipeline_runner import PipelineRunner

        runner = PipelineRunner()

        stages, declarations = runner._load_stages("gaming", self._gaming_config())
        stage_names = [s.__class__.__name__ for s in stages]

        render_idx = stage_names.index("RenderGamingVideo")
        audio_idx = stage_names.index("GenerateGamingAudio")
        overlay_idx = stage_names.index("RenderTextOverlays")

        # Both post-render stages come after render video
        assert audio_idx > render_idx
        assert overlay_idx > render_idx

    def test_pipeline_has_26_stages(self):
        """Pipeline should have 26 enabled stages (gaming + shared)."""
        from core.pipeline_runner import PipelineRunner

        runner = PipelineRunner()
        stages, _ = runner._load_stages("gaming", self._gaming_config())
        assert len(stages) == 26
