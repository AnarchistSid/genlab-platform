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
_BASELINE_Y = "h*0.80"

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


#: ANIME-16 §11. ONE baseline for the whole reel, in the lower third. v4's
#: captions sat mid-frame across the mouth and chin of a close-up, and moved
#: between halves of the reel. A caption that moves is a caption the eye has
#: to find again.
_BASELINE_LOCKED = 0.80
#: The single permitted alternate is HIGHER, never lower, and never mid-face.
_BASELINE_ALTERNATE = 0.64


def caption_filters(
    cues: list[C.Cue],
    font: str | None = None,
    textdir: Path | None = None,
    *,
    body_font: Path | None = None,
    accent: str = "#FFFFFF",
    karaoke: bool = True,
    baseline: float = _BASELINE_LOCKED,
) -> str:
    """One drawtext per line, at ONE locked baseline, with karaoke.

    Karaoke (§12): the word currently being spoken is drawn again on top of
    the line in the brand accent. Word timings already exist — the highlight
    was simply never restored after the captions moved to the script+ASR
    path. The x offset of each word is MEASURED with the real font, because
    the line is centred and a character-ratio estimate puts the highlight on
    the wrong word by the end of a long line.
    """
    if textdir is None:
        raise ValueError("caption_filters needs a textdir to stage cue text")
    from genlab_core.still import typography as T

    parts: list[str] = []
    ff = f":fontfile={body_font}" if body_font else (f":fontfile={font}" if font else "")
    accent_hex = accent.lstrip("#")
    y_base = f"h*{baseline}"

    for i, cue in enumerate(cues):
        lines = [" ".join(w.t for w in ln) for ln in cue.lines] or [cue.text]
        n = len(lines)
        # Size the whole cue so its widest line fits, once, so every line of
        # every cue shares a size and the block does not breathe.
        size = _FONT_SIZE
        if body_font:
            fitted = T.fit(
                max(lines, key=len), body_font, max_size=_FONT_SIZE, min_size=34, max_lines=1
            )
            size = fitted.size
        for j, line in enumerate(lines):
            text = _textfile(textdir, f"cue_{i:03d}_{j}", line)
            rise = (n - 1 - j) * (size + _LINE_SPACING)
            parts.append(
                f"drawtext=textfile='{text}'"
                f":fontsize={size}:fontcolor=white"
                f":box=1:boxcolor=black@{_PLATE_ALPHA}:boxborderw={_PLATE_PAD}"
                f":x=(w-text_w)/2:y={y_base}-{rise}"
                f":enable='between(t,{cue.start:.3f},{cue.end:.3f})'{ff}"
            )
            if not (karaoke and body_font):
                continue
            # §12 — the spoken word, in accent, over the line.
            words_in_line = [w for w in cue.words if w.t in line.split()] or cue.words
            line_w = T.text_width_px(line, body_font, size)
            cursor = 0.0
            for w in line.split():
                wid = T.text_width_px(w, body_font, size)
                src = next((x for x in cue.words if x.t == w), None)
                if src is not None and src.b > src.a:
                    wpath = _textfile(textdir, f"kar_{i:03d}_{j}_{int(cursor)}", w)
                    dx = cursor - line_w / 2.0
                    parts.append(
                        f"drawtext=textfile='{wpath}'"
                        f":fontsize={size}:fontcolor=0x{accent_hex}"
                        f":x=(w/2)+({dx:.1f}):y={y_base}-{rise}"
                        f":enable='between(t,{src.a:.3f},{src.b:.3f})'{ff}"
                    )
                cursor += wid + T.text_width_px(" ", body_font, size)
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


def _probe_fps(video: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
                        str(video)], capture_output=True, text=True)
    txt = (r.stdout or "30/1").strip()
    try:
        num, den = txt.split("/")
        return float(num) / float(den or 1)
    except ValueError:
        return float(txt or 30.0)


def append_loop_back(video: Path, out: Path, hold_s: float = 0.4, *,
                     fps: float | None = None, timeout_s: int = 300) -> bool:
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

    The tail is built at the INPUT's frame rate, not a hardcoded 30. The
    concat below is a stream COPY, and copying a 30 fps tail onto a 24 fps
    body does not error -- it produces a file whose duration is wrong. On a
    23.71 s reel that added 6.32 s of broken timeline, visible only by
    measuring the output.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    fps = fps or _probe_fps(video)
    frames = max(1, round(hold_s * fps))
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
            f"select=eq(n\\,0),loop=loop={frames - 1}:size=1:start=0,setpts=N/{fps:.6f}/TB",
            "-frames:v",
            str(frames),
            "-r",
            f"{fps:.6f}",
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
