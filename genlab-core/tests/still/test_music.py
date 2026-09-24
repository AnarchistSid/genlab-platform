"""Pins for the music measurement and the §3 mix gates.

The pin that matters most is `test_sub_share_definition`: two defensible
readings of "sub share" differ by 3x on the same audio, and the 25% gate is
stated against the magnitude one. Taking the threshold without its method
made every candidate pass.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from genlab_core.still import music as M

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone(path: Path, spec: str, seconds: float = 6.0) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", spec,
                    "-t", str(seconds), "-c:a", "pcm_s16le", "-ar", "44100",
                    "-y", str(path)], check=True)
    return path


def test_sub_share_definition(tmp_path):
    """Magnitude share, not power share — the gate is stated against it."""
    low = _tone(tmp_path / "low.wav", "sine=frequency=60:sample_rate=44100")
    high = _tone(tmp_path / "high.wav", "sine=frequency=3000:sample_rate=44100")
    assert M.sub_share(low) > 0.5
    assert M.sub_share(high) < 0.05


def test_the_two_sub_definitions_diverge_on_broadband_audio(tmp_path):
    """Measured on the v2 reels: power 41-50%, magnitude 14-17%.

    The gap only appears on broadband content, where magnitude spreads across
    many high bins while power stays concentrated low. On a pure tone the
    relationship INVERTS, so a pure tone cannot stand in for music here.
    """
    mixed = tmp_path / "mixed.wav"
    subprocess.run(["ffmpeg", "-v", "error",
                    "-f", "lavfi", "-i", "sine=frequency=55:sample_rate=44100",
                    "-f", "lavfi", "-i", "anoisesrc=color=white:sample_rate=44100",
                    "-filter_complex", "[0:a]volume=0dB[a];[1:a]volume=-12dB[b];"
                                       "[a][b]amix=inputs=2:normalize=0",
                    "-t", "6", "-c:a", "pcm_s16le", "-y", str(mixed)], check=True)
    assert M.sub_power_share(mixed) > M.sub_share(mixed), (
        f"power {M.sub_power_share(mixed):.3f} magnitude {M.sub_share(mixed):.3f}")


def test_find_drop_needs_a_sustained_step(tmp_path):
    quiet = _tone(tmp_path / "q.wav", "sine=frequency=60:sample_rate=44100", 6.0)
    loud = _tone(tmp_path / "l.wav", "sine=frequency=60:sample_rate=44100", 6.0)
    step = tmp_path / "step.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(quiet), "-i", str(loud),
                    "-filter_complex",
                    "[0:a]volume=-24dB[a];[1:a]volume=0dB[b];[a][b]concat=n=2:v=0:a=1",
                    "-c:a", "pcm_s16le", "-y", str(step)], check=True)
    t, db, _ = M.find_drop(step)
    assert t is not None and db > M.MIN_DROP_STEP_DB
    assert t == pytest.approx(6.0, abs=1.0)


def test_a_steady_tone_has_no_drop(tmp_path):
    flat = _tone(tmp_path / "flat.wav", "sine=frequency=60:sample_rate=44100", 12.0)
    t, db, _ = M.find_drop(flat)
    assert t is None, f"a steady tone reported a drop at {t}s (+{db:.1f} dB)"


def test_a_single_hit_is_not_a_drop(tmp_path):
    """The statistic is a level SHIFT; one loud frame must not move it."""
    base = _tone(tmp_path / "b.wav", "sine=frequency=60:sample_rate=44100", 12.0)
    hit = tmp_path / "hit.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(base), "-af",
                    "volume='1+40*between(t,6.0,6.05)':eval=frame",
                    "-c:a", "pcm_s16le", "-y", str(hit)], check=True)
    t, _, _ = M.find_drop(hit)
    assert t is None


def test_align_trims_when_the_windup_is_short(tmp_path):
    bed = _tone(tmp_path / "bed.wav", "sine=frequency=60:sample_rate=44100", 20.0)
    info = M.align_bed(bed, drop_t=8.0, impact_rel=5.0, reel_s=15.0,
                       dest=tmp_path / "out.m4a")
    assert info["trim_s"] == pytest.approx(3.0) and info["preroll_s"] == 0.0


def test_align_prerolls_when_the_windup_is_long(tmp_path):
    bed = _tone(tmp_path / "bed.wav", "sine=frequency=60:sample_rate=44100", 20.0)
    info = M.align_bed(bed, drop_t=8.0, impact_rel=14.0, reel_s=25.0,
                       dest=tmp_path / "out.m4a")
    assert info["preroll_s"] == pytest.approx(6.0) and info["trim_s"] == 0.0


def test_beats_are_phase_locked_to_the_drop():
    beats = M.beat_times(Path("x"), bpm=150.0, drop_t=8.0, reel_s=20.0, offset=12.0)
    assert any(abs(b - 12.0) < 1e-6 for b in beats), "the drop must itself be a beat"
    period = 60.0 / 150.0
    assert all(abs((b - 12.0) / period - round((b - 12.0) / period)) < 1e-6 for b in beats)


def test_mix_gate_rejects_a_title_louder_than_the_hit():
    bad = M.MixCheck(sub_share=0.30, loudest_s=1.5, impact_s=9.0,
                     lufs=-14.0, true_peak=-1.5)
    good = M.MixCheck(sub_share=0.30, loudest_s=9.2, impact_s=9.0,
                      lufs=-14.0, true_peak=-1.5)
    assert not bad.passes and not bad.gates["loudest_is_the_hit"]
    assert good.passes


def test_hook_goes_on_a_bar_line_not_the_nearest_beat():
    """At 150 BPM a bar is 1.6 s; the nearest beat can be three beats off it."""
    beats = M.beat_times(Path("x"), bpm=150.0, drop_t=8.0, reel_s=20.0, offset=12.0)
    at = M.first_downbeat_after(0.30, beats, anchor=12.0)
    bar = 4 * 60.0 / 150.0
    assert abs((at - 12.0) / bar - round((at - 12.0) / bar)) < 1e-6
    assert at >= 0.30
    assert at != M.snap_to_beat(0.30, beats) or abs(at - 0.30) < 1e-9


def test_snap_cuts_recomputes_the_impact_after_snapping():
    """Aligning a bed to a PRE-snap impact puts the drop off the hit."""
    shots = [{"start": 0.0, "end": 2.13}, {"start": 10.0, "end": 13.07},
             {"start": 20.0, "end": 24.44}]
    impact_rel, total = M.snap_cuts(shots, bpm=150.0, impact_source_t=11.5)
    period = 60.0 / 150.0
    assert impact_rel is not None
    # the impact sits inside shot 2, after that shot's snapped start
    assert abs(impact_rel - (shots[1]["reel_start"] + 1.5)) < 1e-6
    for sh in shots[:-1]:
        edge = sh["reel_start"] + (sh["end"] - sh["start"])
        assert abs(edge / period - round(edge / period)) < 1e-6, "cut is off grid"


def test_snap_cuts_never_drops_a_short_shot():
    shots = [{"start": 0.0, "end": 0.32}, {"start": 5.0, "end": 5.31}]
    before = len(shots)
    _, total = M.snap_cuts(shots, bpm=150.0, impact_source_t=99.0)
    assert len(shots) == before
    assert total > 0.5


def test_usable_drop_is_the_nearest_one_not_the_biggest(tmp_path):
    """Phonk repeats its drop; taking only the largest loses the usable one."""
    seg = []
    for i, db in enumerate((-30, -2, -30, -6)):
        p = tmp_path / f"s{i}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=60:sample_rate=44100", "-t", "5",
                        "-af", f"volume={db}dB", "-c:a", "pcm_s16le", "-y", str(p)],
                       check=True)
        seg.append(p)
    joined = tmp_path / "j.wav"
    lst = tmp_path / "l.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in seg))
    subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c:a", "pcm_s16le", "-y", str(joined)], check=True)
    drops = M.find_drops(joined)
    assert len(drops) >= 2, f"only found {drops}"
    # the biggest step is the first one; the second is the one near 15 s
    assert max(drops, key=lambda d: d[1])[0] < 10.0
    assert any(abs(t - 15.0) <= 2.0 for t, _ in drops)


def _click_track(path: Path, bpm: float, seconds: float = 20.0) -> Path:
    period = 60.0 / bpm
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=60:sample_rate=44100", "-t", str(seconds),
                    "-af", f"volume='if(lt(mod(t,{period:.4f}),0.05),4,0.02)':eval=frame",
                    "-c:a", "pcm_s16le", "-y", str(path)], check=True)
    return path


def test_tempo_resolves_the_octave_both_ways(tmp_path):
    """Widening the search band does not resolve an octave, it moves it.

    A flux fit scores mean onset strength at sampled positions, so HALVING
    the tempo can score better by sampling the stronger subset -- which is
    how four beds demonstrably at 148/148/140/150 read as 74 BPM.
    """
    fast = _click_track(tmp_path / "fast.wav", 148.0)
    slow = _click_track(tmp_path / "slow.wav", 74.0)
    assert M.detect_tempo(fast) == pytest.approx(148.0, rel=0.06)
    assert M.detect_tempo(slow) == pytest.approx(74.0, rel=0.06)


def test_tempo_gate_rejects_a_half_tempo_generation(tmp_path):
    slow = _click_track(tmp_path / "slow.wav", 74.0)
    assert not M.tempo_ok(M.detect_tempo(slow, 148.0), 148.0)
    fast = _click_track(tmp_path / "fast.wav", 148.0)
    assert M.tempo_ok(M.detect_tempo(fast, 148.0), 148.0)


def _bed(path: Path, seconds: float, bpm: float = 150.0) -> Path:
    period = 60.0 / bpm
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=60:sample_rate=44100", "-t", str(seconds),
                    "-af", f"volume='if(lt(mod(t,{period:.4f}),0.05),4,0.3)':eval=frame",
                    "-c:a", "pcm_s16le", "-y", str(path)], check=True)
    return path


def test_fit_stretches_before_it_prerolls(tmp_path):
    """A small stretch is cheaper than a silent opening, so it goes first."""
    bed = _bed(tmp_path / "b.wav", 30.0)
    info = M.fit_bed(bed, drop_t=10.0, impact_rel=10.3, reel_s=20.0, bpm=150.0,
                     dest=tmp_path / "o.m4a")
    assert info["preroll_s"] == 0.0 and info["trim_s"] == 0.0
    assert -M.MAX_STRETCH * 100 <= info["stretch_pct"] < 0


def test_fit_never_stretches_past_the_cap(tmp_path):
    bed = _bed(tmp_path / "b.wav", 30.0)
    info = M.fit_bed(bed, drop_t=5.0, impact_rel=20.0, reel_s=30.0, bpm=150.0,
                     dest=tmp_path / "o.m4a")
    assert abs(info["stretch_pct"]) <= M.MAX_STRETCH * 100 + 1e-6
    assert info["preroll_s"] > 0, "the rest must be absorbed by pre-roll, not more stretch"


def test_fit_trims_the_intro_when_the_drop_lands_late(tmp_path):
    bed = _bed(tmp_path / "b.wav", 30.0)
    info = M.fit_bed(bed, drop_t=18.0, impact_rel=6.0, reel_s=20.0, bpm=150.0,
                     dest=tmp_path / "o.m4a")
    assert info["trim_s"] > 5.0 and info["preroll_s"] == 0.0


def test_fit_loops_bar_aligned_when_the_track_runs_out(tmp_path):
    """Not exercised by the four pilot reels; pinned so it is not untested."""
    bed = _bed(tmp_path / "b.wav", 12.0)
    out = tmp_path / "o.m4a"
    info = M.fit_bed(bed, drop_t=4.0, impact_rel=4.0, reel_s=28.0, bpm=150.0, dest=out)
    assert info["loops"] >= 1 and info["loop_bars"] >= 1
    from genlab_core.still.reference import duration_s
    assert duration_s(out) == pytest.approx(28.0, abs=0.3)


def test_the_window_is_never_shortened_to_meet_the_drop(tmp_path):
    """fit_bed takes reel_s as GIVEN; it has no way to shorten the reel."""
    import inspect
    src = inspect.getsource(M.fit_bed)
    assert "reel_s =" not in src, "fit_bed must not reassign the reel length"


def test_melody_similarity_separates_a_copy_from_a_style_match(tmp_path):
    """Calibrated on real material: unrelated 0.281-0.411, identical 1.000."""
    a = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=220:sample_rate=44100", "-t", "6",
                    "-c:a", "pcm_s16le", "-y", str(a)], check=True)
    b = tmp_path / "b.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=233:sample_rate=44100", "-t", "6",
                    "-c:a", "pcm_s16le", "-y", str(b)], check=True)
    assert M.chroma_similarity(a, a) > 0.95, "a track must match itself"
    assert M.MAX_MELODY_SIMILARITY < 1.0
    # a different pitch class is a different tune
    assert M.chroma_similarity(a, b) < M.chroma_similarity(a, a)
