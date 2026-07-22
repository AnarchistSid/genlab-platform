"""Pin tests for the Layer 3 S7 phase E storytime compositor.

Covers the ``FrameCompositor.compose_storytime`` method shipped 2026-07-22.
TTS cascade + FFmpeg are mocked — this tests the orchestration contract
(what filter graph is built, which inputs are wired to which streams,
what raises vs degrades) rather than the actual audio/video rendering.

Live rendering coverage will come from the ai_creators canary once the
GENLAB_STORYTIME_COMPOSITOR_ENABLED flag flips per operator judgment.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.interfaces.tts import TTSResult
from genlab_core.media.frame_compositor import ChannelBranding, FrameCompositor


def _branding(logo_path: str) -> ChannelBranding:
    return ChannelBranding(
        niche_id="ai_creators",
        channel_name="BlackboxBrief",
        accent_color="#00D4FF",
        logo_path=logo_path,
        handle="@blackbox.brief",
    )


class TestComposeStorytime:
    def test_raises_when_narration_empty(self, tmp_path: Path) -> None:
        """Empty narration_text is a caller contract violation — raise
        rather than silently degrade to a broken storytime render."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fake png bytes")
        compositor = FrameCompositor(_branding(str(logo)))
        src = tmp_path / "src.mp4"
        src.write_bytes(b"fake mp4")
        out = tmp_path / "out.mp4"
        with pytest.raises(RuntimeError, match="narration_text is empty"):
            compositor.compose_storytime(
                source_video_path=str(src),
                hook_text="hook",
                output_path=str(out),
                narration_text="",
            )

    def test_raises_when_tts_cascade_fails(self, tmp_path: Path) -> None:
        """When all TTS providers fail, storytime cannot proceed — raise.
        Better a loud failure than a silent render with no audio."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fake png bytes")
        compositor = FrameCompositor(_branding(str(logo)))
        src = tmp_path / "src.mp4"
        src.write_bytes(b"fake mp4")
        out = tmp_path / "out.mp4"

        # Mock cascade to return failure
        failed_result = TTSResult(success=False, error="all providers down")
        mock_cascade = MagicMock()
        mock_cascade.synthesize.return_value = failed_result

        with patch(
            "genlab_core.tts.factory.build_tts_cascade",
            return_value=mock_cascade,
        ):
            with pytest.raises(RuntimeError, match="TTS cascade failed"):
                compositor.compose_storytime(
                    source_video_path=str(src),
                    hook_text="hook",
                    output_path=str(out),
                    narration_text="A meaningful narration.",
                )

    def test_ffmpeg_called_with_three_inputs(self, tmp_path: Path) -> None:
        """The FFmpeg command MUST wire 3 inputs: source video (0), TTS
        audio (1), logo image (2). If wiring drifts, the filter graph
        would reference nonexistent streams and the render would fail."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fake png bytes")
        compositor = FrameCompositor(_branding(str(logo)))
        src = tmp_path / "src.mp4"
        src.write_bytes(b"fake mp4")
        out = tmp_path / "out.mp4"

        # Mock cascade to write a fake TTS file at whatever path it's given
        def _fake_synthesize(text, path):
            Path(path).write_bytes(b"fake mp3")
            return TTSResult(success=True, output_path=str(path), provider="edge_tts", duration=5.0)

        mock_cascade = MagicMock()
        mock_cascade.synthesize.side_effect = _fake_synthesize

        # Capture the ffmpeg command
        captured_cmd = []

        def _fake_run(cmd, *, timeout, fallback_preset):
            captured_cmd.extend(cmd)
            return

        with patch(
            "genlab_core.tts.factory.build_tts_cascade",
            return_value=mock_cascade,
        ):
            with patch.object(compositor, "_run_ffmpeg", side_effect=_fake_run):
                compositor.compose_storytime(
                    source_video_path=str(src),
                    hook_text="Test hook",
                    output_path=str(out),
                    narration_text="A meaningful narration.",
                )

        # 3 -i inputs
        assert captured_cmd.count("-i") == 3, (
            f"Expected 3 -i flags for source+tts+logo; got {captured_cmd.count('-i')}"
        )
        # TTS audio mapped, source audio dropped
        assert "-map" in captured_cmd
        map_idx = [i for i, x in enumerate(captured_cmd) if x == "-map"]
        map_values = [captured_cmd[i + 1] for i in map_idx]
        assert "1:a" in map_values, (
            f"TTS audio (stream 1:a) MUST be mapped so source audio is replaced; "
            f"got maps: {map_values}"
        )
        # Source audio (0:a) MUST NOT be mapped
        assert "0:a" not in map_values, (
            f"Source audio (0:a) leaked into output — TTS narration would be "
            f"mixed with source noise; got maps: {map_values}"
        )

    def test_ffmpeg_output_is_1080x1920(self, tmp_path: Path) -> None:
        """Portrait target MUST be preserved per CLAUDE.md rule: every
        rendered video is 1080x1920. Filter graph asserts the scale."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fake png bytes")
        compositor = FrameCompositor(_branding(str(logo)))
        src = tmp_path / "src.mp4"
        src.write_bytes(b"fake mp4")
        out = tmp_path / "out.mp4"

        def _fake_synthesize(text, path):
            Path(path).write_bytes(b"fake mp3")
            return TTSResult(success=True, output_path=str(path), provider="edge_tts")

        captured = []

        def _fake_run(cmd, *, timeout, fallback_preset):
            captured.extend(cmd)

        mock_cascade = MagicMock()
        mock_cascade.synthesize.side_effect = _fake_synthesize

        with patch(
            "genlab_core.tts.factory.build_tts_cascade",
            return_value=mock_cascade,
        ):
            with patch.object(compositor, "_run_ffmpeg", side_effect=_fake_run):
                compositor.compose_storytime(
                    source_video_path=str(src),
                    hook_text="Hi",
                    output_path=str(out),
                    narration_text="Narration content here that is long enough.",
                )

        # Find filter_complex arg
        assert "-filter_complex" in captured
        idx = captured.index("-filter_complex")
        filter_graph = captured[idx + 1]
        assert "1080:1920" in filter_graph, (
            f"1080x1920 scale not in filter graph — portrait target broken: {filter_graph}"
        )

    def test_ffmpeg_uses_bt709_color_and_libx264(self, tmp_path: Path) -> None:
        """CLAUDE.md rule: every video is bt709, libx264 (H.264), AAC 48kHz.
        Filter graph or command args MUST include these."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fake png bytes")
        compositor = FrameCompositor(_branding(str(logo)))
        src = tmp_path / "src.mp4"
        src.write_bytes(b"fake mp4")
        out = tmp_path / "out.mp4"

        def _fake_synthesize(text, path):
            Path(path).write_bytes(b"fake mp3")
            return TTSResult(success=True, output_path=str(path), provider="edge_tts")

        captured = []

        def _fake_run(cmd, *, timeout, fallback_preset):
            captured.extend(cmd)

        mock_cascade = MagicMock()
        mock_cascade.synthesize.side_effect = _fake_synthesize

        with patch(
            "genlab_core.tts.factory.build_tts_cascade",
            return_value=mock_cascade,
        ):
            with patch.object(compositor, "_run_ffmpeg", side_effect=_fake_run):
                compositor.compose_storytime(
                    source_video_path=str(src),
                    hook_text="",
                    output_path=str(out),
                    narration_text="A narration.",
                )

        # libx264 + bt709 + 48kHz audio per CLAUDE.md video standards
        assert "libx264" in captured
        assert "bt709" in captured
        assert "48000" in captured
        assert "-shortest" in captured

    def test_narration_trimmed_to_1500_chars(self, tmp_path: Path) -> None:
        """Sanity — an excessively long narration would blow the TTS
        cost budget. compose_storytime caps at 1500 chars before
        calling the cascade."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fake png bytes")
        compositor = FrameCompositor(_branding(str(logo)))
        src = tmp_path / "src.mp4"
        src.write_bytes(b"fake mp4")
        out = tmp_path / "out.mp4"

        captured_texts = []

        def _fake_synthesize(text, path):
            captured_texts.append(text)
            Path(path).write_bytes(b"fake mp3")
            return TTSResult(success=True, output_path=str(path), provider="edge_tts")

        mock_cascade = MagicMock()
        mock_cascade.synthesize.side_effect = _fake_synthesize

        long_narration = "x" * 3000
        with patch(
            "genlab_core.tts.factory.build_tts_cascade",
            return_value=mock_cascade,
        ):
            with patch.object(compositor, "_run_ffmpeg"):
                compositor.compose_storytime(
                    source_video_path=str(src),
                    hook_text="",
                    output_path=str(out),
                    narration_text=long_narration,
                )
        assert len(captured_texts) == 1
        assert len(captured_texts[0]) == 1500

    def test_raises_when_logo_missing(self, tmp_path: Path) -> None:
        """R-26 logo invariant — no logo means no render. Storytime
        MUST NOT silently ship an unbranded reel."""
        # Point branding at a non-existent logo path
        compositor = FrameCompositor(_branding("/nonexistent/logo.png"))
        src = tmp_path / "src.mp4"
        src.write_bytes(b"fake mp4")
        out = tmp_path / "out.mp4"
        with pytest.raises(RuntimeError, match="Channel logo missing"):
            compositor.compose_storytime(
                source_video_path=str(src),
                hook_text="",
                output_path=str(out),
                narration_text="A narration.",
            )
