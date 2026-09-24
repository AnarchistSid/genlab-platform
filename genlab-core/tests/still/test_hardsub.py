"""Pins for the §3 hardsub gate.

These render real frames and run the real OCR rather than mocking it: the
defects this gate has already had were both in what tesseract actually
returns off a band, which a mock cannot reproduce. The first version of
this gate flagged a clean window at 92% because it read the right-aligned
copyright notice, so that exact layout is the first pin.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from genlab_core.still import hardsub as H

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None or shutil.which("ffmpeg") is None,
    reason="needs tesseract + ffmpeg",
)

_CREDIT = "(c) Koyoharu Gotoge / SHUEISHA, Aniplex, ufotable"
_LINE = "mi hechiceria lo trae a la realidad"


def _clip(path: Path, *, credit: bool = False, sub: bool = False) -> Path:
    """A 4 s 1920x1080 clip, optionally with corner credit and centred sub."""
    draws = []
    if credit:
        draws.append(
            f"drawtext=text='{_CREDIT}':x=w-tw-20:y=h-th-12:"
            f"fontsize=22:fontcolor=white"
        )
    if sub:
        # Only across the middle of the clip -- a subtitle that ran the
        # whole clip would BE furniture by the gate's own definition.
        draws.append(
            f"drawtext=text='{_LINE}':x=(w-tw)/2:y=h-th-60:"
            f"fontsize=44:fontcolor=white:box=1:boxcolor=black@0.35:"
            f"enable='between(t,3,7)'"
        )
    vf = ",".join(draws) if draws else "null"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", "color=c=#303845:s=1920x1080:d=10:r=12", "-vf", vf,
         "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path


def test_corner_credit_alone_does_not_trip_the_gate(tmp_path):
    """The defect that shipped: a clean window read 92% subbed."""
    clip = _clip(tmp_path / "credit.mp4", credit=True)
    ok, frac, _ = H.check(clip, 3.5, 6.5)
    assert ok, f"corner credit read as subtitles at {frac:.0%}"


def test_centred_subtitle_trips_the_gate(tmp_path):
    clip = _clip(tmp_path / "sub.mp4", credit=True, sub=True)
    ok, frac, lines = H.check(clip, 3.5, 6.5)
    assert not ok and frac > H.MAX_SUBBED_FRAC
    assert any("realidad" in ln for ln in lines)


def test_static_tokens_are_treated_as_furniture(tmp_path):
    """A token in >=80% of frames is furniture, whatever it says."""
    scans = [H.SubScan(float(i), "Weekly Shonen Jump presents") for i in range(10)]
    assert H.subbed_fraction(scans) == 0.0
    assert scans[0].has_sub, "the same text is a line when it is not everywhere"


def test_a_two_word_fragment_is_noise(tmp_path):
    assert not H.SubScan(0.0, "Koyoharu Got").has_sub
    assert H.SubScan(0.0, "a hero who saves everyone").has_sub


def test_shot_level_gate_has_no_tolerance(tmp_path):
    """A window may carry the odd line; a shot may not."""
    clip = _clip(tmp_path / "shotgate.mp4", credit=True, sub=True)
    furniture = H.learn_furniture(clip)
    assert H.prints_in(clip, 4.0, 6.0, furniture), "subtitled shot must be rejected"
    assert not H.prints_in(clip, 0.5, 2.5, furniture), "clean shot must pass"
