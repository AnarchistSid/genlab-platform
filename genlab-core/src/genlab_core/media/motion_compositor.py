"""Motion graphics compositor — intro + source + outro concatenation.

Prepends a branded intro (3-5s brand animation) and appends a branded
outro (2-3s CTA animation) around the source reel. Consumer of the
``intro_animation`` and ``outro_cta`` transformation dimensions from
the bandit selector.

**Design decision: concat filter, not concat demuxer**
The concat demuxer is faster (`-c copy`, no re-encode) but requires
all inputs to match exactly on codec/resolution/fps. Operator-designed
Canva exports may vary in framerate or use different codec profiles.
The concat filter normalizes each input to 1080×1920 + reencodes,
which is robust at the cost of ~2-5s extra render time.

Variable-segment support:
  * intro + source + outro (3 segments) — full transformation
  * intro + source (2 segments) — outro asset missing/disabled
  * source + outro (2 segments) — intro asset missing/disabled
  * source only (1 segment) — no-op, returns source path unchanged
    (skips FFmpeg invocation entirely)

Audio handling:
Each intro/outro asset MUST have an audio stream (even silent). Canva
exports typically include a silent audio track by default. If an
asset lacks audio, ``ffmpeg concat filter`` errors — the module logs
+ returns False (caller falls back to legacy path).

Color-space compliance (per CLAUDE.md):
Output uses ``bt709`` for color space, primaries, and transfer curve.
Reencode uses libx264 CRF 20 preset=fast (matches PLATFORM_SPECS).

Related sprint context:
- [[zero-cost-transformation-stack-2026-07-05]] — motion graphics is
  YouTube-named as a valid transformation menu item
- PR 4 (#668) — selector picks intro_animation/outro_cta value
- Operator setup: Canva-designed 1080×1920 MP4 intros/outros per niche
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MotionCompositeSpec:
    """Parameters for one intro+source+outro composite render.

    intro_path and outro_path are optional — either or both may be
    None, in which case the corresponding segment is skipped.
    """

    source_video_path: Path
    output_path: Path
    intro_path: Path | None = None
    outro_path: Path | None = None
    target_width: int = 1080
    target_height: int = 1920
    crf: int = 20
    preset: str = "fast"
    audio_bitrate: str = "128k"


def _resolve_asset_path(
    niche_root: Path, dimension: str, asset_dir: str, template_name: str
) -> Path | None:
    """Resolve a niche_root + asset dir + template name to a real path.

    Expected layout:
        <niche_root>/<asset_dir>/<template_name>.mp4

    Returns None when the file doesn't exist — caller treats absent
    assets as "skip this segment", matching the audio_replacer's
    fail-open library pattern.
    """
    if not template_name:
        return None
    if not asset_dir:
        return None
    # Try common extensions in the same style as audio_replacer
    for ext in (".mp4", ".mov", ".webm"):
        candidate = niche_root / asset_dir / f"{template_name}{ext}"
        if candidate.is_file():
            return candidate
    # Task #631 (2026-07-09 late evening): elevated DEBUG → WARNING.
    # Live-fire showed this fires silently when the bandit picks a
    # template name that doesn't have a physical asset file. Silent
    # because DEBUG isn't in journalctl at default level. Result:
    # motion_compositor returns False → intro/outro skipped → arm
    # attribution discarded. Making this WARNING so future asset-vs-
    # config drift surfaces at the ops layer.
    logger.warning(
        "[motion_compositor] asset not found for %s: tried "
        "%s/%s/{mp4,mov,webm}. Bandit picked template that has no "
        "physical asset — arm attribution for this dimension will "
        "be discarded downstream.",
        dimension,
        asset_dir,
        template_name,
    )
    return None


def _normalize_segment_label(index: int) -> str:
    """FFmpeg filter labels can't collide across segments."""
    return f"v{index}"


