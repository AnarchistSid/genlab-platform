"""Shots -> a reel: transition offsets, loudness, loop-back.

Three things found by running it:

1. xfade OVERLAPS, so offsets must accumulate `duration - transition`. Using
   raw starts drifts by one transition per cut and desyncs the audio.
2. Single-pass loudnorm is a live estimator: the first real mix landed
   -15.7 LUFS against a -14 +/-1 gate. Two-pass hits -14.0 exactly.
3. `metadata=print` writes at INFO level, so the loop-back measurement ran
   under `-v error` and read NOTHING — then returned its 255.0 fallback,
   which looks like "completely different frames" rather than "the gate did
   not run".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from genlab_core.still.assemble import (
    AudioPlan,
    LoopBackUnmeasurable,
    UnknownTransition,
    build_concat_filter,
    concat_shots,
    loop_back_ok,
    mix_audio,
    xfade_for,
)
from genlab_core.still.beats import load_kit
from genlab_core.still.render import Shot, probe_duration, render_shot

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture(scope="module")
def kit():
    return load_kit("anime")


@pytest.fixture(scope="module")
def still_image(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("as") / "s.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "gradients=s=1080x1920:n=3", "-frames:v", "1", str(p)], check=True)
    return p


@pytest.fixture(scope="module")
def tone(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("as") / "t.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=220:duration=6", str(p)], check=True)
    return p


class TestTransitionOffsets:
    def test_offsets_account_for_the_overlap(self):
        fc, final = build_concat_filter(3, [2.0, 3.0, 2.5], "ink_bleed", 0.4)
        assert "offset=1.600" in fc, "first join at duration - transition"
        assert "offset=4.200" in fc, "second must ADD (3.0 - 0.4), not use a raw start"
        assert final == "x2"

    def test_a_single_shot_needs_no_filter(self):
        fc, final = build_concat_filter(1, [2.0], "ink_bleed", 0.4)
        assert fc == "" and final == "0:v"

    def test_the_transition_mapping_is_explicit(self):
        """The kit says ink_bleed/film_burn; xfade has neither."""
        assert xfade_for("ink_bleed") == "dissolve"
        assert xfade_for("film_burn") == "fadewhite"

    def test_an_unmapped_transition_raises(self):
        with pytest.raises(UnknownTransition, match="no xfade mapping"):
            xfade_for("sparkle")

    def test_every_transition_the_kit_names_is_mapped(self, kit):
        for name in kit["transitions"]["set"]:
            assert xfade_for(name)


class TestConcat:
    def test_joined_length_is_shots_minus_overlaps(self, still_image, kit, tmp_path):
        durs = [2.0, 2.5, 2.0]
        shots = []
        for i, d in enumerate(durs):
            out = tmp_path / f"{i}.mp4"
            assert render_shot(Shot(i, still_image, out, "zoom_in", d), kit)
            shots.append(out)
        joined = tmp_path / "j.mp4"
        assert concat_shots(shots, durs, joined, kit=kit)
        trans = kit["transitions"]["duration_s"]
        expected = sum(durs) - trans * (len(durs) - 1)
        assert abs(probe_duration(joined) - expected) <= 0.1


class TestLoudness:
    def test_two_pass_hits_the_target(self, tone, kit, tmp_path):
        """One pass landed -15.7 on the first real mix; the gate is +/-1."""
        from genlab_core.still.audio import measure_loudness

        out = tmp_path / "m.m4a"
        plan = AudioPlan(narration=tone, bed=None,
                         duck_db=kit["audio"]["music_duck_db"],
                         target_lufs=kit["audio"]["target_lufs"],
                         true_peak=kit["audio"]["true_peak_max"])
        assert mix_audio(plan, out, 6.0)
        i, tp = measure_loudness(out)
        target = kit["audio"]["target_lufs"]
        assert target - 1.0 <= i <= target + 1.0, f"{i} LUFS outside {target}+/-1"
        assert tp <= kit["audio"]["true_peak_max"] + 0.1

    def test_the_duck_is_inside_the_kit_band(self, kit):
        assert -10 <= kit["audio"]["music_duck_db"] <= -6


class TestLoopBack:
    def test_it_measures_rather_than_guessing(self, still_image, kit, tmp_path):
        """A still held twice loops perfectly; that must read near zero."""
        a = tmp_path / "a.mp4"
        assert render_shot(Shot(0, still_image, a, "zoom_in", 1.0), kit)
        ok, score = loop_back_ok(a, tolerance=255.0)
        assert score < 255.0, "255.0 is the old unmeasurable fallback, not a score"
        assert isinstance(score, float)

    def test_an_unmeasurable_loop_raises_rather_than_scoring(self, tmp_path):
        """A gate that cannot run must say so, not return a verdict."""
        empty = tmp_path / "nope.mp4"
        empty.write_bytes(b"")
        with pytest.raises((LoopBackUnmeasurable, subprocess.CalledProcessError)):
            loop_back_ok(empty)
