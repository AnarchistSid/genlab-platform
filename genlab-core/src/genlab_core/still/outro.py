"""ANIME-PEAK-08 §1 — the outro card, trimmed before anything is cut.

Every licensed clip in the pilot ends in a platform outro: Crunchyroll's
"WATCH ON CR / SUBSCRIBE" panel, or its Spanish "VER MÁS" variant. Measured
on the five pilot clips it runs 8 s on four of them and 20 s on the JJK
upload. Nothing excluded it, so a window drifting late would have rendered
the subscribe card into a reel.

Detection is the card's defining property rather than its artwork: it is a
near-static run at the TAIL. A logo template would need one template per
platform per locale and would miss the next one; frame-to-frame similarity
needs nothing and generalises. The trailing run is what matters -- anime
holds still mid-scene often enough that a static run elsewhere means
nothing.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SAMPLE_FPS = 2.0         # frames per second sampled in the tail
# Measured on the five pilot clips: moving content holds 0.03-0.07 of its
# pixels still between samples; the outro cards hold 0.84-1.00 even though
# they animate their thumbnail panels. 0.5 sits in an empty gap an order of
# magnitude wide. Note this number belongs to the METRIC, not to the task --
# it was 0.95 while _similarity returned mean absolute difference, and
# carrying that value onto the changed-pixel measure lost three of the five
# cards.
SIMILAR = 0.50           # share of still pixels (vs the FINAL frame) = "card"
DIP_TOLERANCE = 2        # consecutive sub-threshold samples a card may contain
MIN_CARD_S = 3.0         # a shorter static tail is a held shot, not a card
MAX_CARD_S = 30.0        # how far back from the end to look
SAMPLE_W = 160           # similarity is a gross measure; work small


@dataclass(frozen=True)
class Outro:
    start_s: float | None
    duration_s: float
    clip_s: float

    @property
    def found(self) -> bool:
        return self.start_s is not None

    @property
    def usable_end_s(self) -> float:
        """Where the clip's real content ends."""
        return self.start_s if self.start_s is not None else self.clip_s


def _tail_frames(path: Path, clip_s: float) -> tuple[list, float]:
    """Greyscale tail frames, plus the timestamp of the first one."""
    import numpy as np
    from PIL import Image

    start = max(0.0, clip_s - MAX_CARD_S)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{start}", "-i", str(path),
             "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_W}:-2,format=gray",
             "-y", str(d / "%04d.png")], check=True)
        frames = [np.asarray(Image.open(p), dtype=float)
                  for p in sorted(d.glob("*.png"))]
    return frames, start


CHANGE_LEVEL = 8.0       # per-pixel difference counting as "this pixel moved"


def _similarity(a, b) -> float:
    """Share of pixels that did NOT move.

    Not mean absolute difference: that averages a small moving element into a
    large still background and reports a held shot with a talking mouth as a
    static card. Counting changed PIXELS keeps a card (nothing moves) apart
    from a held shot (something does).
    """
    import numpy as np

    return float((np.abs(a - b) <= CHANGE_LEVEL).mean())


def detect(path: Path) -> Outro:
    from genlab_core.still.reference import duration_s

    clip_s = duration_s(path)
    frames, start = _tail_frames(path, clip_s)
    if len(frames) < 3:
        return Outro(None, 0.0, clip_s)

    # Compare every tail frame to the FINAL one, not to its predecessor.
    # The JJK card contains a one-frame wipe between thumbnail panels: against
    # the predecessor that read 0.68 and ended the run 10 s early, hiding half
    # the card. Against the final frame the whole card holds >= 0.98 while
    # content never exceeds 0.81 -- the two classes separate with a margin
    # wide enough that the threshold is not a tuned number.
    last = frames[-1]
    sims = [_similarity(last, f) for f in frames]

    n, dips = 0, 0
    for s in reversed(sims):
        if s < SIMILAR:
            dips += 1
            if dips > DIP_TOLERANCE:
                break
        else:
            dips = 0
        n += 1
    n -= dips  # do not count the trailing dips that ended the walk
    card_s = n / SAMPLE_FPS
    if card_s < MIN_CARD_S:
        logger.info("[outro] %s: no card (longest static tail %.1fs)",
                    path.name, card_s)
        return Outro(None, 0.0, clip_s)

    card_start = start + (len(frames) - n) / SAMPLE_FPS
    logger.info("[outro] %s: card at %.1fs, %.1fs long, content ends there",
                path.name, card_start, clip_s - card_start)
    return Outro(card_start, clip_s - card_start, clip_s)


def trimmed_end(path: Path) -> float:
    """The last second of real content. Use this as the window search bound."""
    return detect(path).usable_end_s
