"""Captions, hook card and outro — composited ONCE, last.

Once and last because per-shot compositing re-encodes text through every
later pass and softens it, and because a caption cue spanning a cut has to
be drawn over the joined timeline rather than over one of the two shots it
straddles.

Cue construction is delegated to ``talk.captions`` — the CONTENT-03 rules
(phrase splitting, one cue on screen, names from metadata, sentence case)
already live there with their own gates. Re-deriving them here would be a
second implementation of a spec that is hard to get right once.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genlab_core.still.audio import Word as AlignedWord
from genlab_core.talk import captions as C

logger = logging.getLogger(__name__)

#: Caption plate: a dark band behind the text so it survives a bright still.
#: Drawn as drawtext's own box rather than a separate overlay, so it moves
#: with the text and cannot desync from it.
_PLATE_ALPHA = 0.55
#: The SAME size talk/captions.py budgets line width against (FONT_SZ), not a
#: second guess. layout() splits a cue into lines using MAX_LINE_PX computed
#: from FONT_SZ; drawing at any other size makes that budget wrong, and the
#: kit drew at 56 while the engine measured at 76.
_FONT_SIZE = C.FONT_SZ
_PLATE_PAD = 26
_LINE_SPACING = 12
#: Bottom safe zone: above the platform UI, below the subject's face.
_BASELINE_Y = "h*0.72"

#: Reference: talk/captions.py's own test accepts 1 function-word ending across
#: 47 cues (~2%) on a real transcript. 20% is a rate that means the
#: segmentation is wrong, not that one line landed awkwardly.
_FUNCTION_WORD_ENDING_MAX_RATE = 0.20


class CaptionGateFailed(RuntimeError):
    """Cues violate a CONTENT-03 rule and must not be burned."""


def cues_from_alignment(
    words: list[AlignedWord], names: list[str] | None = None, captions_end: float | None = None
) -> list[C.Cue]:
    """faster-whisper words -> CONTENT-03 cues, gated."""
    conv = [C.Word(t=w.text, a=w.start_s, b=w.end_s) for w in words]
    cues = C.build(conv, names or [], captions_end)

    problems: list[str] = []

    # HARD: talk/captions.py's own tests assert these absolutely
    # (`orphans(cues) == []`, `max_visible(cues) <= 1`).
    if orph := C.orphans(cues):
        problems.append(f"cues under two words: {orph[:3]}")
    visible = C.max_visible(cues)
    if visible > 1:
        problems.append(f"{visible} cues on screen at once")

    # PROPORTIONAL: function-word endings are penalised in segment()
    # (`score -= 100  # never end here`) but not forbidden, and the engine's
    # own reference test accepts 1 across 47 cues on a real 192-word
    # transcript — about 2%. Treating a single one as fatal is stricter than
    # the engine's accepted behaviour, and it rejected a perfectly readable
    # narration on the first run. Fail on a RATE that says the segmentation
    # is wrong, not on the existence of one.
    if cues:
        bad = C.function_word_endings(cues)
        rate = len(bad) / len(cues)
        if rate > _FUNCTION_WORD_ENDING_MAX_RATE:
            problems.append(
                f"{len(bad)}/{len(cues)} lines end on a function word "
                f"({rate:.0%} > {_FUNCTION_WORD_ENDING_MAX_RATE:.0%}): {bad[:3]}"
            )
        elif bad:
            logger.info(
                "[still] %d/%d cues end on a function word (%.0f%%, within the "
                "engine's own ~2%% reference): %s",
                len(bad),
                len(cues),
                rate * 100,
                bad[:2],
            )
    if problems:
        raise CaptionGateFailed("; ".join(problems))
    return cues


def _esc_path(path: Path) -> str:
    """Escape a path for use as an ffmpeg filter OPTION VALUE."""
    return str(path).replace("\\", "\\\\").replace(":", r"\:")


def _textfile(textdir: Path, tag: str, text: str) -> str:
    """Write caption text to a file and return an escaped ``textfile=`` path.

    drawtext's ``text=`` takes the string through the filtergraph parser, and
    there is no escape for an apostrophe INSIDE a single-quoted value -- the
    quote simply ends there. One ``it's`` in a narration therefore unbalances
    the quoting for everything after it, and the next comma (ours is inside
    ``enable='between(t,16.320,17.5)'``) is read as a filter separator:
    ``No such filter: '16.320'``. That is a real failure this kit hit on its
    second reel. ``textfile=`` moves the text out of the parser's reach
    entirely, so apostrophes, commas, colons, em dashes and percent signs all
    stop being special at once, instead of one escape rule at a time.
    """
    textdir.mkdir(parents=True, exist_ok=True)
    f = textdir / f"{tag}.txt"
    f.write_text(text, encoding="utf-8")
    return _esc_path(f)


def caption_filters(cues: list[C.Cue], font: str | None = None, textdir: Path | None = None) -> str:
    """One drawtext per cue, each enabled only for its own window."""
    if textdir is None:
        raise ValueError("caption_filters needs a textdir to stage cue text")
    parts = []
    for i, cue in enumerate(cues):
        # cue.lines is what layout() produced -- one line if it fits, two
        # balanced by WIDTH if it does not. Flattening to cue.text throws that
        # away and draws a 35-character line at x=(w-text_w)/2, which goes
        # negative and clips off BOTH edges of the frame. Observed on reel A:
        # "After dominating Kyoto and Nerima," ran past 1080px.
        #
        # ONE drawtext PER LINE, not one drawtext holding a newline. This
        # ffmpeg build breaks the line on 0x0A *and* also renders a glyph for
        # it -- a tofu box sat at the end of line 1 of every wrapped caption.
        # Per-line filters also give each line its own plate, which hugs the
        # text instead of boxing the ragged pair.
        lines = [" ".join(w.t for w in ln) for ln in cue.lines] or [cue.text]
        n = len(lines)
        for j, line in enumerate(lines):
            text = _textfile(textdir, f"cue_{i:03d}_{j}", line)
            rise = (n - 1 - j) * (_FONT_SIZE + _LINE_SPACING)
            f = (
                f"drawtext=textfile='{text}'"
                f":fontsize={_FONT_SIZE}:fontcolor=white"
                f":box=1:boxcolor=black@{_PLATE_ALPHA}:boxborderw={_PLATE_PAD}"
                f":x=(w-text_w)/2:y={_BASELINE_Y}-{rise}"
                f":enable='between(t,{cue.start:.3f},{cue.end:.3f})'"
            )
            if font:
                f += f":fontfile={font}"
            parts.append(f)
    return ",".join(parts)


@dataclass(frozen=True)
class Outro:
    handle: str
    cta: str
    duration_s: float


def outro_filters(
    outro: Outro, start_s: float, font: str | None = None, textdir: Path | None = None
) -> str:
    if textdir is None:
        raise ValueError("outro_filters needs a textdir to stage slate text")
    handle = _textfile(textdir, "outro_handle", outro.handle)
    cta = _textfile(textdir, "outro_cta", outro.cta)
    end = start_s + outro.duration_s
    common = (
        f":fontcolor=white:box=1:boxcolor=black@0.62:boxborderw=22"
        f":x=(w-text_w)/2:enable='between(t,{start_s:.3f},{end:.3f})'"
    )
    ff = f":fontfile={font}" if font else ""
    return ",".join(
        [
            f"drawtext=textfile='{handle}':fontsize=72:y=h*0.44{common}{ff}",
            f"drawtext=textfile='{cta}':fontsize=44:y=h*0.54{common}{ff}",
        ]
    )


def append_loop_back(video: Path, out: Path, hold_s: float = 0.4, *, timeout_s: int = 300) -> bool:
    """Append frame 0 to the tail so the loop is invisible.

    The kit asks the reel to end on a frame matching frame 0. Appending the
    ACTUAL first frame is the only way that is true by construction -- an
    outro card that merely resembles it still cuts on playback wrap.

    The tail is built ENTIRELY IN YUV. An earlier version extracted frame 0 to
    a PNG and re-encoded it, which round-trips YUV->RGB->YUV; the PNG carries
    no colour tag, so the way back used a different matrix than the bt709 way
    out and every tail pixel came back with a small luma offset. Measured on
    reel B, that put the loop-back score at 1.72 against a 1.0 tolerance -- and
    it stayed at 1.65 even with a LOSSLESS re-encode, which is what proves the
    loss was the colour round-trip and not the encoder. Staying in YUV scores
    0.51 on the same frame.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, round(hold_s * 30))
    tail = out.with_suffix(".tail.mp4")
    p = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,0),loop=loop={frames - 1}:size=1:start=0,setpts=N/30/TB",
            "-frames:v",
            str(frames),
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            str(tail),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if p.returncode != 0:
        logger.warning("[still] loop-back tail failed: %s", p.stderr[-300:])
        tail.unlink(missing_ok=True)
        return False
    lst = out.with_suffix(".concat.txt")
    lst.write_text(f"file '{video.resolve()}'\nfile '{tail.resolve()}'\n")
    p = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    for f in (tail, lst):
        f.unlink(missing_ok=True)
    if p.returncode != 0:
        logger.warning("[still] loop-back concat failed: %s", p.stderr[-300:])
        return False
    return True


def composite(
    video: Path,
    audio: Path,
    out: Path,
    *,
    cues: list[C.Cue],
    outro: Outro | None,
    video_s: float,
    kit: dict[str, Any],
    font: str | None = None,
    timeout_s: int = 900,
) -> bool:
    """Burn captions + outro and mux the audio. ONE pass, at the end."""
    textdir = out.parent / f".{out.stem}_text"
    chain = caption_filters(cues, font, textdir)
    otro = ""
    if outro is not None:
        otro = outro_filters(outro, max(0.0, video_s - outro.duration_s), font, textdir)
    vf = ",".join(x for x in (chain, otro) if x)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(audio)]
    if vf:
        cmd += ["-vf", vf]
    cmd += [
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-shortest",
        "-c:v",
        "libx264",
        "-crf",
        "20",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        str(out),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if p.returncode != 0:
        logger.warning("[still] composite failed: %s", p.stderr[-400:])
        return False
    return True
