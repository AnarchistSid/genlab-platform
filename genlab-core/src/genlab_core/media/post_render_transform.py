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

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _probe_duration_seconds(video_path: Path) -> float | None:
    """Return the duration of ``video_path`` in seconds via ffprobe, or None on
    any failure. Used by the min-duration guard below.

    Kept local to this module (vs importing ValidateVideos._probe) so this
    guard doesn't drag validate_videos.py + its VMAF machinery into every
    render pipeline invocation. The 2-line ffprobe call is cheap.
    """
    if not video_path.is_file():
        return None
    try:
        raw = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(video_path),
            ],
            timeout=15,
        )
        data = json.loads(raw)
        dur = data.get("format", {}).get("duration")
        return float(dur) if dur is not None else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, KeyError):
        return None


def apply_post_render_transformations(
    rendered_path: str,
    *,
    niche_id: str,
    niche_root: Path,
    visuals_yaml_path: str,
    blueprint_context: dict[str, Any] | None = None,
    video_duration_s: float = 55.0,
) -> tuple[str, dict[str, str]]:
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
        A 2-tuple of ``(path, arm_ids_by_dimension)``:

        * ``path`` — the final reel path (str). Either the transformed
          output (if enabled + config allowed AND stages actually ran)
          or the input ``rendered_path`` unchanged (fail-open).
        * ``arm_ids_by_dimension`` — dict of ``{dimension: arm_id}``
          pairs for every transformation dimension the selector picked
          arms for. Empty ``{}`` on every fail-open path (flag off,
          config off, exception at any stage) — signals "no arms
          selected, no reward attribution".

        Task #581 (2026-07-08) changed this from ``str`` to
        ``tuple[str, dict]`` because the pre-fix return discarded
        ``TransformationResult.arm_ids_by_dimension``. 255 registered
        transformation bandit arms had α=β=1 for weeks because reward
        never routed to them — the selector picked, the orchestrator
        applied, and then the caller threw away the arm attribution.
        See ``docs/AUDIT-2026-07-08.md`` §11.2 and
        ``audit-round-4-2026-07-08.md``.
    """
    # Env kill-switch. Uses the shared ``env_true`` helper so this site's
    # truthiness semantics match the sibling checks in
    # ``transformation_orchestrator._flag_enabled`` and
    # ``transformation_selector._flag_enabled``. Round-3 flag audit
    # (2026-07-08) found this site alone used ``!= "1"`` (strict) while
    # the siblings used ``.lower() in ("1", "true", "yes", "on")`` —
    # setting the flag to ``"true"`` produced a partial fire (selector
    # picks arms, orchestrator accepts, post_render silently rejects).
    from genlab_core.settings import env_true

    if not env_true("GENLAB_INTELLIGENT_TRANSFORM_ENABLED"):
        logger.debug(
            "[%s] GENLAB_INTELLIGENT_TRANSFORM_ENABLED off — post_render_transform skipping",
            niche_id,
        )
        return rendered_path, {}

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
            "[%s] post_render_transform dep import failed (%s) — returning base composite",
            niche_id,
            exc,
        )
        return rendered_path, {}

    try:
        with open(visuals_yaml_path) as f:
            visuals = yaml.safe_load(f) or {}
        cfg = IntelligentTransformConfig.from_visuals_dict(visuals)
    except Exception as exc:
        logger.warning(
            "[%s] visuals.yaml parse failed (%s) — returning base composite",
            niche_id,
            exc,
        )
        return rendered_path, {}

    if not getattr(cfg, "enabled", False):
        logger.debug(
            "[%s] intelligent_transform.enabled=false in visuals.yaml — "
            "post_render_transform skipping",
            niche_id,
        )
        return rendered_path, {}

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
            niche_id,
            exc,
        )
        return rendered_path, {}

    # Any stages actually applied? Verify the file exists + has bytes.
    if transformed.is_file() and transformed.stat().st_size > 0:
        # 2026-07-07 min-duration guard — live-fire caught the
        # transformation orchestrator producing 13.056s output on
        # 28s source clips because HighlightMomentConfig.window_seconds
        # defaults to 8 and intro/outro concat adds only ~3s each.
        # ValidateVideos then rejects with ``too_short:13.1s`` (SPEC.
        # min_duration=15.0). Every render since transformation
        # activated 2026-07-06 hit this — 4 gaming + 4 anime + 7
        # movies + 4 sports + 4 ai_creators DRAFTED as of catch time.
        #
        # Rather than risk transformation degrading the reel below
        # spec, fall back to the untransformed composite whenever the
        # transformed output would fail publish. The base composite
        # is known-good (source-clip duration untouched); the
        # transformation is only a value-add — never worth failing
        # publish for.
        _SPEC_MIN_DURATION_S = 15.0
        try:
            probe_dur = _probe_duration_seconds(transformed)
        except Exception as exc:  # noqa: BLE001 — fail-open to base
            logger.warning(
                "[%s] transformed-duration probe failed (%s) — returning base composite",
                niche_id,
                exc,
            )
            return rendered_path, {}
        if probe_dur is not None and probe_dur < _SPEC_MIN_DURATION_S:
            logger.warning(
                "[%s] transformed output %.2fs < SPEC.min_duration %.1fs "
                "— returning base composite (stages_applied=%s). Check "
                "HighlightMomentConfig.window_seconds vs intro+outro "
                "concat overhead.",
                niche_id,
                probe_dur,
                _SPEC_MIN_DURATION_S,
                list(result.stages_applied),
            )
            # Leave the base composite untouched at ``rendered``.
            # The transformed side-file will get cleaned by disk_cleanup.
            # Return empty arm_ids: the orchestrator picked arms but we
            # rejected the output, so no reward should be attributed
            # (would train the bandit on a bad experience it didn't
            # actually cause).
            return rendered_path, {}

        # Replace the composite in-place so downstream sees the
        # transformed reel at the same path.
        try:
            transformed.replace(rendered)
        except Exception as exc:
            logger.warning(
                "[%s] transformed-file rename failed (%s) — returning transformed side-file",
                niche_id,
                exc,
            )
            # Rename failed but transformation DID complete — return the
            # side-file path AND the arms so reward still attributes.
            return str(transformed), dict(result.arm_ids_by_dimension)
        logger.info(
            "[%s] Intelligent transformation applied: stages=%s",
            niche_id,
            list(result.stages_applied),
        )
        # Success — same path, now overwritten with transformed bytes,
        # AND the arm attribution dict so reward routes per-dimension.
        return rendered_path, dict(result.arm_ids_by_dimension)

    logger.info(
        "[%s] Intelligent transformation produced no output — returning "
        "base composite (stages_applied=%s, stages_skipped=%s)",
        niche_id,
        list(result.stages_applied),
        list(result.stages_skipped),
    )
    # Orchestrator ran but every stage skipped (no music library, no
    # intro/outro assets, etc.). No transformation applied → no arm
    # attribution. Empty dict is correct here.
    return rendered_path, {}
