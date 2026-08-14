"""Pin Phase 4.A session 2 audio feature extraction.

Synthesizes MP4s with audio via ffmpeg's lavfi audio sources:

  * ``sine=frequency=440`` — pure tone → LOW variance (flat drone)
  * ``anoisesrc=color=white`` — white noise → HIGH variance across bands
  * ``sine=frequency=200`` (below voice) — should give music-heavy skew
  * silent audio → dialogue_density near 0

Skips when ffmpeg isn't available on the test host.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from genlab_core.quality.audio_features import (
    FeatureResult,
    _parse_rms_levels,
    extract_audio_energy_variance,
    extract_dialogue_density,
    extract_music_to_voice_ratio,
)


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg not installed on test host",
)


# ── Synthetic-MP4 fixtures ────────────────────────────────────────


def _make_video_with_audio(
    tmp_path: Path, audio_filter: str, seconds: float = 2.0,
    filename: str = "clip.mp4",
) -> Path:
    """Build a black-video + given-audio MP4.

    audio_filter should already include duration if the source
    supports it (e.g., 'sine=frequency=440:d=2.0'). Caller wraps
    -shortest, so if the audio is longer than seconds it will
    still cut at the video length.

    Note: anullsrc + sine + anoisesrc + testsrc all use `=` for
    the FIRST parameter and `:` for subsequent. Appending `:d=N`
    to a bare filter name like 'anullsrc' produces malformed
    'anullsrc:d=N'; callers must include full filter args."""
    out = tmp_path / filename
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=30:d={seconds}",
            "-f", "lavfi", "-i", audio_filter,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-t", str(seconds),
            str(out),
        ],
        capture_output=True, check=True, timeout=15,
    )
    return out


def _make_video_no_audio(tmp_path: Path, seconds: float = 2.0) -> Path:
    """Video with no audio stream — should trigger the no_audio_stream
    fail-open path."""
    out = tmp_path / "noaudio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=30:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-an",  # explicitly strip audio
            str(out),
        ],
        capture_output=True, check=True, timeout=15,
    )
    return out


# ── FeatureResult shape / cold-start ──────────────────────────────


class TestColdStart:
    def test_missing_file_all_extractors(self, tmp_path):
        p = tmp_path / "nope.mp4"
        for fn in (
            extract_audio_energy_variance,
            extract_dialogue_density,
            extract_music_to_voice_ratio,
        ):
            r = fn(p)
            assert isinstance(r, FeatureResult)
            assert r.ok is False
            assert r.reason == "file_not_found"

    def test_no_audio_stream_all_extractors(self, tmp_path):
        vid = _make_video_no_audio(tmp_path)
        for fn in (
            extract_audio_energy_variance,
            extract_dialogue_density,
            extract_music_to_voice_ratio,
        ):
            r = fn(vid)
            assert r.ok is False
            assert r.reason == "no_audio_stream"


# ── extract_audio_energy_variance ────────────────────────────────


class TestEnergyVariance:
    def test_pure_tone_low_variance(self, tmp_path):
        """A 440Hz sine wave is the most stable possible signal —
        variance should be near 0."""
        vid = _make_video_with_audio(tmp_path, "sine=frequency=440:beep_factor=0:d=2.0")
        r = extract_audio_energy_variance(vid)
        assert r.ok is True
        assert r.score < 0.1

    def test_pulsed_vs_constant_ranking(self, tmp_path):
        """A pulsed (beep_factor=1) sine has PROMINENT variance
        because RMS swings hard between beep-on and beep-off
        windows. A constant sine (beep_factor=0) is much flatter.
        The pin: pulsed > constant. Direction, not magnitude.

        This originally used white-noise-vs-sine which failed
        because white noise is TIME-uniform (RMS is stable
        window-to-window even though spectral content varies).
        The lesson: RMS variance measures amplitude modulation,
        not spectral variety."""
        constant = _make_video_with_audio(
            tmp_path, "sine=frequency=440:beep_factor=0:d=2.0",
            filename="constant.mp4",
        )
        pulsed = _make_video_with_audio(
            tmp_path, "sine=frequency=440:beep_factor=4:d=3.0",
            filename="pulsed.mp4",
            seconds=3.0,  # need >=3s to fit multiple beep cycles
        )
        c_r = extract_audio_energy_variance(constant)
        p_r = extract_audio_energy_variance(pulsed)
        assert c_r.ok and p_r.ok
        assert p_r.score >= c_r.score


# ── extract_dialogue_density ─────────────────────────────────────


class TestDialogueDensity:
    def test_constant_audio_near_1(self, tmp_path):
        """Constant sine → no silence → density ~1."""
        vid = _make_video_with_audio(tmp_path, "sine=frequency=440:d=2.0")
        r = extract_dialogue_density(vid)
        assert r.ok is True
        assert r.score >= 0.90

    def test_silent_audio_density_near_0(self, tmp_path):
        """anullsrc = silence → all silence → density ~0."""
        vid = _make_video_with_audio(tmp_path, "anullsrc=r=44100:d=2.0")
        r = extract_dialogue_density(vid)
        assert r.ok is True
        # anullsrc emits perfect silence; silencedetect should
        # register almost the entire duration
        assert r.score <= 0.10


# ── extract_music_to_voice_ratio ─────────────────────────────────


class TestMusicToVoiceRatio:
    def test_voice_band_tone_leans_voice(self, tmp_path):
        """A 1000 Hz sine is squarely in the voice band. voice_rms
        should be near total_rms → delta near 0 → score near 0.5."""
        vid = _make_video_with_audio(tmp_path, "sine=frequency=1000:beep_factor=0:d=2.0")
        r = extract_music_to_voice_ratio(vid)
        assert r.ok is True
        # Score should be around 0.5, not extreme
        assert 0.30 <= r.score <= 0.70

    def test_out_of_band_tone_leans_music(self, tmp_path):
        """A 100 Hz sine is BELOW the voice band. voice_rms should be
        much lower than total_rms → positive delta → score > 0.5.

        (Below voice = "music" in the ratio's naming.)"""
        vid = _make_video_with_audio(tmp_path, "sine=frequency=100:beep_factor=0:d=2.0")
        r = extract_music_to_voice_ratio(vid)
        assert r.ok is True
        # Below-voice-band signal should NOT score more voice-heavy
        # than a voice-band signal — the pin is directional
        voice_vid = _make_video_with_audio(
            tmp_path, "sine=frequency=1000:beep_factor=0:d=2.0",
            filename="voice.mp4",
        )
        voice_r = extract_music_to_voice_ratio(voice_vid)
        assert voice_r.ok is True
        # Out-of-voice-band should be at least as music-heavy as in-band
        assert r.score >= voice_r.score - 0.05


# ── Parser unit tests ────────────────────────────────────────────


class TestRmsParser:
    def test_parses_valid_metadata_lines(self):
        stderr = (
            "[Parsed_astats_1 @ 0x123] lavfi.astats.Overall.RMS_level=-24.5\n"
            "some other line\n"
            "[Parsed_astats_1 @ 0x456] lavfi.astats.Overall.RMS_level=-18.2\n"
        )
        values = _parse_rms_levels(stderr)
        assert values == [-24.5, -18.2]

    def test_skips_neg_inf_sentinel(self):
        """astats emits '-inf' for silent frames — worth filtering
        so a mostly-silent video doesn't dominate the variance."""
        stderr = (
            "[Parsed_astats_1 @ 0x123] lavfi.astats.Overall.RMS_level=-inf\n"
            "[Parsed_astats_1 @ 0x456] lavfi.astats.Overall.RMS_level=-24.5\n"
        )
        values = _parse_rms_levels(stderr)
        # -inf parses as float but is < -100 → filtered
        assert values == [-24.5]

    def test_no_matches_returns_empty(self):
        assert _parse_rms_levels("nothing to parse here") == []
