"""Pins for the STILL kit's overlay stage: captions, outro, loop-back.

Every pin here encodes a shape this module has ALREADY failed on, on a real
reel, rather than a shape it might fail on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from genlab_core.still import overlay as O
from genlab_core.talk.captions import Cue, Word


def _cue(text: str, a: float, b: float) -> Cue:
    """Cue.start/end are explicit FIELDS that build() sets -- they are not
    derived from the words. Omitting them yields enable='between(t,0,0)' and a
    caption that never appears while ffmpeg still exits 0.
    """
    ws = text.split()
    step = (b - a) / len(ws)
    return Cue(
        [Word(w, a + i * step, a + (i + 1) * step) for i, w in enumerate(ws)], start=a, end=b
    )


def _black(dst: Path, seconds: int = 18) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=540x960:d={seconds}:r=30",
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            str(dst),
        ],
        check=True,
        timeout=120,
    )
    return dst


def _burn(src: Path, vf: str, dst: Path, frames: int = 540):
    return subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-vf",
            vf,
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            str(dst),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )


# --- the bug that broke reel B -------------------------------------------


def test_apostrophe_in_a_cue_does_not_break_the_filtergraph(tmp_path):
    """An ``it's`` used to unbalance quoting and split the chain at the next
    comma, which lives inside ``enable='between(t,16.320,17.5)'``. ffmpeg
    reported ``No such filter: '16.320'`` -- a timestamp read as a filter name.
    """
    cues = [
        _cue("meaning he'll protect her until", 15.0, 16.3),
        _cue("and it's going to destroy, really", 16.32, 17.5),
    ]
    vf = O.caption_filters(cues, None, tmp_path / "text")
    r = _burn(_black(tmp_path / "src.mp4"), vf, tmp_path / "out.mp4")
    assert r.returncode == 0, r.stderr[-400:]


@pytest.mark.parametrize(
    "hostile",
    [
        "it's a trap",
        "one, two, three",
        "ratio 16:9",
        "100% done",
        "back\\slash",
        "em — dash",
        "quote ’ curly",
    ],
)
def test_every_character_class_that_needed_its_own_escape_rule(tmp_path, hostile):
    """The point of ``textfile=`` is that these stop being special ALL AT ONCE.
    Escaping them one rule at a time is how the apostrophe was missed.
    """
    vf = O.caption_filters([_cue(hostile, 1.0, 3.0)], None, tmp_path / "text")
    r = _burn(_black(tmp_path / "src.mp4", 5), vf, tmp_path / "out.mp4", 150)
    assert r.returncode == 0, f"{hostile!r} -> {r.stderr[-300:]}"


def test_caption_actually_reaches_the_pixels(tmp_path):
    """A control render with NO captions, differenced against one WITH them.

    Without the control, "ffmpeg exited 0" also passes when drawtext silently
    drew nothing -- which is the failure mode a text-escaping bug produces.
    """
    src = _black(tmp_path / "src.mp4", 5)
    cues = [_cue("the caption is on screen here", 1.0, 4.0)]
    vf = O.caption_filters(cues, None, tmp_path / "text")
    assert _burn(src, vf, tmp_path / "with.mp4", 150).returncode == 0

    def luma(video: Path) -> float:
        p = subprocess.run(
            # metadata=print writes at INFO; -v error swallows it entirely
            # and the parse then fails on an empty string.
            [
                "ffmpeg",
                "-v",
                "info",
                "-ss",
                "2",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        line = [ln for ln in p.stderr.splitlines() if "YAVG" in ln]
        assert line, p.stderr[-300:]
        return float(line[-1].rsplit("=", 1)[-1])

    assert luma(tmp_path / "with.mp4") > luma(src) + 0.5, "captions drew nothing"


# --- gates ----------------------------------------------------------------


def test_cue_gate_is_hard_on_orphans_and_overlap_proportional_on_function_words():
    assert O._FUNCTION_WORD_ENDING_MAX_RATE == 0.20
    src = Path(O.__file__).read_text()
    assert "cues under two words" in src and "cues on screen at once" in src


def test_textdir_is_required_rather_than_defaulted():
    """A default temp dir would be deleted before ffmpeg reads the files."""
    with pytest.raises(ValueError):
        O.caption_filters([_cue("a b c", 0.0, 1.0)], None, None)
    with pytest.raises(ValueError):
        O.outro_filters(O.Outro("@h", "cta", 2.0), 1.0, None, None)


def test_outro_is_placed_by_start_not_by_duration(tmp_path):
    vf = O.outro_filters(
        O.Outro("@FrameDrift", "Follow for more", 2.5), 12.0, None, tmp_path / "text"
    )
    assert "between(t,12.000,14.500)" in vf
    assert vf.count("drawtext=") == 2, "handle and CTA are two lines, not one"


# --- loop-back ------------------------------------------------------------


def test_loop_back_tail_matches_frame_zero(tmp_path):
    """Compare the TAIL against frame 0 -- not frame 0 against itself.

    The previous version of this pin extracted frame 0 from the source and
    frame 0 from the output and asserted they matched. Both are the same frame
    by construction, so it passed while the appended tail sat 1.72 luma away
    from frame 0 because of a colour-matrix round-trip. A loop-back pin that
    never looks at the tail cannot fail on a bad loop.
    """
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=540x960:d=3:r=30",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            str(src),
        ],
        check=True,
        timeout=120,
    )
    out = tmp_path / "looped.mp4"
    assert O.append_loop_back(src, out, hold_s=0.4)

    first, last = tmp_path / "a.png", tmp_path / "b.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(out),
            "-vf",
            "select=eq(n\\,0)",
            "-vsync",
            "0",
            "-frames:v",
            "1",
            str(first),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-sseof",
            "-0.1",
            "-i",
            str(out),
            "-update",
            "1",
            "-frames:v",
            "1",
            str(last),
        ],
        check=True,
        timeout=60,
    )
    d = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-i",
            str(first),
            "-i",
            str(last),
            "-filter_complex",
            "blend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    vals = [float(x.rsplit("=", 1)[1]) for x in d.stderr.splitlines() if "YAVG=" in x]
    assert vals, d.stderr[-300:]
    assert vals[-1] <= 1.0, f"tail is {vals[-1]:.2f} luma from frame 0"

    def dur(v: Path) -> float:
        q = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(v),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return float(q.stdout.strip())

    assert dur(out) > dur(src) + 0.3, "tail was not appended"


def test_loop_back_never_round_trips_through_rgb(tmp_path):
    """A .png in the tail path reintroduces the colour-matrix shift."""
    src = Path(O.__file__).read_text()
    body = src[src.index("def append_loop_back") : src.index("def composite(")]
    assert ".png" not in body and "f0.png" not in body, "tail goes back through RGB"
    assert "-colorspace" in body and "bt709" in body


def test_loop_back_cleans_up_its_intermediates(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=540x960:d=2:r=30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
        timeout=120,
    )
    out = tmp_path / "looped.mp4"
    assert O.append_loop_back(src, out)
    leftovers = sorted(
        p.name for p in tmp_path.iterdir() if p.name.startswith("looped.") and p.suffix != ".mp4"
    )
    assert leftovers == [], leftovers


def test_a_wrapped_cue_is_drawn_on_two_lines(tmp_path):
    """layout() splits wide cues into cue.lines; the burner must honour them.

    Flattening to cue.text drew a 35-character line centred with
    x=(w-text_w)/2, which is NEGATIVE once the text is wider than the frame --
    so it clipped off both edges at once. Seen on reel A.
    """
    from genlab_core.talk import captions as C

    # The actual cue reel A produced. Five words -- what segment() caps at --
    # and 35 characters, which is 1543px at the engine's own CHAR_W.
    long = "After dominating Kyoto and Nerima,"
    cue = _cue(long, 0.0, 3.0)
    cue.lines = C.layout(cue.words)
    assert len(cue.lines) == 2, "fixture no longer exercises wrapping"

    vf = O.caption_filters([cue], None, tmp_path / "text")
    files = sorted((tmp_path / "text").glob("cue_000_*.txt"))
    assert len(files) == 2, "wrapped cue was not drawn as two drawtexts"
    assert vf.count("drawtext=") == 2
    for f in files:
        body = f.read_text()
        assert "\n" not in body, "a newline inside a textfile renders as a tofu glyph on this build"
        assert C.px(body) <= C.MAX_LINE_PX, f"{C.px(body):.0f}px over budget"
    # both lines share one enable window, so they appear and leave together
    assert vf.count(f"between(t,{cue.start:.3f},{cue.end:.3f})") == 2


def test_burner_font_matches_the_size_layout_budgeted_against():
    """MAX_LINE_PX is derived from FONT_SZ. Drawing at another size silently
    invalidates every width decision layout() made."""
    from genlab_core.talk import captions as C

    assert O._FONT_SIZE == C.FONT_SZ
