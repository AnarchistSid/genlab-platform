"""Post-render intelligent-transformation wire (task #466 wire, 2026-07-06).

The intelligent-transformation orchestrator (shipped in the 16-PR
sprint 2026-07-05) exists, is unit-tested, and works when called
directly — but no niche's ``VisualRenderStrategy`` calls it. Every
render on prod produces the base FrameCompositor composite only:
no music mood, no highlight trim, no motion intros/outros, no
animated captions. All 11 dimensions of the bandit are dormant.

This module ships the one-line wire. Each niche's ``visual_render.py``
imports :func:`apply_post_render_transformations` and calls it once,
right after ``FrameCompositor.compose()`` returns the rendered path.

Fail-open contract
------------------
This function NEVER raises. Any error at any stage is logged and the
original composite path is returned unchanged. Same discipline as
``apply_transformations`` — a broken transformation must never lose
the base render, because the reel still needs to publish.

Idempotency
-----------
Safe to call on the same rendered path twice: the second call finds
the flag/config unchanged and produces the same transformed output
(orchestrator's underlying stages are deterministic given the same
bandit-selected arms). In practice each render fires once per story,
so this is theoretical.

Related
-------
- Sprint memory: ``[[session-2026-07-05-sprint-activation-plus-9-follow-ups]]``
- Orchestrator: :func:`genlab_core.media.transformation_orchestrator.apply_transformations`
- Config schema: :class:`genlab_core.media.intelligent_transform.IntelligentTransformConfig`
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def apply_post_render_transformations(
    rendered_path: str,
    *,
    niche_id: str,
    niche_root: Path,
    visuals_yaml_path: str,
    blueprint_context: dict[str, Any] | None = None,
    video_duration_s: float = 55.0,
) -> str:
    """Run the intelligent transformation on a rendered reel.

    Called from each niche's VisualRenderStrategy after
    ``FrameCompositor.compose()`` succeeds. Reads:

    * ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED`` env flag (kill switch)
    * ``visuals.yaml.intelligent_transform`` block (per-niche enable +
      dimension config)

    Both must line up for any transformation to fire.

    Args:
        rendered_path: FrameCompositor.compose output path (str).
        niche_id: ``'ai_creators'``, ``'gaming'``, ``'sports'``,
            ``'movies'``, ``'anime'``.
        niche_root: repo root of the niche (used by orchestrator to
            look up music beds + motion assets).
        visuals_yaml_path: absolute path to the niche's
            ``config/visuals.yaml``.
        blueprint_context: opaque dict carrying hook + caption_segments
            + any bandit context. Passed through to selectors.
        video_duration_s: rendered reel duration in seconds. Used for
            caption timing + highlight window sizing.

    Returns:
        Path to the final reel (str). Either the transformed output
        (if enabled + config allowed) or the input ``rendered_path``
        unchanged (fail-open).
    """
    # Env kill-switch: if the flag isn't "1", short-circuit.
    if os.environ.get("GENLAB_INTELLIGENT_TRANSFORM_ENABLED") != "1":
        logger.debug(
            "[%s] GENLAB_INTELLIGENT_TRANSFORM_ENABLED off — "
            "post_render_transform skipping",
            niche_id,
        )
        return rendered_path

    try:
        # Deferred imports so a stale bytecode / missing dep in the
        # transformation subtree can never break the base render path.
        import yaml

        from genlab_core.media.intelligent_transform import (
            IntelligentTransformConfig,
        )
        from genlab_core.media.transformation_orchestrator import (
            apply_transformations,
        )
    except Exception as exc:  # pragma: no cover — dep import failure
        logger.warning(
            "[%s] post_render_transform dep import failed (%s) — "
            "returning base composite",
            niche_id, exc,
        )
        return rendered_path

    try:
        with open(visuals_yaml_path) as f:
            visuals = yaml.safe_load(f) or {}
        cfg = IntelligentTransformConfig.from_visuals_dict(visuals)
    except Exception as exc:
        logger.warning(
            "[%s] visuals.yaml parse failed (%s) — returning base composite",
            niche_id, exc,
        )
        return rendered_path

    if not getattr(cfg, "enabled", False):
        logger.debug(
            "[%s] intelligent_transform.enabled=false in visuals.yaml — "
            "post_render_transform skipping",
            niche_id,
        )
        return rendered_path

    # Write transformed output alongside the composite. On success we
    # rename over the composite so downstream stages (validate_videos,
    # publisher) see the transformed file at the same path they already
    # expect. On failure the composite is untouched.
    rendered = Path(rendered_path)
    transformed = rendered.with_stem(f"{rendered.stem}_transformed")

    try:
        result = apply_transformations(
            source_video_path=rendered,
            output_path=transformed,
            niche_root=niche_root,
            niche_id=niche_id,
            config=cfg,
            blueprint_context=blueprint_context or {},
            video_duration_s=video_duration_s,
        )
    except Exception as exc:  # apply_transformations promises no
        # raise, but defense in depth.
        logger.warning(
            "[%s] apply_transformations raised (%s) — returning base composite",
            niche_id, exc,
        )
        return rendered_path

    # Any stages actually applied? Verify the file exists + has bytes.
    if transformed.is_file() and transformed.stat().st_size > 0:
        # Replace the composite in-place so downstream sees the
        # transformed reel at the same path.
        try:
            transformed.replace(rendered)
        except Exception as exc:
            logger.warning(
                "[%s] transformed-file rename failed (%s) — returning "
                "transformed side-file",
                niche_id, exc,
            )
            return str(transformed)
        logger.info(
            "[%s] Intelligent transformation applied: stages=%s",
            niche_id, list(result.stages_applied),
        )
        return rendered_path  # same path, now overwritten with transformed bytes

    logger.info(
        "[%s] Intelligent transformation produced no output — returning "
        "base composite (stages_applied=%s, stages_skipped=%s)",
        niche_id,
        list(result.stages_applied),
        list(result.stages_skipped),
    )
    return rendered_path
