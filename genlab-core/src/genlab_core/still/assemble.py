"""Shots -> a reel: transitions, captions, bed, outro, loop-back.

Order is fixed and load-bearing:

  1. concat the shots with a transition at each cut
  2. mix the audio (narration + ducked bed)
  3. burn the captions and the outro ONCE, last, over the finished picture

Overlays last, once, because compositing them per-shot re-encodes text
through every subsequent pass and softens it — and because a caption cue
that spans a cut has to be drawn over the joined timeline, not over one of
the two shots it straddles.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genlab_core.still.render import FPS

logger = logging.getLogger(__name__)

#: The kit names ink_bleed / film_burn. ffmpeg's xfade has no such
#: transitions, so these are the nearest built-ins and the mapping is written
#: down rather than hidden: `ink_bleed` -> a dissolve (pigment spreading),
#: `film_burn` -> fadewhite (stock flaring before it tears). A real ink bleed
#: needs a luma-wipe with an alpha ramp asset, which is its own piece of work.
#: Calling these the real thing in a kit comment and shipping a dissolve is
#: the kind of gap that reads as craft failure later.
_XFADE = {"ink_bleed": "dissolve", "film_burn": "fadewhite"}


class LoopBackUnmeasurable(RuntimeError):
    """The loop-back difference could not be measured.

    Distinct from "the loop is bad". A gate that cannot run must say so
    rather than return a value that reads like a verdict.
    """


class UnknownTransition(ValueError):
    """The kit named a transition with no mapping."""


def xfade_for(name: str) -> str:
    try:
        return _XFADE[name]
    except KeyError:
        raise UnknownTransition(
            f"no xfade mapping for transition {name!r}; known: {sorted(_XFADE)}"
        ) from None


@dataclass(frozen=True)
class AudioPlan:
    narration: Path
    bed: Path | None
    duck_db: float
    target_lufs: float
    true_peak: float


def build_concat_filter(
    n_shots: int, durations: list[float], transition: str, trans_s: float
) -> tuple[str, str]:
    """(filter_complex, final_label) joining shots with a transition per cut.

    xfade OVERLAPS by ``trans_s``, so each join shortens the timeline. The
    offsets below account for that cumulatively; computing them from raw
    starts would drift by trans_s per cut and desync the audio by the end.
    """
    xf = xfade_for(transition)
    if n_shots == 1:
        return "", "0:v"
    parts: list[str] = []
    prev = "0:v"
    offset = durations[0] - trans_s
    for i in range(1, n_shots):
        label = f"x{i}"
        parts.append(
            f"[{prev}][{i}:v]xfade=transition={xf}:duration={trans_s:.3f}"
            f":offset={offset:.3f}[{label}]"
        )
        prev = label
        offset += durations[i] - trans_s
    return ";".join(parts), prev


def concat_shots(
    shots: list[Path],
    durations: list[float],
    out: Path,
    *,
    kit: dict[str, Any],
    timeout_s: int = 600,
) -> bool:
    """Join shots with one transition per cut."""
    trans = kit["transitions"]["set"][0]
    trans_s = float(kit["transitions"]["duration_s"])
    fc, final = build_concat_filter(len(shots), durations, trans, trans_s)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for s in shots:
        cmd += ["-i", str(s)]
    if fc:
        cmd += ["-filter_complex", fc, "-map", f"[{final}]"]
    else:
        cmd += ["-map", "0:v"]
    cmd += [
        "-r",
        str(FPS),
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
        str(out),
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if p.returncode != 0:
        logger.warning("[still] concat failed: %s", p.stderr[-400:])
        return False
    return True


def mix_audio(plan: AudioPlan, out: Path, video_s: float, *, timeout_s: int = 300) -> bool:
    """Narration over a ducked bed, loudness-normalised.

    The bed is ducked by a fixed gain rather than sidechained: a sidechain
    compressor pumps on every consonant at this length, and the kit asks for
    a 6-10 dB bed, not a reactive one.

    The result is padded to ``video_s``. It used to end with the narration,
    and ``composite`` runs with ``-shortest`` -- so the end card, which plays
    after the last word, was silently cut off the tail of every reel. The
    card was in the timeline, in the concat and in the manifest, and not in
    the file.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(plan.narration)]
    if plan.bed:
        cmd += ["-stream_loop", "-1", "-i", str(plan.bed)]
        fc = (
            f"[1:a]volume={plan.duck_db}dB,atrim=0:{video_s:.3f},asetpts=PTS-STARTPTS[bed];"
            f"[0:a][bed]amix=inputs=2:duration=longest:dropout_transition=0,"
            f"loudnorm=I={plan.target_lufs}:TP={plan.true_peak}:LRA=11,"
            f"apad,atrim=0:{video_s:.3f}[a]"
        )
        cmd += ["-filter_complex", fc, "-map", "[a]"]
    else:
        cmd += [
            "-af",
            f"loudnorm=I={plan.target_lufs}:TP={plan.true_peak}:LRA=11,apad,atrim=0:{video_s:.3f}",
        ]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if p.returncode != 0:
        logger.warning("[still] audio mix failed: %s", p.stderr[-400:])
        return False

    # SECOND PASS. Single-pass loudnorm is a live estimator and lands within
    # ~2 LU, which is outside a +/-1 gate: measured -15.7 against a -14
    # target on the first real mix. The second pass feeds it the measured
    # values so it normalises to the target instead of toward it.
    from genlab_core.still.audio import measure_loudness

    measured_i, measured_tp = measure_loudness(out)
    if abs(measured_i - plan.target_lufs) <= 0.5:
        return True
    tmp = out.with_suffix(".pass2" + out.suffix)
    p2 = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(out),
            "-af",
            f"loudnorm=I={plan.target_lufs}:TP={plan.true_peak}:LRA=11:"
            f"measured_I={measured_i}:measured_TP={measured_tp}:"
            f"measured_LRA=11:measured_thresh={measured_i - 10:.2f}:linear=true,"
            f"apad,atrim=0:{video_s:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(tmp),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if p2.returncode == 0 and tmp.exists():
        tmp.replace(out)
    else:
        logger.warning("[still] loudnorm pass 2 failed, keeping pass 1: %s", p2.stderr[-200:])
    return True


def loop_back_ok(video: Path, tolerance: float = 1.0) -> tuple[bool, float]:
    """Mean absolute difference between the last frame and the first.

    The kit asks the outro to land on a frame matching frame 0 so the loop is
    invisible. Measured, not asserted.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        first, last = Path(td) / "a.png", Path(td) / "b.png"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(video),
                "-vf",
                "select=eq(n\\,0)",
                "-vsync",
                "0",
                "-frames:v",
                "1",
                str(first),
            ],
            check=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-sseof",
                "-0.2",
                "-i",
                str(video),
                "-update",
                "1",
                "-frames:v",
                "1",
                str(last),
            ],
            check=True,
        )
        # `-v info`, NOT `-v error`: metadata=print writes at INFO level, so
        # `-v error` suppresses the only output this reads. With it silenced
        # the parse found nothing and returned the 255.0 fallback — a number
        # that reads as "completely different frames" rather than as "the
        # measurement did not happen". Same shape as the unsatisfiable guard:
        # the not-measured value must not look like a valid result.
        out = subprocess.run(
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
        )
        vals = [float(x.split("=")[1]) for x in out.stderr.splitlines() if "YAVG=" in x]
        if not vals:
            raise LoopBackUnmeasurable(
                "no YAVG samples from the difference blend — the loop-back "
                "gate did not run. Do not treat this as a failed loop."
            )
        score = sum(vals) / len(vals)
    return score <= tolerance, score
