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
                "status": "no_stories",
                "rendered": 0,
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
                        hook_text = (story.get("content") or {}).get("hook", "") or story.get("title", "")

                        # Pre-render quality gate (PR #784, 2026-07-13).
                        # This niche has its own render code path — the
                        # base class's ``_compose_frame`` gate doesn't
                        # apply here. Without this call ai_creators
                        # would keep shipping LLM refusal preambles as
                        # hooks: 5 already published in 21 days
                        # ("I can't write a hook for this story…").
                        # Session 2026-07-14 audit surfaced the bypass.
                        from genlab_core.rendering.pre_render_quality import (
                            check_pre_render_quality,
                        )

                        _qc = check_pre_render_quality(hook_text, niche_id="ai_creators")
                        if not _qc.ok:
                            logger.warning(
                                "[ai_creators] pre-render quality gate rejected story %s (%s): %s",
                                sid[:16] if sid else "?",
                                _qc.reason,
                                _qc.detail,
                            )
                            story.setdefault("media", {})["render_error"] = f"pre_render_quality:{_qc.reason}"
                            story["media"]["render_status"] = "quality_gate_rejected"
                            continue

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
                                # Task #466 wire (2026-07-06): run the
                                # intelligent-transformation orchestrator
                                # on the composite. Fail-open — returns
                                # the base composite path unchanged if
                                # anything goes wrong.
                                from genlab_core.media.post_render_transform import (
                                    apply_post_render_transformations,
                                )

                                content = story.get("content") or {}
                                blueprint_context = {
                                    "hook": hook_text,
                                    "caption_segments": content.get("caption_segments"),
                                    "title": story.get("title", ""),
                                    "summary": story.get("summary", ""),
                                }
                                # Task #581 (2026-07-08): now returns
                                # (path, arm_ids_by_dimension). The dict
                                # travels through push_to_backlog into
                                # register_pending_feedback so
                                # transformation arms get reward-attributed
                                # per dimension.
                                result, _arm_ids = apply_post_render_transformations(
                                    result,
                                    niche_id="ai_creators",
                                    niche_root=BB_ROOT,
                                    visuals_yaml_path=visuals_yaml,
                                    blueprint_context=blueprint_context,
                                    video_duration_s=55.0,
                                )
                                if _arm_ids:
                                    story["arm_ids_by_dimension"] = dict(_arm_ids)
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
                    "[ai_creators] Failed to render: %s",
                    story.get("title", "?"),
                )

        context.setdefault("run_stats", {})["render"] = {
            "rendered": rendered,
            "total": len(stories),
            "videos_found": videos_found,
        }
        logger.info(
            "[ai_creators] VisualRender: %d/%d with video, %d rendered",
            videos_found,
            len(stories),
            rendered,
        )
        return context