def build_concat_filtergraph(n_segments: int, width: int, height: int) -> str:
    """Build the FFmpeg filter_complex string for concat with normalization.

    Each input's video is scaled + padded to (width, height) so mismatched
    aspect ratios don't distort. Each input's AUDIO is normalized to
    48 kHz stereo via ``aformat`` — the concat filter's ``a=1`` output
    requires all audio inputs to share sample rate + channel layout, or
    it fails with ``[aost#0:1/aac] error code: -22 (Invalid argument),
    Nothing was written into output file``. Live-fire caught this on
    2026-07-06 movies render 1 of 5 when the source clip's audio was
    mono/44100Hz and my placeholder motion assets were stereo/48kHz.

    Example for n_segments=3:
        [0:v]scale=1080:1920:force_original_aspect_ratio=decrease,
        pad=1080:1920:-1:-1:color=black[v0];
        [1:v]scale=...pad...[v1];
        [2:v]scale=...pad...[v2];
        [0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];
        [1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];
        [2:a]aformat=sample_rates=48000:channel_layouts=stereo[a2];
        [v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[outv][outa]
    """
    if n_segments < 1:
        raise ValueError("n_segments must be >= 1")

    parts: list[str] = []
    scale_expr = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:-1:-1:color=black"
    )
    # Video normalization
    for i in range(n_segments):
        parts.append(f"[{i}:v]{scale_expr}[v{i}]")

    # Audio normalization — matches the outer -ar 48000 -ac 2 encode
    # spec so concat has homogeneous inputs. Regression here reproduces
    # the exit=-22 failure from 2026-07-06.
    aformat_expr = "aformat=sample_rates=48000:channel_layouts=stereo"
    for i in range(n_segments):
        parts.append(f"[{i}:a]{aformat_expr}[a{i}]")

    # Compose the concat inputs — interleaved [vN][aN] pairs.
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n_segments))
    parts.append(f"{concat_inputs}concat=n={n_segments}:v=1:a=1[outv][outa]")
    return ";".join(parts)


def build_ffmpeg_command(
    spec: MotionCompositeSpec, ffmpeg_binary: str, segments: list[Path]
) -> list[str]:
    """Compose full argv for a concat render.

    Kept as a pure function so tests can assert command shape without
    invoking ffmpeg.
    """
    cmd: list[str] = [ffmpeg_binary, "-y"]
    for seg in segments:
        cmd.extend(["-i", str(seg)])

    filtergraph = build_concat_filtergraph(len(segments), spec.target_width, spec.target_height)
    cmd.extend(
        [
            "-filter_complex",
            filtergraph,
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            # CLAUDE.md compliance — bt709 color space is non-negotiable
            # for web delivery. Match FrameCompositor's enforcement.
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            spec.preset,
            "-crf",
            str(spec.crf),
            "-c:a",
            "aac",
            "-b:a",
            spec.audio_bitrate,
            # Match FrameCompositor's audio channel + sample rate
            "-ac",
            "2",
            "-ar",
            "48000",
            str(spec.output_path),
        ]
    )
    return cmd


def _collect_segments(spec: MotionCompositeSpec) -> list[Path]:
    """Build the ordered list of segments for concat.

    Skips None entries (intro or outro not designed for this niche).
    Source is always the middle segment.
    """
    segments: list[Path] = []
    if spec.intro_path is not None:
        segments.append(spec.intro_path)
    segments.append(spec.source_video_path)
    if spec.outro_path is not None:
        segments.append(spec.outro_path)
    return segments


