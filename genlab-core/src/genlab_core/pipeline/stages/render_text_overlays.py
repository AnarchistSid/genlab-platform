"""Pipeline stage: Burn text overlays onto rendered videos.

Post-render stage that adds hook text overlays onto the top bar of
rendered videos using FFmpeg drawtext filter.

For each story with a rendered video + hook text:
  1. Probe video dimensions
  2. Calculate text placement (top bar region)
  3. Run FFmpeg drawtext to burn overlay
  4. Update story media path to the overlaid version

Non-fatal: overlay failures leave the video untouched.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from genlab_core.media.ffmpeg import get_ffmpeg_binary
from genlab_core.media.ffmpeg_utils import escape_drawtext

logger = logging.getLogger(__name__)

# Overlay defaults
DEFAULT_FONT_SIZE = 42
DEFAULT_FONT_COLOR = "white"
DEFAULT_BG_COLOR = "black@0.6"
DEFAULT_Y_POSITION = 50  # pixels from top


class RenderTextOverlays:
    """Burn hook text overlays onto rendered videos.

    Reads: context['stories'], context['niche_config']
    Writes: context['stories'][*]['media']['overlaid_path'],
            context['run_stats']['text_overlays']
    """

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        stories = context.get("stories", [])
        if not stories:
            logger.info("[RenderTextOverlays] No stories to overlay")
            return context

        config = context.get("niche_config", {})
        overlay_cfg = config.get("visuals", {}).get("text_overlay", {})

        font_size = overlay_cfg.get("font_size", DEFAULT_FONT_SIZE)
        font_color = overlay_cfg.get("font_color", DEFAULT_FONT_COLOR)
        y_pos = overlay_cfg.get("y_position", DEFAULT_Y_POSITION)

        overlaid = 0
        skipped = 0
        errors = 0

        for story in stories:
            media = story.get("media", {})
            rendered = media.get("rendered_path", "")
            hook_text = (
                story.get("hook", "")
                or (story.get("content") or {}).get("hook", "")
                or media.get("hook_text", "")
            )

            if media.get("compositor") == "frame_compositor":
                # frame_compositor already burned logo + hook into the video
                skipped += 1
                continue

            if not rendered or not Path(rendered).exists():
                skipped += 1
                continue
            if not hook_text:
                skipped += 1
                continue

            try:
                out_path = self._burn_overlay(
                    Path(rendered), hook_text, font_size, font_color, y_pos,
                )
                if out_path:
                    media["overlaid_path"] = str(out_path)
                    media["rendered_path"] = str(out_path)
                    overlaid += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception(
                    "[RenderTextOverlays] Failed for story %s",
                    story.get("story_id", "unknown"),
                )
                errors += 1

        logger.info(
            "[RenderTextOverlays] %d overlaid, %d skipped, %d errors",
            overlaid, skipped, errors,
        )

        context.setdefault("run_stats", {})["text_overlays"] = {
            "overlaid": overlaid,
            "skipped": skipped,
            "errors": errors,
        }

        return context

    @staticmethod
    def _burn_overlay(
        video_path: Path,
        text: str,
        font_size: int,
        font_color: str,
        y_pos: int,
    ) -> Optional[Path]:
        """Burn drawtext overlay onto video, return output path."""
        ffmpeg = get_ffmpeg_binary()

        # Escape text for FFmpeg drawtext (canonical escaping from ffmpeg_utils)
        escaped = escape_drawtext(text)

        # Output alongside source with _overlaid suffix
        out_path = video_path.with_stem(f"{video_path.stem}_overlaid")

        # Drawtext filter: centered horizontally, y_pos from top
        drawtext = (
            f"drawtext=text='{escaped}'"
            f":fontsize={font_size}"
            f":fontcolor={font_color}"
            f":x=(w-text_w)/2"
            f":y={y_pos}"
            f":box=1:boxcolor=black@0.6:boxborderw=10"
        )

        cmd = [
            ffmpeg, "-y",
            "-i", str(video_path),
            "-vf", drawtext,
            "-codec:a", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning(
                    "[RenderTextOverlays] FFmpeg failed: %s",
                    result.stderr[-500:] if result.stderr else "no stderr",
                )
                return None
            return out_path
        except subprocess.TimeoutExpired:
            logger.warning("[RenderTextOverlays] FFmpeg timed out for %s", video_path.name)
            return None
