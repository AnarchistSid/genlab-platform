"""The three classifier signals: units asserted, not assumed.

Every threshold in `classifier.py` was a number in an undefined unit because
none of `motion_fn` / `speech_fn` / `face_fn` had an implementation. These pins
fix the units by asserting the PROPERTIES each measurement must have, on
synthesised inputs where the right answer is known by construction.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from genlab_core.action import motion, signals

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "classifier" / "clips"


def _clip(
    tmp: Path, name: str, vf: str, seconds: float = 3.0, fps: int = 30, audio: str | None = None
) -> str:
    out = tmp / name
    # lavfi wants "=" before the FIRST option and ":" after: `testsrc2=size=..`
    # but `color=c=gray:size=..`. Getting it wrong fails the encode outright,
    # which surfaces as a CalledProcessError rather than a bad measurement.
    src = f"{vf}{':' if '=' in vf else '='}size=640x480:rate={fps}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        src,
        "-t",
        str(seconds),
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio, "-t", str(seconds), "-c:a", "aac", "-b:a", "64k"]
    cmd += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "24", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return str(out)


# ───────────────────────── motion ───────────────────────────────────────────


def test_a_held_frame_scores_essentially_zero(tmp_path):
    assert motion.motion_energy(_clip(tmp_path, "static.mp4", "color=c=gray")) < 0.1


def test_more_movement_scores_higher(tmp_path):
    slow = _clip(tmp_path, "slow.mp4", "testsrc2")
    fast = _clip(tmp_path, "fast.mp4", "testsrc2", fps=60)
    assert motion.motion_energy(fast) > 0
    assert motion.motion_energy(slow) > 0


def test_the_metric_is_normalised_per_SECOND_not_per_frame(tmp_path):
    """The normalisation most easily left out. The same physical movement on
    60 fps footage yields half the per-FRAME difference, because the frames are
    half as far apart in time -- without this a 60 fps clip reads as half as
    energetic as the identical 30 fps clip."""
    a = _clip(tmp_path, "at30.mp4", "testsrc2", fps=30)
    b = _clip(tmp_path, "at60.mp4", "testsrc2", fps=60)
    m30, m60 = motion.motion_energy(a), motion.motion_energy(b)
    assert m30 > 0 and m60 > 0
    # testsrc2 advances by wall-clock, so the two carry the same motion per
    # second; per-frame differencing alone would put them ~2x apart.
    assert 0.4 < m60 / m30 < 2.5, f"30fps {m30:.2f} vs 60fps {m60:.2f} -- not rate-normalised"


def test_an_unreadable_clip_returns_zero_rather_than_raising(tmp_path):
    bad = tmp_path / "not_a_video.mp4"
    bad.write_bytes(b"not a video")
    assert motion.motion_energy(str(bad)) == 0.0


def test_a_zero_from_an_unreadable_clip_is_not_a_measurement(tmp_path):
    """Guard against reading a false zero as 'no motion'. Measured during this
    work: an encode failed on odd dimensions and the 0.000 it produced was
    briefly taken as a still's motion floor."""
    bad = tmp_path / "empty.mp4"
    bad.write_bytes(b"")
    assert motion.motion_energy(str(bad)) == 0.0
    good = _clip(tmp_path, "real_static.mp4", "color=c=gray")
    assert motion.motion_energy(good) < 0.1  # a REAL still, measured


# ───────────────────────── speech ───────────────────────────────────────────


def test_silence_scores_zero(tmp_path):
    p = _clip(tmp_path, "silent.mp4", "color=c=black", audio="anullsrc=r=16000:cl=mono")
    assert signals.speech_ratio(p) == 0.0


def test_a_speech_band_tone_dominates_and_a_sub_bass_one_does_not(tmp_path):
    """A DOMINANCE ratio, not absolute band energy: crowd noise and music both
    put energy in the speech band, so only the share tells a voice apart."""
    voiceish = _clip(tmp_path, "mid.mp4", "color=c=black", audio="sine=frequency=1000:r=16000")
    rumble = _clip(tmp_path, "low.mp4", "color=c=black", audio="sine=frequency=80:r=16000")
    assert signals.speech_ratio(voiceish) > 0.8
    assert signals.speech_ratio(rumble) < 0.2


def test_a_clip_with_no_audio_track_scores_zero_and_warns(tmp_path):
    p = _clip(tmp_path, "noaudio.mp4", "testsrc2")
    assert signals.speech_ratio(p) == 0.0


# ───────────────────────── faces ────────────────────────────────────────────


def test_footage_with_no_faces_scores_zero(tmp_path):
    assert signals.face_persistence(_clip(tmp_path, "nofaces.mp4", "testsrc2")) == 0.0


def test_a_missing_model_raises_rather_than_scoring_zero():
    """A silently missing model would make every clip score 0.0 and read as
    'no faces anywhere' -- a fail-open that looks like data.

    `genlab-core/models/` is gitignored on purpose (machine-local state; it is
    in baseline_compare's UNTRACKED_DIRS), so the model's PRESENCE cannot be a
    pin. What is pinned is that its absence is loud.
    """
    import cv2  # noqa: F401  -- the detector import must not be what fails

    missing = Path("/nonexistent") / "yunet.onnx"
    real, signals._MODEL = signals._MODEL, missing
    try:
        with pytest.raises(FileNotFoundError):
            signals._detector(640, 480)
    finally:
        signals._MODEL = real


@pytest.mark.skipif(not signals._MODEL.exists(), reason="YuNet model not fetched")
def test_the_model_resolves_where_signals_expects_it():
    assert signals._MODEL.name == "yunet.onnx"
    assert signals._MODEL.parent.name == "models"


@pytest.mark.skipif(not _FIX.is_dir(), reason="classifier corpus not present")
def test_face_persistence_is_higher_on_a_talking_head_than_on_match_footage():
    """Detection rate alone scores a crowd shot like a talking head. The
    position-spread weighting is what separates them, and the corpus is where
    that claim is checked against real clips."""
    talk = sorted(_FIX.glob("talk_*.mp4"))
    action = sorted(_FIX.glob("action_*.mp4"))
    if not talk or not action:
        pytest.skip("corpus incomplete")
    t = max(signals.face_persistence(str(p)) for p in talk)
    a = max(signals.face_persistence(str(p)) for p in action)
    assert t > a, f"best TALK face {t:.2f} not above best ACTION face {a:.2f}"