def composite_motion_graphics(
    spec: MotionCompositeSpec,
    *,
    timeout_seconds: int = 300,
) -> bool:
    """Execute the intro+source+outro concat render.

    Returns True on success, False on:
      * Source video missing
      * Both intro and outro missing (would produce a no-op copy)
      * Referenced intro/outro file missing (asset removed after selection)
      * FFmpeg unavailable / timeout / nonzero exit
      * Output validation failure (missing or tiny file)

    Special case — no assets: when both intro_path and outro_path are
    None, this is a no-op. The function returns False rather than
    doing a wasteful re-encode; the caller falls back to using the
    source path as the composite path.
    """
    if not spec.source_video_path.exists():
        logger.warning(
            "[motion_compositor] source video missing: %s",
            spec.source_video_path,
        )
        return False

    if spec.intro_path is None and spec.outro_path is None:
        # Task #631 (2026-07-09 late evening): elevated DEBUG → INFO.
        # Not a bug per se — the compositor genuinely has nothing to
        # do when both assets are missing/unresolved. But at DEBUG this
        # silently disappeared; at INFO an operator scanning journalctl
        # can spot the pattern "every render skips motion" and cross-
        # reference with the asset-not-found warnings above.
        logger.info(
            "[motion_compositor] both intro and outro None — skipping "
            "compositor (caller uses source unchanged; no arm attribution "
            "for intro/outro dimensions)"
        )
        return False

    segments = _collect_segments(spec)
    # Verify all referenced assets exist
    for seg in segments:
        if not seg.exists():
            logger.warning("[motion_compositor] segment missing: %s", seg)
            return False

    from genlab_core.media.ffmpeg import get_ffmpeg_binary

    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError as exc:
        logger.warning("[motion_compositor] ffmpeg not available: %s", exc)
        return False

    cmd = build_ffmpeg_command(spec, ffmpeg, segments)
    logger.info(
        "[motion_compositor] compositing %d segments -> %s (intro=%s outro=%s)",
        len(segments),
        spec.output_path,
        spec.intro_path.name if spec.intro_path else "-",
        spec.outro_path.name if spec.outro_path else "-",
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[motion_compositor] ffmpeg timed out after %ds: %s",
            timeout_seconds,
            spec.output_path,
        )
        return False
    except (OSError, FileNotFoundError) as exc:
        logger.warning("[motion_compositor] ffmpeg subprocess failed: %s", exc)
        return False

    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        logger.warning(
            "[motion_compositor] ffmpeg exit=%d for %s. Last stderr:\n%s",
            result.returncode,
            spec.output_path,
            stderr_tail,
        )
        return False

    if not spec.output_path.exists() or spec.output_path.stat().st_size < 1024:
        logger.warning(
            "[motion_compositor] output missing/too small: %s",
            spec.output_path,
        )
        return False

    return True


@dataclass
class MotionAssetChoice:
    """Bandit-picked intro + outro template names + niche asset dirs.

    Constructed from TransformationChoices + config.dimensions in the
    render pipeline. Passed to composite_for_reel().
    """

    niche_root: Path
    intro_template: str = ""
    intro_asset_dir: str = "assets/motion/intros"
    outro_template: str = ""
    outro_asset_dir: str = "assets/motion/outros"

    def resolve_intro(self) -> Path | None:
        return _resolve_asset_path(
            self.niche_root,
            "intro_animation",
            self.intro_asset_dir,
            self.intro_template,
        )

    def resolve_outro(self) -> Path | None:
        return _resolve_asset_path(
            self.niche_root,
            "outro_cta",
            self.outro_asset_dir,
            self.outro_template,
        )


def composite_for_reel(
    choice: MotionAssetChoice,
    source_video_path: Path,
    output_path: Path,
    *,
    target_width: int = 1080,
    target_height: int = 1920,
    timeout_seconds: int = 300,
) -> bool:
    """High-level entry point matching audio_replacer.replace_audio_for_reel.

    Returns False on any of: source missing, both assets absent,
    referenced asset file missing, ffmpeg failure. Caller falls back
    to using source_video_path unchanged.
    """
    spec = MotionCompositeSpec(
        source_video_path=source_video_path,
        output_path=output_path,
        intro_path=choice.resolve_intro(),
        outro_path=choice.resolve_outro(),
        target_width=target_width,
        target_height=target_height,
    )
    return composite_motion_graphics(spec, timeout_seconds=timeout_seconds)


__all__ = [
    "MotionAssetChoice",
    "MotionCompositeSpec",
    "build_concat_filtergraph",
    "build_ffmpeg_command",
    "composite_for_reel",
    "composite_motion_graphics",
]
