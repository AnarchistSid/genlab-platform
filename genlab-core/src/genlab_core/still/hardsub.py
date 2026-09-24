"""ANIME-PEAK-07 §3 — burned-in subtitles disqualify a window.

Official anime uploads are regional: the same fight on a LatAm or SEA
official channel carries burned-in Spanish or English subtitles. The
channel is licensed, so the rights gate passes — and the frames are still
unusable, because a reel that reads "Ritual Maldito Inverso: Rojo." over
the hero shot is somebody else's subtitle track.

The gate is OCR over the lower fifth of the frame, which is where every
broadcaster puts them. A window that prints text on more than a small
share of its frames is `source_hardsub` and is not made.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

BAND_TOP = 0.78          # OCR the bottom 22% of the frame
BAND_SIDE = 0.18         # ...and only its centre: see _ocr_band
MAX_SUBBED_FRAC = 0.15   # above this share of frames carrying text, reject
MIN_CHARS = 8            # shorter strings are OCR noise on textured art
MIN_WORDS = 3            # a two-word fragment in the band is OCR noise, not a line
MIN_CONF = 55            # tesseract per-word confidence
OCR_LANG = "eng+spa"
OCR_SCALE = 1080         # gate resolution, per reference.py GATE_SCALE

# Studio bugs and platform furniture live in the same band on some uploads;
# they are not subtitles and must not trip the gate.
_FURNITURE = re.compile(
    r"toei|toho|crunchyroll|animation|shueisha|kodansha|aniplex|"
    r"©|\(c\)|project|subscribe|watch on|ver m[aá]s|more videos",
    re.I,
)
_WORD = re.compile(r"[A-Za-zÀ-ÿ]{3,}")


@dataclass(frozen=True)
class SubScan:
    t: float
    text: str

    @property
    def has_sub(self) -> bool:
        return self.has_sub_beyond(frozenset())

    def has_sub_beyond(self, static: frozenset[str] | set[str]) -> bool:
        clean = _FURNITURE.sub(" ", self.text)
        words = [w for w in _WORD.findall(clean) if w.lower() not in static]
        return len(" ".join(words)) >= MIN_CHARS and len(words) >= MIN_WORDS


def _ocr_band(path: Path, t: float) -> str:
    """OCR the CENTRE of the lower band.

    Broadcasters right-align the legal notice ("(c) Koyoharu Gotoge /
    SHUEISHA, Aniplex, ufotable") into the corner and centre the subtitle.
    Measured on a clean Demon Slayer window, OCR over the full band read
    text on 92% of frames -- all of it the copyright line plus noise off
    the artwork, which would have condemned a source that carries no
    subtitles at all. Cropping to the centre 64% removes the confound at
    its source instead of trying to out-regex the OCR.
    """
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "band.png"
        vf = (f"scale={OCR_SCALE}:-2,"
              f"crop=iw*{1 - 2 * BAND_SIDE}:ih*{1 - BAND_TOP}:"
              f"iw*{BAND_SIDE}:ih*{BAND_TOP},"
              f"format=gray,eq=contrast=1.6")
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path),
                        "-frames:v", "1", "-vf", vf, "-y", str(png)], check=True)
        out = subprocess.run(["tesseract", str(png), "stdout", "-l", OCR_LANG,
                              "--psm", "6", "tsv"], capture_output=True, text=True)
        words = []
        for line in out.stdout.splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) < 12:
                continue
            try:
                conf = float(cols[10])
            except ValueError:
                continue
            if conf >= MIN_CONF and cols[11].strip():
                words.append(cols[11].strip())
        return " ".join(words)


def scan_window(path: Path, start: float, end: float,
                stride_s: float = 1.0) -> list[SubScan]:
    out, t = [], start
    while t < end:
        out.append(SubScan(t, _ocr_band(path, t)))
        t += stride_s
    return out


STATIC_FRAC = 0.80       # a token in this share of frames is furniture


def _static_tokens(scans: list[SubScan]) -> set[str]:
    """Tokens that appear in nearly every frame of the clip.

    The legal notice is burned into every frame; a subtitle is not. That
    difference identifies furniture without a hardcoded list of studio
    names -- which would be config living in code, and would need editing
    the first time a new studio appears.
    """
    if not scans:
        return set()
    seen: dict[str, int] = {}
    for s in scans:
        for w in {w.lower() for w in _WORD.findall(s.text)}:
            seen[w] = seen.get(w, 0) + 1
    need = STATIC_FRAC * len(scans)
    return {w for w, n in seen.items() if n >= need}


def subbed_fraction(scans: list[SubScan]) -> float:
    if not scans:
        return 0.0
    static = _static_tokens(scans)
    return sum(1 for s in scans if s.has_sub_beyond(static)) / len(scans)


def learn_furniture(path: Path, samples: int = 10) -> set[str]:
    """Furniture is a property of the CLIP, not of the window.

    Learning it from the window alone is a scope error: a line that
    persists through the window appears in every frame OF THE WINDOW and
    erases itself. The legal notice is on every frame of the whole clip;
    a subtitle, sampled clip-wide, is not.
    """
    from genlab_core.still.reference import duration_s

    dur = duration_s(path)
    scans = [SubScan(dur * (k + 0.5) / samples,
                     _ocr_band(path, dur * (k + 0.5) / samples))
             for k in range(samples)]
    return _static_tokens(scans)


def check(path: Path, start: float, end: float,
          stride_s: float = 1.0) -> tuple[bool, float, list[str]]:
    """(passes, subbed_fraction, the lines that were read)."""
    scans = scan_window(path, start, end, stride_s=stride_s)
    static = learn_furniture(path)
    frac = (sum(1 for s in scans if s.has_sub_beyond(static)) / len(scans)
            if scans else 0.0)
    lines = [s.text for s in scans if s.has_sub_beyond(static)]
    ok = frac <= MAX_SUBBED_FRAC
    logger.info("[hardsub] %s %.1f-%.1fs subbed=%.0f%% -> %s",
                path.name, start, end, frac * 100, "ok" if ok else "source_hardsub")
    return ok, frac, lines
