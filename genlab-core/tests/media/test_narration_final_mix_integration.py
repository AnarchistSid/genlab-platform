"""NARR-05 (2026-08-19) — end-to-end proof that the voice-over actually
lands in the rendered audio.

Why this exists
===============
The NARR-01 unit tests assert the *filtergraph string* and the *ffmpeg
argv*. Both passed for the whole life of the canary while every published
reel was silent, because the defect was neither in the graph nor in the
command — it was that ``GenerateAudio`` ran after its only consumer, so
``narration_audio_path`` was always ``None`` and the correct 3-input graph
was simply never selected.

A string assertion cannot catch that. This test invokes real ffmpeg and
measures the rendered output, so it fails if the VO is absent for ANY
reason: wrong graph, wrong argv, wrong stage order, silent fallback.

Method
======
Three tones, deliberately far apart in frequency:

  * source video audio ... 200 Hz
  * music bed ............ 440 Hz
  * voice-over ........... 1800 Hz   <- the signal under test

Render twice from identical source + music: once WITH the VO input, once
WITHOUT (the legacy 2-input path). Bandpass both outputs around 1800 Hz
and compare energy.

The no-VO render is the control, which makes the assertion
self-calibrating — no absolute dB threshold to tune, and no dependency on
loudnorm's exact gain. If the VO reached the mix, the 1800 Hz band is
dramatically hotter than the control. If it silently degraded, the two
renders are indistinguishable.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from genlab_core.media.audio_replacer import AudioMixSpec, build_ffmpeg_command
from genlab_core.media.ffmpeg import get_ffmpeg_binary

pytestmark = pytest.mark.integration

_VO_HZ = 1800
_DURATION = "3"


def _ffmpeg() -> str:
    binary = get_ffmpeg_binary()
    if not binary:
        pytest.skip("ffmpeg not available")
    return binary


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        pytest.fail(
            f"ffmpeg failed ({proc.returncode})\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr tail:\n{proc.stderr[-2500:]}"
        )
    return proc


def _make_source_video(ffmpeg: str, path: Path) -> None:
    """Video + a 200 Hz 'source audio' track."""
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x568:rate=15:duration={_DURATION}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=200:duration={_DURATION}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )


def _make_tone(ffmpeg: str, path: Path, hz: int) -> None:
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={hz}:duration={_DURATION}",
            "-c:a",
            "libmp3lame",
            str(path),
        ]
    )


def _band_energy_db(ffmpeg: str, media: Path, hz: int) -> float:
    """Mean volume inside a narrow band around ``hz``, in dBFS."""
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(media),
            "-af",
            f"bandpass=f={hz}:width_type=h:w=120,volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", proc.stderr)
    if not match:
        pytest.fail(
            "volumedetect produced no mean_volume for "
            f"{media.name}\nstderr tail:\n{proc.stderr[-2000:]}"
        )
    return float(match.group(1))


class TestFinalMixContainsVO:
    def test_vo_energy_present_in_rendered_audio(self, tmp_path: Path):
        ffmpeg = _ffmpeg()

        source = tmp_path / "source.mp4"
        music = tmp_path / "music.mp3"
        vo = tmp_path / "vo.mp3"
        with_vo = tmp_path / "out_with_vo.mp4"
        without_vo = tmp_path / "out_without_vo.mp4"

        _make_source_video(ffmpeg, source)
        _make_tone(ffmpeg, music, 440)
        _make_tone(ffmpeg, vo, _VO_HZ)

        # Control: the legacy 2-input path (what prod was silently doing).
        _run(
            build_ffmpeg_command(
                AudioMixSpec(
                    source_video_path=source,
                    music_bed_path=music,
                    output_path=without_vo,
                ),
                ffmpeg,
            )
        )

        # Under test: the NARR-01 3-input path.
        _run(
            build_ffmpeg_command(
                AudioMixSpec(
                    source_video_path=source,
                    music_bed_path=music,
                    output_path=with_vo,
                    narration_audio_path=vo,
                ),
                ffmpeg,
            )
        )

        control_db = _band_energy_db(ffmpeg, without_vo, _VO_HZ)
        narrated_db = _band_energy_db(ffmpeg, with_vo, _VO_HZ)

        assert narrated_db > control_db + 20.0, (
            "Voice-over did not survive into the final mix. "
            f"{_VO_HZ} Hz band energy: narrated={narrated_db:.1f} dB vs "
            f"control(no VO)={control_db:.1f} dB. A delta this small means "
            "the render silently fell back to the legacy 2-input mix — the "
            "exact prod failure NARR-05 fixed."
        )

    def test_narrated_render_is_48khz(self, tmp_path: Path):
        """CLAUDE.md STRICT VIDEO REQUIREMENTS mandate AAC 48 kHz stereo.

        ``loudnorm`` — which only runs on the narration branch — resamples
        internally and propagates a non-48k rate to the encoder. A
        pre-verification render of a real BB reel came out at 96 kHz.
        Measured on the rendered file, not asserted on the argv, so a
        future filtergraph change that reintroduces the resample is caught
        even if the ``-ar`` flag survives.
        """
        ffmpeg = _ffmpeg()

        source = tmp_path / "source.mp4"
        music = tmp_path / "music.mp3"
        vo = tmp_path / "vo.mp3"
        out = tmp_path / "narrated.mp4"

        _make_source_video(ffmpeg, source)
        _make_tone(ffmpeg, music, 440)
        _make_tone(ffmpeg, vo, _VO_HZ)
        _run(build_ffmpeg_command(
            AudioMixSpec(
                source_video_path=source,
                music_bed_path=music,
                output_path=out,
                narration_audio_path=vo,
            ),
            ffmpeg,
        ))

        probe = subprocess.run(
            [
                ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
                "-select_streams", "a", "-show_entries", "stream=sample_rate",
                "-of", "default=noprint_wrappers=1:nokey=1", str(out),
            ],
            capture_output=True, text=True, timeout=60,
        )
        rate = (probe.stdout or "").strip()
        assert rate == "48000", (
            f"narrated mix rendered at {rate} Hz, not 48000 Hz — violates "
            "the AAC 48kHz stereo requirement every platform spec assumes"
        )

    def test_legacy_render_has_no_vo_band(self, tmp_path: Path):
        """Guard the control itself.

        If the 200 Hz / 440 Hz tones leaked into the 1800 Hz band, the
        comparison above would be measuring noise rather than the VO, and
        could pass for the wrong reason.
        """
        ffmpeg = _ffmpeg()

        source = tmp_path / "source.mp4"
        music = tmp_path / "music.mp3"
        out = tmp_path / "legacy.mp4"

        _make_source_video(ffmpeg, source)
        _make_tone(ffmpeg, music, 440)
        _run(
            build_ffmpeg_command(
                AudioMixSpec(
                    source_video_path=source,
                    music_bed_path=music,
                    output_path=out,
                ),
                ffmpeg,
            )
        )

        assert _band_energy_db(ffmpeg, out, _VO_HZ) < -40.0, (
            "source/music tones are bleeding into the VO detection band — "
            "the control is not clean, so the presence assertion would be "
            "measuring leakage instead of narration"
        )


class TestDuckingDynamics:
    """NARR-10 (2026-08-20) — the gap that let a duck-less graph ship.

    ``TestFinalMixContainsVO`` asserts the voice-over ARRIVES. It passed
    unchanged on a graph with no ducking of any kind, which is exactly how a
    render with two simultaneous speech layers reached the operator listen and
    failed there ("too much overlapped audio").

    Lesson, recorded: **a presence test passes on a graph with no dynamics.
    Assert the behaviour, not the ingredient.**

    Method note — why this measures WITHIN one render rather than across two:
    the obvious test is "source is quieter in the VO-on render than the VO-off
    render." That does not work. ``loudnorm`` runs only on the narration
    branch, so it lifts the whole mix toward -14 LUFS and the source band
    measures ~4.5 dB *louder* in the VO-on render despite being ducked 11 dB
    harder. Measured, not assumed. The intelligibility-relevant quantity is how
    far the source sits below the voice in the single file a listener hears.
    """

    def test_voice_sits_well_above_source_in_final_mix(self, tmp_path: Path):
        """VO must dominate the source clip's own audio.

        Threshold derived by measurement on this exact fixture, not invented:

            source at  -9 dB (pre-NARR-10)  ->  VO above source =  8.30 dB
            source at -20 dB (NARR-10)      ->  VO above source = 17.10 dB

        15 dB sits between them — it fails the graph that shipped the defect
        and passes the fix with ~2 dB of headroom.
        """
        ffmpeg = _ffmpeg()

        source = tmp_path / "source.mp4"
        music = tmp_path / "music.mp3"
        vo = tmp_path / "vo.mp3"
        out = tmp_path / "narrated.mp4"

        _make_source_video(ffmpeg, source)   # source audio = 200 Hz
        _make_tone(ffmpeg, music, 440)
        _make_tone(ffmpeg, vo, _VO_HZ)       # 1800 Hz
        _run(build_ffmpeg_command(
            AudioMixSpec(
                source_video_path=source,
                music_bed_path=music,
                output_path=out,
                # Deliberately pass the pre-NARR-10 value: the narration branch
                # must override it. If the override regresses, this fails.
                source_duck_db=-9,
                narration_audio_path=vo,
            ),
            ffmpeg,
        ))

        source_db = _band_energy_db(ffmpeg, out, 200)
        vo_db = _band_energy_db(ffmpeg, out, _VO_HZ)
        margin = vo_db - source_db

        assert margin >= 15.0, (
            f"source clip audio is only {margin:.1f} dB below the voice-over "
            f"(source={source_db:.1f} dB, VO={vo_db:.1f} dB). Below ~15 dB the "
            "source's own dialogue stays intelligible and the reel carries two "
            "competing speech layers — the exact defect that failed the "
            "operator listen on 2026-08-19."
        )

    def test_caller_source_duck_is_overridden_on_narration_path(self):
        """The narration branch ignores the audio_ducking bandit arm.

        Pinned because it is a real behavioural override: whatever
        ``source_duck_db`` the caller supplies, the narrated graph emits the
        fixed deep duck. Discovering this by surprise later would be worse than
        the override itself.
        """
        from genlab_core.media.audio_replacer import (
            _NARRATION_SOURCE_DUCK_DB,
            build_audio_mix_filtergraph,
        )

        for caller_value in (-9, -12, -15):
            graph = build_audio_mix_filtergraph(
                caller_value, -6, include_narration=True,
            )
            assert f"volume={_NARRATION_SOURCE_DUCK_DB}dB[src]" in graph
            assert f"volume={caller_value}dB[src]" not in graph

    def test_no_sidechain_in_shipped_graph(self):
        """Spec, code and docs reconciled to one truth (NARR-10).

        The §3.2 spec called for ``sidechaincompress``; it was never
        implemented while the docstring described it for two days. The spec is
        retired, so assert the absence deliberately rather than leaving the
        discrepancy available to drift back.
        """
        from genlab_core.media.audio_replacer import build_audio_mix_filtergraph

        graph = build_audio_mix_filtergraph(-9, -6, include_narration=True)
        assert "sidechaincompress" not in graph
