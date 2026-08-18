"""Pin audio_replacer NARR-01 mix graph — both the byte-identical
2-input legacy shape (when narration off) and the 3-input NARR-01
shape (when VO landed).

Class of bug guarded here: pre-NARR-01 audio path must be BYTE-
IDENTICAL when narration is disabled. Any drift → all 5 niches
render differently overnight even with the canary off. Non-canary
regression is caught by this file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from genlab_core.media.audio_replacer import (
    AudioMixSpec,
    build_audio_mix_filtergraph,
    build_ffmpeg_command,
)


class TestLegacyPathUnchanged:
    """Byte-identical 2-input filtergraph + ffmpeg argv when
    narration_audio_path is None. Pin against the exact pre-NARR-01
    output string — any drift caught immediately.
    """

    def test_legacy_filtergraph_exact_string(self):
        """The pre-NARR-01 filter_complex string, byte-identical."""
        graph = build_audio_mix_filtergraph(
            source_duck_db=-12, music_bed_db=-6,
        )
        expected = (
            "[0:a]volume=-12dB[a1];"
            "[1:a]volume=-6dB[a2];"
            "[a1][a2]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        assert graph == expected

    def test_legacy_ffmpeg_argv_shape(self):
        """Argv shape must match pre-NARR-01: 2 inputs, no VO -i flag."""
        spec = AudioMixSpec(
            source_video_path=Path("/tmp/src.mp4"),
            music_bed_path=Path("/tmp/music.mp3"),
            output_path=Path("/tmp/out.mp4"),
        )
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        # Exactly 2 -i inputs (source + music)
        assert cmd.count("-i") == 2
        # No narration path anywhere
        assert not any("narration" in str(arg).lower() for arg in cmd)
        # Loudnorm NOT in the legacy filtergraph
        assert "loudnorm" not in " ".join(cmd)

    def test_niche_config_override_still_2input(self):
        """FrameDrift (anime) uses source_duck_db=-9 per their
        niche.yaml. Custom ducking must keep the 2-input shape."""
        graph = build_audio_mix_filtergraph(
            source_duck_db=-9, music_bed_db=-20,
        )
        assert "amix=inputs=2" in graph
        assert "amix=inputs=3" not in graph
        assert "loudnorm" not in graph


class TestNarrationPath:
    """3-input filtergraph shape + ffmpeg argv when VO landed."""

    def test_narration_filtergraph_has_three_inputs(self):
        graph = build_audio_mix_filtergraph(
            source_duck_db=-12, music_bed_db=-6,
            include_narration=True,
        )
        assert "[0:a]" in graph  # source
        assert "[1:a]" in graph  # music
        assert "[2:a]" in graph  # VO
        assert "amix=inputs=3" in graph

    def test_narration_filtergraph_has_loudnorm(self):
        """Post-amix EBU R128 normalization to target_lufs."""
        graph = build_audio_mix_filtergraph(
            source_duck_db=-12, music_bed_db=-6,
            include_narration=True, target_lufs=-14.0,
        )
        assert "loudnorm=I=-14" in graph
        # Reused ffmpeg_utils.build_loudnorm_filter defaults
        assert "TP=-1.5" in graph
        assert "LRA=11" in graph

    def test_music_pre_ducked_when_narration_present(self):
        """Music bed gets an ADDITIONAL vo_bed_duck_db under VO.
        With music_bed_db=-6 and vo_bed_duck_db=-8, total = -14 dB."""
        graph = build_audio_mix_filtergraph(
            source_duck_db=-12, music_bed_db=-6,
            include_narration=True, vo_bed_duck_db=-8,
        )
        assert "volume=-14dB" in graph  # -6 + -8

    def test_narration_ffmpeg_argv_has_three_inputs(self):
        spec = AudioMixSpec(
            source_video_path=Path("/tmp/src.mp4"),
            music_bed_path=Path("/tmp/music.mp3"),
            output_path=Path("/tmp/out.mp4"),
            narration_audio_path=Path("/tmp/vo.mp3"),
        )
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        assert cmd.count("-i") == 3
        assert "/tmp/vo.mp3" in cmd

    def test_narration_none_falls_back_to_legacy(self):
        """Setting narration_audio_path=None on the spec must produce
        the 2-input command exactly (byte-identical to pre-NARR-01)."""
        spec = AudioMixSpec(
            source_video_path=Path("/tmp/src.mp4"),
            music_bed_path=Path("/tmp/music.mp3"),
            output_path=Path("/tmp/out.mp4"),
            narration_audio_path=None,  # explicit
        )
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg")
        assert cmd.count("-i") == 2
        # Filter shape is 2-input
        filter_idx = cmd.index("-filter_complex") + 1
        assert "amix=inputs=2" in cmd[filter_idx]
        assert "loudnorm" not in cmd[filter_idx]


class TestCustomLufsTarget:
    """target_lufs is a niche.yaml knob; must reach loudnorm arg."""

    def test_custom_lufs_reaches_filtergraph(self):
        graph = build_audio_mix_filtergraph(
            source_duck_db=-12, music_bed_db=-6,
            include_narration=True, target_lufs=-16.0,
        )
        assert "loudnorm=I=-16" in graph


class TestVoBedDuckExtremes:
    """Sanity: extreme vo_bed_duck_db values still produce valid graph."""

    @pytest.mark.parametrize("duck", [-3, -8, -14, -20])
    def test_various_duck_values(self, duck):
        graph = build_audio_mix_filtergraph(
            source_duck_db=-12, music_bed_db=-6,
            include_narration=True, vo_bed_duck_db=duck,
        )
        expected_music_volume = -6 + duck
        assert f"volume={expected_music_volume}dB" in graph
