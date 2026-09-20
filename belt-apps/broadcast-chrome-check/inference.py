"""Find the static chrome burned into a video, and give back the clean rectangle.

Footage repurposed into vertical short-form almost never arrives clean. Broadcast
sport carries a score/clock bug; screen-captured embeds carry tweet text, player
UI and borders. Crop through it and the overlay gets graded, zoomed and tracked
along with the picture, which looks exactly as wrong as it sounds.

The obvious detector is motion energy -- chrome does not move, picture does --
and it is wrong in a way that fails silently. Measured on UFC broadcast footage:
the scoreboard band read 50% of peak frame-to-frame difference and a motion
detector reported "no chrome found", while the frame plainly carried
``HUNT | DWCS 4:35 R1 | PEREA``. THE CLOCK TICKS. 4:35, 4:34, 4:32 across three
seconds, so the band is full of motion.

What actually separates chrome from picture is that most of its pixels NEVER
CHANGE: the box, the rules and the lettering are identical every frame and only
the digits move.

    UFC knockdown, rows 943-993   frozen-pixel fraction 0.13-0.19
    same clip, whole frame        frozen-pixel fraction 0.007   (26x baseline)

The threshold is relative to the clip's own baseline rather than absolute,
because a scoreboard is a centred box roughly a third of frame width -- even a
perfectly static one cannot push a whole row past ~0.4. The same measure handles
screen-capture chrome, where the frozen fraction runs near 1.0.
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

import numpy as np
from inferencesh import BaseAppOutput, BaseApp, BaseAppSetup, File, OutputMeta, TextMeta
from PIL import Image
from pydantic import BaseModel, Field

logger = logging.getLogger("broadcast-chrome-check")
logging.basicConfig(level=logging.INFO)

FROZEN_STD = 2.0        # 8-bit luma spread below which a pixel counts as frozen
FROZEN_RATIO = 8.0      # ...and how far over the clip's own baseline it must sit
FROZEN_FLOOR = 0.04     # ...with an absolute floor, for near-static shots
MIN_BAND_ROWS = 6


class AppSetup(BaseAppSetup):
    pass


class RunInput(BaseModel):
    video: File = Field(description="Video to inspect.")
    start_seconds: float = Field(
        default=0.0, description="Where to start sampling, in seconds.")
    duration_seconds: float = Field(
        default=6.0, description="How much of the video to sample. A few seconds "
                                 "is plenty; chrome is chrome.")
    samples: int = Field(
        default=24, description="Frames to sample across that window. More is "
                                "steadier but slower.")
    edge_fraction: float = Field(
        default=0.25, description="Fraction of the frame at each edge to search. "
                                  "0.25 covers a scoreboard and a tweet header.")


class ChromeBand(BaseModel):
    edge: str = Field(description="Which edge the band sits on: top/bottom/left/right.")
    start_px: int = Field(description="First row or column of the band.")
    end_px: int = Field(description="Last row or column of the band.")
    frozen_fraction: float = Field(
        description="Share of pixels in the band that never change. Compare with "
                    "baseline_frozen_fraction -- the RATIO is the evidence.")
    thickness_px: int = Field(description="How deep the band is.")


class RunOutput(BaseAppOutput):
    chrome_found: bool = Field(description="True when any edge carries static chrome.")
    bands: List[ChromeBand] = Field(description="Every band found, one per edge.")
    clean_rect: List[int] = Field(
        description="[x, y, width, height] of the picture with chrome removed. "
                    "Feed this straight to a crop.")
    source_size: List[int] = Field(description="[width, height] of the source.")
    baseline_frozen_fraction: float = Field(
        description="The clip's own frozen-pixel baseline. Live picture sits near "
                    "zero; a still shot sits high, which is why the threshold is "
                    "derived per clip and not fixed.")
    threshold_used: float = Field(description="Frozen fraction a line had to clear.")
    full_bleed_floor: float = Field(
        description="Smallest magnification that still fills a 1080x1920 vertical "
                    "frame from the CLEAN rect. Cropping chrome away raises this: "
                    "removing a 137px band from 1080p takes it from 1.778x to "
                    "2.036x, and a crop below it would letterbox.")
    notes: str = Field(description="What was measured, in one paragraph.")


def _probe_size(path: str) -> tuple:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def _sample(path: str, start: float, dur: float, n: int, out_dir: Path) -> List[np.ndarray]:
    fps = max(n / max(dur, 0.1), 0.1)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(dur), "-i", path,
         "-vf", f"fps={fps},format=gray", "-frames:v", str(n),
         str(out_dir / "f_%04d.png")], check=True)
    files = sorted(out_dir.glob("f_*.png"))
    return [np.asarray(Image.open(f), np.float32) for f in files]


def _frozen(frames: List[np.ndarray]) -> np.ndarray:
    return (np.stack(frames).std(0) < FROZEN_STD)


def _band(profile: np.ndarray, thr: float, length: int, frac: float, from_end: bool):
    """Walk in from one edge while lines keep looking like chrome."""
    span = int(length * frac)
    idx = range(length - 1, length - span - 1, -1) if from_end else range(span)
    hits = [i for i in idx if profile[i] >= thr]
    if len(hits) < MIN_BAND_ROWS:
        return None
    return (min(hits), max(hits))


class App(BaseApp):
    async def setup(self, setup: AppSetup):
        logger.info("broadcast-chrome-check ready")

    async def run(self, input_data: RunInput) -> RunOutput:
        src = input_data.video.path
        w, h = _probe_size(src)
        logger.info("source %dx%d, sampling %d frames from %.2fs for %.2fs",
                         w, h, input_data.samples, input_data.start_seconds,
                         input_data.duration_seconds)

        with tempfile.TemporaryDirectory() as td:
            frames = _sample(src, input_data.start_seconds,
                             input_data.duration_seconds, input_data.samples, Path(td))
        if len(frames) < 3:
            raise RuntimeError(
                f"only {len(frames)} frames decoded from {input_data.start_seconds}s "
                f"-- widen duration_seconds or move start_seconds inside the video")

        frozen = _frozen(frames)
        rows, cols = frozen.mean(1), frozen.mean(0)
        baseline = float(np.median(np.concatenate([rows, cols])))
        thr = max(baseline * FROZEN_RATIO, FROZEN_FLOOR)
        logger.info("baseline frozen fraction %.4f -> threshold %.4f", baseline, thr)

        found, bands = [], []
        for edge, prof, length, from_end in (
                ("top", rows, len(rows), False), ("bottom", rows, len(rows), True),
                ("left", cols, len(cols), False), ("right", cols, len(cols), True)):
            b = _band(prof, thr, length, input_data.edge_fraction, from_end)
            if b is None:
                continue
            a, z = b
            bands.append(ChromeBand(edge=edge, start_px=int(a), end_px=int(z),
                                    frozen_fraction=round(float(prof[a:z + 1].mean()), 4),
                                    thickness_px=int(z - a + 1)))
            found.append(edge)

        x0, y0, x1, y1 = 0, 0, w, h
        for b in bands:
            if b.edge == "top":
                y0 = max(y0, b.end_px + 1)
            elif b.edge == "bottom":
                y1 = min(y1, b.start_px)
            elif b.edge == "left":
                x0 = max(x0, b.end_px + 1)
            elif b.edge == "right":
                x1 = min(x1, b.start_px)
        cw, ch = max(x1 - x0, 1), max(y1 - y0, 1)
        floor = 1920.0 / ch

        notes = (
            f"Sampled {len(frames)} frames. Baseline frozen-pixel fraction "
            f"{baseline:.4f}; a line had to clear {thr:.4f} to count as chrome. "
            + (f"Chrome on: {', '.join(found)}. Clean rect {cw}x{ch} at ({x0},{y0}). "
               if bands else "No static band on any searched edge. ")
            + f"A vertical 1080x1920 crop from the clean rect cannot go below "
              f"{floor:.4f}x without letterboxing."
        )
        logger.info(notes)

        return RunOutput(
            chrome_found=bool(bands),
            bands=bands,
            clean_rect=[int(x0), int(y0), int(cw), int(ch)],
            source_size=[w, h],
            baseline_frozen_fraction=round(baseline, 5),
            threshold_used=round(thr, 5),
            full_bleed_floor=round(float(floor), 4),
            notes=notes,
            output_meta=OutputMeta(outputs=[TextMeta(text=notes)]),
        )
