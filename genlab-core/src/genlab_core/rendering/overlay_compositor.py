"""Shared FFmpeg text overlay compositor for Gen Lab.

Used by:
  ClutchWire — sports score overlays (team names + score, top-center)
  SpliceReel — film rating overlays (RT score + IMDB rating, bottom-third)

Design:
  - No side effects: takes input_path + spec -> output_path
  - Stateless and reentrant: safe to call concurrently
  - Hard dependency on FFmpeg being installed and on PATH
  - Falls back gracefully (logs warning, returns input_path unchanged)
    if FFmpeg is not found or the overlay fails
  - Never crashes the pipeline — overlays are cosmetic, not required
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class TextOverlay:
    """Single text element to composite onto a video frame."""

    text: str
    x: str = "(w-text_w)/2"
    y: str = "20"
    font_size: int = 48
    font_color: str = "white@0.95"
    box_color: str = "black@0.70"
    box_padding: int = 12
    font: str = "Impact"


@dataclass
class OverlaySpec:
    """Complete overlay specification for a single video.

    Factory methods for each niche:
      OverlaySpec.sports_score(...)  -> ClutchWire
      OverlaySpec.film_rating(...)   -> SpliceReel
    """

    overlays: list[TextOverlay] = field(default_factory=list)

    @classmethod
    def sports_score(
        cls,
        home_team: str,
        away_team: str,
        home_score: int,
        away_score: int,
    ) -> OverlaySpec:
        """ClutchWire score overlay: "AWAY 3 - 2 HOME" top-center."""
        score_text = f"{away_team}  {away_score} - {home_score}  {home_team}"
        return cls(overlays=[
            TextOverlay(
                text=score_text,
                x="(w-text_w)/2",
                y="24",
                font_size=52,
                font_color="white@0.97",
                box_color="black@0.72",
                box_padding=16,
            ),
        ])

    @classmethod
    def film_rating(
        cls,
        title: str,
        rt_score: int | None = None,
        imdb_score: float | None = None,
    ) -> OverlaySpec:
        """SpliceReel rating overlay: title top-center + scores bottom-third."""
        overlays: list[TextOverlay] = [
            TextOverlay(
                text=title,
                x="(w-text_w)/2",
                y="32",
                font_size=42,
                font_color="white@0.97",
                box_color="black@0.72",
                box_padding=14,
            ),
        ]

        score_parts: list[str] = []
        if rt_score is not None:
            score_parts.append(f"RT {rt_score}%")
        if imdb_score is not None:
            score_parts.append(f"IMDB {imdb_score}/10")

        if score_parts:
            overlays.append(TextOverlay(
                text="  |  ".join(score_parts),
                x="(w-text_w)/2",
                y="h*0.70",
                font_size=44,
                font_color="white@0.97",
                box_color="black@0.72",
                box_padding=14,
            ))

        return cls(overlays=overlays)


def _escape_drawtext(text: str) -> str:
    """Escape special chars for FFmpeg drawtext filter syntax."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\u2019")
        .replace(":", "\\:")
    )


def composite_overlay(
    input_path: Path | str,
    output_path: Path | str,
    spec: OverlaySpec,
    overwrite: bool = True,
) -> Path:
    """Apply text overlays to a video using FFmpeg drawtext filter.

    Falls back to returning input_path unchanged if:
      - FFmpeg is not installed
      - The input file doesn't exist
      - FFmpeg returns non-zero exit code

    Returns:
        Path to output file (or input_path if overlay failed)
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        log.warning("[overlay] input file not found: %s", input_path)
        return input_path

    if shutil.which("ffmpeg") is None:
        log.warning("[overlay] ffmpeg not on PATH — returning original")
        return input_path

    if not spec.overlays:
        log.debug("[overlay] no overlays specified — returning original")
        return input_path

    filter_parts = []
    for overlay in spec.overlays:
        safe_text = _escape_drawtext(overlay.text)
        filter_parts.append(
            f"drawtext="
            f"font='{overlay.font}':"
            f"text='{safe_text}':"
            f"x={overlay.x}:"
            f"y={overlay.y}:"
            f"fontsize={overlay.font_size}:"
            f"fontcolor={overlay.font_color}:"
            f"box=1:"
            f"boxcolor={overlay.box_color}:"
            f"boxborderw={overlay.box_padding}"
        )

    vf_chain = ",".join(filter_parts)

    cmd = ["ffmpeg"]
    if overwrite:
        cmd.append("-y")
    cmd.extend([
        "-i", str(input_path),
        "-vf", vf_chain,
        "-c:a", "copy",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        str(output_path),
    ])

    log.info(
        "[overlay] compositing %d overlay(s) onto %s",
        len(spec.overlays), input_path.name,
    )

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            log.warning(
                "[overlay] ffmpeg failed (exit %d): %s",
                result.returncode,
                result.stderr[-500:] if result.stderr else "(no stderr)",
            )
            return input_path

        log.info("[overlay] compositing complete: %s", output_path.name)
        return output_path

    except subprocess.TimeoutExpired:
        log.warning("[overlay] ffmpeg timeout after 120s — returning original")
        return input_path
    except Exception as e:
        log.warning("[overlay] unexpected error: %s", e)
        return input_path
