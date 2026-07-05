"""Transformation pipeline orchestrator — PR 15 wire-in point.

Composes all 5 render-side transformation modules from PRs 7-12 into
a single pipeline that runs behind the ``GENLAB_INTELLIGENT_TRANSFORM_
ENABLED`` env flag. This is where the sprint's independent modules
connect into a single "apply everything the bandit picked" flow.

Pipeline order matters — later stages operate on earlier stages'
output:

  1. **Highlight detection** (PR 12) — pick which N seconds of the
     source to actually use. Downstream stages operate on the trimmed
     window.
  2. **Audio replacement** (PR 7) — replace source audio with music
     bed. Runs first among the render stages because subsequent
     video-only filters preserve audio via ``-c:a copy``.
  3. **Pan/zoom** (PR 9) — apply zoom/pan filter. Video-only.
  4. **Caption overlay** (PR 11) — burn animated captions on top.
     Video-only.
  5. **Motion graphics** (PR 8) — wrap with intro/outro concat.
     Encapsulates everything else, so it goes LAST.

Fail-open at every stage: if any transformation returns False, the
orchestrator keeps the previous intermediate as-is and moves on. The
final output is whatever the last successful stage produced —
possibly the original source if all stages skip.

Two-gate enablement (matches selector):
  * ``config.enabled`` per niche (from visuals.yaml)
  * ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED`` env var

Both must be true for any transformation to apply. Gates check
BEFORE any expensive work (selector query, file I/O).

Related sprint context:
- PR 4 (#668) — selector we call
- PR 5 (#669) — router that reads arm_ids_by_dimension we return
- PR 7-12 (#671-676) — modules we compose
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TransformationResult:
    """Result of running the orchestrator on one reel.

    Attributes:
        output_path: final rendered video. Same as source when
            transformation was skipped (flag off or all stages
            failed).
        arm_ids_by_dimension: payload for pending_feedback
            arm_ids_by_dimension column. Empty when no transformation
            was selected/applied.
        stages_applied: names of stages that returned True — useful
            for debugging + Mission Control instrumentation.
        stages_skipped: names of stages that returned False — normal
            when assets missing (music library, intro/outro assets).
    """

    output_path: Path
    arm_ids_by_dimension: dict[str, str] = field(default_factory=dict)
    stages_applied: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)


def _flag_enabled() -> bool:
    """Global env kill-switch check. Matches selector's semantics."""
    value = os.environ.get("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "").strip().lower()
    return value in ("1", "true", "yes", "on")


def _trim_video(
    source_path: Path,
    output_path: Path,
    start_s: float,
    end_s: float,
    *,
    timeout_seconds: int = 120,
) -> bool:
    """Trim a video to [start_s, end_s] using FFmpeg stream-copy.

    Uses ``-c copy`` for fast passthrough — no re-encode. Frame-accurate
    at keyframes only, which is fine for highlight windows that don't
    need pixel-perfect start times (the bandit is picking a rough
    "interesting region," not a frame-precise cut).

    Returns True on success, False on:
      * ffmpeg unavailable
      * timeout / nonzero exit / tiny output
    """
    import subprocess

    from genlab_core.media.ffmpeg import get_ffmpeg_binary

    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError as exc:
        logger.warning("[transformation_orchestrator] ffmpeg not available for trim: %s", exc)
        return False

    duration = max(0.1, end_s - start_s)
    cmd = [
        ffmpeg,
        "-y",
        "-ss", f"{start_s:.3f}",
        "-i", str(source_path),
        "-t", f"{duration:.3f}",
        "-c", "copy",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning(
            "[transformation_orchestrator] trim ffmpeg failed: %s", exc
        )
        return False
    if result.returncode != 0:
        logger.warning(
            "[transformation_orchestrator] trim exit=%d — %s",
            result.returncode,
            "\n".join(result.stderr.strip().splitlines()[-3:]),
        )
        return False
    if not output_path.exists() or output_path.stat().st_size < 1024:
        return False
    return True


def _get_source_duck_db(config, choices) -> int:
    """Pick the source-audio ducking level.

    Priority:
      1. If ``audio_ducking`` arm was chosen, use its int value
      2. Else use ``music_mood`` dim's default from config
      3. Else -12 dB baseline
    """
    if "audio_ducking" in choices.choices:
        try:
            return int(choices.choices["audio_ducking"].dimension_value)
        except (ValueError, TypeError):
            pass
    try:
        return int(config.dimensions.music_mood.source_audio_duck_db)
    except (AttributeError, ValueError, TypeError):
        return -12


def apply_transformations(
    source_video_path: Path,
    output_path: Path,
    niche_root: Path,
    niche_id: str,
    config,
    *,
    blueprint_context: dict[str, Any] | None = None,
    video_duration_s: float = 20.0,
) -> TransformationResult:
    """Apply the full transformation pipeline to a source video.

    Args:
        source_video_path: input video (usually FrameCompositor.compose
            output — the branded reel with logo overlay).
        output_path: where to write the final transformed video.
        niche_root: repo root of the niche (for asset lookup).
        niche_id: 'gaming', 'movies', etc.
        config: parsed ``IntelligentTransformConfig`` for this niche.
        blueprint_context: opaque dict; carried into selection for
            LinUCB context. Unused directly here.
        video_duration_s: source video duration in seconds — used for
            caption timing + highlight_moment window sizing.

    Returns:
        TransformationResult. When either gate is off, returns
        ``TransformationResult(output_path=source_video_path)`` with
        empty arm_ids_by_dimension.

    Fail-open contract: this function NEVER raises. Any error at any
    stage is logged, that stage is marked skipped, and the pipeline
    moves on with the previous intermediate. The output_path in the
    return is always a valid readable file — either the transformed
    result or the source (via shutil.copy).
    """
    # Gate 1: env kill-switch
    if not _flag_enabled():
        logger.debug(
            "[transformation_orchestrator] GENLAB_INTELLIGENT_TRANSFORM_ENABLED off "
            "— pipeline no-op for %s", niche_id,
        )
        return TransformationResult(output_path=source_video_path)

    # Gate 2: per-niche config
    if not getattr(config, "enabled", False):
        logger.debug(
            "[transformation_orchestrator] config disabled for %s", niche_id
        )
        return TransformationResult(output_path=source_video_path)

    # Run selector
    try:
        from genlab_core.media.transformation_selector import (
            select_transformation_dimensions,
        )

        choices = select_transformation_dimensions(niche_id, config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[transformation_orchestrator] selector failed for %s: %s",
            niche_id, exc,
        )
        return TransformationResult(output_path=source_video_path)

    if choices.is_empty():
        logger.info(
            "[transformation_orchestrator] no choices selected for %s "
            "(cold-start or all filtered out)", niche_id,
        )
        return TransformationResult(output_path=source_video_path)

    logger.info(
        "[transformation_orchestrator] running %d transformation stages "
        "for %s: %s",
        len(choices.choices), niche_id, sorted(choices.choices.keys()),
    )

    result = TransformationResult(
        output_path=source_video_path,
        arm_ids_by_dimension=choices.to_arm_ids_by_dimension(),
    )

    # Working directory for intermediates
    temp_dir = Path(tempfile.mkdtemp(prefix="genlab_transform_"))
    try:
        current_path = source_video_path

        # ── Stage 0: highlight-window trim ─────────────────────
        # Consumer wire for PR 12's HighlightDetector (task #522).
        # Runs BEFORE audio/pan/captions/motion so every downstream
        # stage operates on the trimmed clip. When the highlight_moment
        # arm picks 'first_frame' (baseline), the window starts at 0
        # and this stage is a near-no-op — we skip the ffmpeg pass
        # entirely in that case to save render time.
        if "highlight_moment" in choices.choices:
            method = choices.choices["highlight_moment"].dimension_value
            try:
                window_seconds = int(
                    config.dimensions.highlight_moment.window_seconds
                )
            except (AttributeError, ValueError, TypeError):
                window_seconds = 8

            try:
                from genlab_core.media.highlight_detector import (
                    detect_highlight,
                )

                window = detect_highlight(
                    current_path,
                    method,
                    video_duration_s=video_duration_s,
                    window_s=float(window_seconds),
                    fallback_to_first_frame=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[transformation_orchestrator] highlight_detector "
                    "raised: %s", exc,
                )
                window = None

            # Skip the trim pass when the window starts at 0 AND covers
            # the full source (baseline first_frame with window >= dur)
            # — saves an unnecessary ffmpeg round-trip.
            need_trim = (
                window is not None
                and window.is_valid()
                and (
                    window.start_s > 0.01
                    or window.end_s < video_duration_s - 0.01
                )
            )
            if need_trim:
                trim_path = temp_dir / "00_trim.mp4"
                trim_success = _trim_video(
                    current_path, trim_path, window.start_s, window.end_s
                )
                if trim_success:
                    current_path = trim_path
                    # Downstream caption timing must reflect the
                    # trimmed duration.
                    video_duration_s = window.end_s - window.start_s
                    result.stages_applied.append("highlight_moment")
                else:
                    result.stages_skipped.append("highlight_moment")
            else:
                # Fallback to first_frame or full-video window — treat
                # as a valid selection (arm still gets attributed) but
                # no ffmpeg pass needed.
                result.stages_applied.append("highlight_moment")

        # ── Stage 1: audio replacement ─────────────────────────
        if "music_mood" in choices.choices:
            music_mood = choices.choices["music_mood"].dimension_value
            duck_db = _get_source_duck_db(config, choices)
            try:
                music_bed_db = int(config.dimensions.music_mood.music_bed_db)
            except (AttributeError, ValueError, TypeError):
                music_bed_db = -6

            next_path = temp_dir / "01_audio.mp4"
            try:
                from genlab_core.media.audio_replacer import (
                    replace_audio_for_reel,
                )

                success = replace_audio_for_reel(
                    niche_root=niche_root,
                    source_video_path=current_path,
                    output_path=next_path,
                    mood_tag=music_mood,
                    source_duck_db=duck_db,
                    music_bed_db=music_bed_db,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[transformation_orchestrator] audio_replacer raised: %s",
                    exc,
                )
                success = False
            if success:
                current_path = next_path
                result.stages_applied.append("music_mood")
            else:
                result.stages_skipped.append("music_mood")

        # ── Stage 2: pan/zoom ──────────────────────────────────
        if "pan_zoom" in choices.choices:
            pattern = choices.choices["pan_zoom"].dimension_value
            next_path = temp_dir / "02_panzoom.mp4"
            try:
                from genlab_core.media.pan_zoom import apply_pan_zoom_for_reel

                success = apply_pan_zoom_for_reel(
                    source_video_path=current_path,
                    output_path=next_path,
                    pattern=pattern,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[transformation_orchestrator] pan_zoom raised: %s", exc
                )
                success = False
            if success:
                current_path = next_path
                result.stages_applied.append("pan_zoom")
            else:
                result.stages_skipped.append("pan_zoom")

        # ── Stage 3: caption overlay ───────────────────────────
        # Requires blueprint_context to carry pre-computed segments
        # (from PR 10's generate_caption_segments in the writer stage).
        # Writer stores as list[dict] for DB round-trip compatibility;
        # convert back to CaptionSegment objects for the animator.
        segments = None
        if blueprint_context is not None:
            raw = blueprint_context.get("caption_segments")
            if isinstance(raw, list) and raw:
                try:
                    from genlab_core.writing.caption_segments import (
                        CaptionSegment,
                    )

                    segments = [
                        CaptionSegment(
                            text=item.get("text", ""),
                            emphasis_words=list(item.get("emphasis_words") or []),
                        )
                        for item in raw
                        if isinstance(item, dict) and item.get("text")
                    ]
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "[transformation_orchestrator] caption_segments "
                        "deserialize failed: %s", exc,
                    )
                    segments = None

        if segments and "caption_style" in choices.choices:
            style = choices.choices["caption_style"].dimension_value
            emphasis_color = (
                choices.choices.get("caption_emphasis_color")
                and choices.choices["caption_emphasis_color"].dimension_value
                or "yellow"
            )
            try:
                pacing_ms = int(
                    choices.choices["caption_pacing"].dimension_value
                ) if "caption_pacing" in choices.choices else 750
            except (ValueError, TypeError):
                pacing_ms = 750

            next_path = temp_dir / "03_captions.mp4"
            try:
                from genlab_core.media.caption_animator import (
                    CaptionAnimationSpec,
                    apply_captions,
                )

                spec = CaptionAnimationSpec(
                    source_video_path=current_path,
                    output_path=next_path,
                    segments=segments,
                    style=style,
                    emphasis_color=emphasis_color,
                    pacing_ms=pacing_ms,
                    video_duration_s=video_duration_s,
                )
                success = apply_captions(spec)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[transformation_orchestrator] caption_animator raised: %s",
                    exc,
                )
                success = False
            if success:
                current_path = next_path
                result.stages_applied.append("caption_style")
            else:
                result.stages_skipped.append("caption_style")
        elif "caption_style" in choices.choices:
            # Style was chosen but no segments produced (writer path
            # didn't run generate_caption_segments) — skip cleanly.
            result.stages_skipped.append("caption_style")

        # ── Stage 4: motion graphics wrap ──────────────────────
        # Wraps intro + current + outro. Runs LAST because it
        # encapsulates all prior stages.
        intro_choice = choices.choices.get("intro_animation")
        outro_choice = choices.choices.get("outro_cta")
        if intro_choice or outro_choice:
            next_path = temp_dir / "04_motion.mp4"
            try:
                from genlab_core.media.motion_compositor import (
                    MotionAssetChoice,
                    composite_for_reel,
                )

                intro_dir = (
                    config.dimensions.intro_animation.asset_dir
                    or "assets/motion/intros"
                )
                outro_dir = (
                    config.dimensions.outro_cta.asset_dir
                    or "assets/motion/outros"
                )
                asset_choice = MotionAssetChoice(
                    niche_root=niche_root,
                    intro_template=(
                        intro_choice.dimension_value if intro_choice else ""
                    ),
                    intro_asset_dir=intro_dir,
                    outro_template=(
                        outro_choice.dimension_value if outro_choice else ""
                    ),
                    outro_asset_dir=outro_dir,
                )
                success = composite_for_reel(
                    asset_choice, current_path, next_path
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[transformation_orchestrator] motion_compositor raised: %s",
                    exc,
                )
                success = False
            if success:
                current_path = next_path
                if intro_choice:
                    result.stages_applied.append("intro_animation")
                if outro_choice:
                    result.stages_applied.append("outro_cta")
            else:
                if intro_choice:
                    result.stages_skipped.append("intro_animation")
                if outro_choice:
                    result.stages_skipped.append("outro_cta")

        # ── Finalize: copy the last intermediate to output_path ───
        # If NO stage applied (all skipped), current_path is still
        # source — the copy is a no-op safety net.
        if current_path != source_video_path:
            try:
                shutil.copyfile(current_path, output_path)
                result.output_path = output_path
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[transformation_orchestrator] final copy failed: %s "
                    "— falling back to source", exc,
                )
                result.output_path = source_video_path
        else:
            # No transformation succeeded — leave output_path unwritten
            # and return source path. Caller uses that.
            result.output_path = source_video_path
    finally:
        # Best-effort cleanup of intermediates. Don't raise on cleanup
        # failure — the return is already computed.
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    logger.info(
        "[transformation_orchestrator] %s complete — applied=%s skipped=%s "
        "output=%s",
        niche_id,
        result.stages_applied,
        result.stages_skipped,
        result.output_path,
    )
    return result


__all__ = [
    "TransformationResult",
    "apply_transformations",
]
