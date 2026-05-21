"""Pipeline stage: Burn Whisper-generated ASS captions onto video.

Runs Whisper transcription on rendered videos, generates ASS subtitle
files with word-by-word highlighting, and burns them via FFmpeg.

SEPARATE stage from GENERATE_AUDIO — independent error handling.

Failure handling:
  - Whisper transcription failure: log, pass through video unchanged
  - ASS generation failure: log, pass through video unchanged
  - FFmpeg burn failure: log, keep original video path
  - A failure here NEVER prevents the story from reaching PUBLISH

Usage:
    stage = RenderTextOverlays()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from niches.gaming.tools.ass_subtitle_generator import burn_ass_captions, generate_ass_file
from niches.gaming.tools.caption_generator import load_captions_config, transcribe

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
NICHE_ROOT = Path(__file__).resolve().parent.parent


class RenderTextOverlays:
    """Burn Whisper-generated word-by-word captions onto rendered videos."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        blueprints = context.get("blueprints", [])

        run_id = "unknown"
        from genlab_core.context import get_current_context

        ctx = get_current_context()
        if ctx:
            run_id = ctx.run_id

        captions_config = load_captions_config()
        style_config = captions_config.get("subtitle_style", {})
        fonts_dir = str(PROJECT_ROOT / "assets" / "fonts")

        stats = {
            "captioned": 0,
            "failed": 0,
            "skipped": 0,
        }

        # Process individual stories
        for i, story in enumerate(stories):
            rendered_path = (story.get("media") or {}).get("rendered_path")
            if not rendered_path or not Path(rendered_path).exists():
                stats["skipped"] += 1
                continue

            captioned_path = self._add_captions(
                video_path=rendered_path,
                item_key=f"story_{i}",
                run_id=run_id,
                style_config=style_config,
                fonts_dir=fonts_dir,
            )

            if captioned_path:
                story["media"]["rendered_path"] = captioned_path
                stats["captioned"] += 1
            else:
                # Keep original — video proceeds without captions
                stats["failed"] += 1

        # Process compilation blueprints
        for i, blueprint in enumerate(blueprints):
            rendered_path = blueprint.get("rendered_path")
            if not rendered_path or not Path(rendered_path).exists():
                stats["skipped"] += 1
                continue

            captioned_path = self._add_captions(
                video_path=rendered_path,
                item_key=f"comp_{i}",
                run_id=run_id,
                style_config=style_config,
                fonts_dir=fonts_dir,
            )

            if captioned_path:
                blueprint["rendered_path"] = captioned_path
                stats["captioned"] += 1
            else:
                stats["failed"] += 1

        context.setdefault("run_stats", {})["text_overlays"] = stats
        logger.info(
            "[CAPTIONS] %d captioned, %d failed (passthrough), %d skipped",
            stats["captioned"],
            stats["failed"],
            stats["skipped"],
        )
        return context

    def _add_captions(
        self,
        video_path: str,
        item_key: str,
        run_id: str,
        style_config: dict,
        fonts_dir: str,
    ) -> str | None:
        """Transcribe, generate ASS, burn captions. Returns captioned path or None."""
        work_dir = PROJECT_ROOT / ".tmp" / "captions" / run_id
        work_dir.mkdir(parents=True, exist_ok=True)

        # --- Whisper transcription (separate error domain) ---
        transcription = None
        try:
            transcription = transcribe(video_path)
        except Exception as e:
            logger.warning("[CAPTIONS] Whisper failed for %s: %s", item_key, e)
            return None

        if not transcription or not transcription.get("segments"):
            logger.info("[CAPTIONS] No speech detected in %s — skipping captions", item_key)
            return None

        # --- ASS file generation ---
        ass_path = str(work_dir / f"{item_key}.ass")
        try:
            ass_ok = generate_ass_file(transcription, ass_path, style_config)
        except Exception as e:
            logger.warning("[CAPTIONS] ASS generation failed for %s: %s", item_key, e)
            return None

        if not ass_ok:
            return None

        # --- FFmpeg burn ---
        captioned_path = str(work_dir / f"{item_key}_captioned.mp4")
        try:
            burn_ok = burn_ass_captions(
                video_path=video_path,
                ass_path=ass_path,
                output_path=captioned_path,
                fonts_dir=fonts_dir if Path(fonts_dir).exists() else None,
            )
        except Exception as e:
            logger.warning("[CAPTIONS] FFmpeg burn failed for %s: %s", item_key, e)
            return None

        if burn_ok and Path(captioned_path).exists():
            logger.info("[CAPTIONS] %s → %s", item_key, captioned_path)
            return captioned_path

        return None
