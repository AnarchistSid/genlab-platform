"""BB visual render strategy — uses shared FrameCompositor.

Renders video clips through the same FrameCompositor pipeline as all
other niches (CW, SR, FD). No subprocess delegation.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from genlab_core.media.frame_compositor import FrameCompositor
from genlab_core.strategies import VisualRenderStrategy

BB_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


class BBVisualRenderStrategy(VisualRenderStrategy):
    """Render video reels for AI/tech content using FrameCompositor."""

    def execute(self, context: Any) -> Any:
        stories = context.get("stories", [])
        if not stories:
            logger.info("[ai_creators] VisualRenderStrategy: no stories to render")
            context.setdefault("run_stats", {})["render"] = {
                "status": "no_stories", "rendered": 0,
            }
            return context

        clip_index = context.get("clip_index", {})
        clips = clip_index.get("clips", {})
        run_dir = context.get("run_dir", "")
        visuals_yaml = str(BB_ROOT / "config" / "visuals.yaml")

        rendered = 0
        videos_found = 0

        for story in stories:
            try:
                sid = story.get("story_id", "")
                clip_entry = clips.get(sid, {})

                if clip_entry.get("success") and clip_entry.get("clip_path"):
                    clip_path = Path(clip_entry["clip_path"])
                    if clip_path.exists():
                        hook_text = (
                            (story.get("content") or {}).get("hook", "")
                            or story.get("title", "")
                        )
                        output_dir = Path(run_dir) / "visuals" / sid if run_dir else None
                        if output_dir:
                            output_dir.mkdir(parents=True, exist_ok=True)
                            output_path = str(output_dir / f"{sid[:16]}_reel.mp4")
                        else:
                            output_path = str(clip_path.with_stem(f"{clip_path.stem}_reel"))

                        try:
                            compositor = FrameCompositor.from_visuals_yaml(visuals_yaml)
                            result = compositor.compose(
                                source_video_path=str(clip_path),
                                hook_text=hook_text,
                                output_path=output_path,
                                duration_seconds=55,
                            )
                            if result:
                                story.setdefault("media", {})["rendered_path"] = result
                                story["media"]["render_status"] = "video_ready"
                                story["media"]["compositor"] = "frame_compositor"
                                videos_found += 1
                                rendered += 1
                            else:
                                story.setdefault("media", {})["render_status"] = "render_failed"
                                logger.warning(
                                    "[ai_creators] Render failed: %s",
                                    story.get("title", "")[:50],
                                )
                        except Exception as e:
                            story.setdefault("media", {})["render_status"] = "render_failed"
                            logger.warning("[ai_creators] FrameCompositor error: %s", e)
                        continue

                # No video — story stays DRAFTED (no stock fallback)
                story.setdefault("media", {})["render_status"] = "no_video"

            except Exception:
                logger.exception(
                    "[ai_creators] Failed to render: %s", story.get("title", "?"),
                )

        context.setdefault("run_stats", {})["render"] = {
            "rendered": rendered,
            "total": len(stories),
            "videos_found": videos_found,
        }
        logger.info(
            "[ai_creators] VisualRender: %d/%d with video, %d rendered",
            videos_found, len(stories), rendered,
        )
        return context
