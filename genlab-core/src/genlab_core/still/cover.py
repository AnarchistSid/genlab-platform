"""The grid cover — composed for the 4:5 crop, not the 9:16 frame.

ANIME-16 §17, §18. Instagram's grid shows a 4:5 CENTRE CROP of a 9:16 reel.
On a 1080x1920 frame that is y 285-1635; everything outside it is invisible in
the grid. v4 put the cover headline at y 0.72h = 1382 (inside, just) and the
brand mark below it, in the bottom 15% — gone.

So the safe zone is not the reel's safe zone, and the composition is not the
reel's composition:

* headline in the display face at ~60% of frame width, in the CENTRE band
* the show's title small beneath it
* the brand mark bottom-centre but INSIDE 4:5
* the face in the upper-centre of the crop, which means upper-middle of the
  full frame
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FRAME_W, FRAME_H = 1080, 1920
#: The 4:5 centre crop Instagram's grid shows.
CROP_H = int(FRAME_W * 5 / 4)  # 1350
CROP_TOP = (FRAME_H - CROP_H) // 2  # 285
CROP_BOTTOM = CROP_TOP + CROP_H  # 1635
#: §17 — cover text lives in the centre band of the FULL frame.
BAND_TOP, BAND_BOTTOM = 0.35, 0.65
HEADLINE_WIDTH_FRAC = 0.60


@dataclass(frozen=True)
class Cover:
    headline: str
    title: str
    brand: str
    accent: str = "#7B3FE4"


def in_grid_crop(y_px: float) -> bool:
    """Whether a y position survives the 4:5 grid crop."""
    return CROP_TOP <= y_px <= CROP_BOTTOM


def render(
    cover: Cover,
    source: Path,
    dest: Path,
    *,
    display_font: Path,
    body_font: Path,
    timeout_s: int = 300,
) -> dict:
    """Compose the cover. Returns the placement it used, for the manifest."""
    from genlab_core.still import typography as T
    from genlab_core.still.overlay import _textfile

    textdir = dest.parent / f".{dest.stem}_text"
    head = T.target_width_fit(
        cover.headline.upper(),
        display_font,
        target_frac=HEADLINE_WIDTH_FRAC,
        max_lines=2,
        max_size=200,
    )
    if len(head.lines) > 1:
        head = T.fit(
            cover.headline.upper(),
            display_font,
            max_size=150,
            min_size=60,
            max_lines=2,
            width_frac=0.86,
        )

    # EVERY drawtext carries its fontfile. The first version measured with
    # Anton and drew without `fontfile=`, so ffmpeg rendered the default face
    # at Anton's size — and the default face is much wider, so the headline
    # clipped both edges while the manifest reported a comfortable 774px.
    # Measuring one font and drawing another is the same shape as computing a
    # bound two ways; here it is one glyph set two ways.
    dff = f":fontfile={display_font}"
    bff = f":fontfile={body_font}"
    y_head = FRAME_H * BAND_TOP
    line_h = head.size + 16
    parts = []
    for i, line in enumerate(head.lines):
        path = _textfile(textdir, f"cov_head_{i}", line)
        parts.append(
            f"drawtext=textfile='{path}':fontsize={head.size}:fontcolor=white"
            f":borderw=8:bordercolor=black@0.92"
            f":x=(w-text_w)/2:y={y_head + i * line_h:.0f}{dff}"
        )
    y_title = y_head + len(head.lines) * line_h + 28
    tfit = T.fit(cover.title.upper(), body_font, max_size=52, min_size=28, max_lines=1)
    parts.append(
        f"drawtext=textfile='{_textfile(textdir, 'cov_title', tfit.lines[0])}'"
        f":fontsize={tfit.size}:fontcolor=0x{cover.accent.lstrip('#')}"
        f":borderw=4:bordercolor=black@0.9:x=(w-text_w)/2:y={y_title:.0f}{bff}"
    )
    # Brand mark bottom-centre INSIDE the 4:5 crop, not inside the 9:16 frame.
    y_brand = CROP_BOTTOM - 110
    bfit = T.fit(cover.brand, display_font, max_size=64, min_size=34, max_lines=1)
    parts.append(
        f"drawtext=textfile='{_textfile(textdir, 'cov_brand', bfit.lines[0])}'"
        f":fontsize={bfit.size}:fontcolor=white@0.92:borderw=4:bordercolor=black@0.85"
        f":x=(w-text_w)/2:y={y_brand:.0f}{dff}"
    )

    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "eq=contrast=1.14:saturation=1.10," + ",".join(parts)
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-frames:v", "1", "-vf", vf, str(dest)],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if r.returncode != 0:
        raise RuntimeError(f"cover render failed: {r.stderr[-300:]}")

    placement = {
        "headline_lines": head.lines,
        "headline_size": head.size,
        "headline_widest_px": round(head.widest_px),
        "headline_width_frac": round(head.widest_px / FRAME_W, 3),
        "headline_y": round(y_head),
        "title_y": round(y_title),
        "brand_y": y_brand,
        "all_text_inside_4x5_crop": all(
            in_grid_crop(y)
            for y in (y_head, y_head + (len(head.lines) - 1) * line_h, y_title, y_brand + bfit.size)
        ),
        "grid_crop": [CROP_TOP, CROP_BOTTOM],
    }
    if not placement["all_text_inside_4x5_crop"]:
        logger.warning("[cover] some text falls outside the 4:5 grid crop %s", placement)
    return placement
