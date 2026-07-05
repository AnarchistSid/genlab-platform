"""Animated caption overlay — word-by-word reveal via FFmpeg drawtext.

Consumer of three transformation dimensions:
  * ``caption_style``           — reveal pattern (word_by_word, hormozi_yellow, ...)
  * ``caption_emphasis_color``  — color for emphasis words
  * ``caption_pacing``          — ms per word chunk

Reads structured segments from PR 10's ``CaptionSegmentsResult`` and
produces a filter graph that burns per-word drawtext filters onto the
source video with time-based ``enable='between(t,start,end)'`` gates.

Style variants:

  * ``word_by_word`` — words reveal sequentially within a segment,
    each word stays visible until the segment ends. Emphasis words
    render in the picked emphasis color, others in white.
  * ``hormozi_yellow`` — same reveal mechanic as word_by_word, but
    ALL emphasis words render yellow regardless of the emphasis-color
    arm's choice. Named after the Alex Hormozi caption aesthetic.
  * ``karaoke`` — one word at a time; previous word disappears when
    next appears. Less common; supports niches where minimal on-screen
    text is preferred.
  * ``minimal`` — entire segment appears at once, holds through the
    segment's duration. No per-word timing.
  * ``meme`` — larger fontsize (72 vs default 56), heavier stroke.
    Best for gaming/anime where high-contrast text is expected.

Position: center-horizontal, y = h*0.72 (lower third but above the
Instagram username overlay strip at y ≈ h*0.85).

Font: reuses ``FrameCompositor._resolve_fonts`` — Inter Variable
when available, Arial/DejaVu fallback.

Related sprint context:
- PR 10 (#674) — writer produces the CaptionSegmentsResult this reads
- PR 4 (#668) — selector picks style + emphasis_color + pacing
- PR 6 (#670) — caption_style / caption_pacing arm rewards derived
  from hold_15s + hold_3s retention proxies
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from genlab_core.writing.caption_segments import CaptionSegment

logger = logging.getLogger(__name__)


# Emphasis color arm values → FFmpeg color names (drawtext-compatible).
_EMPHASIS_COLOR_MAP: dict[str, str] = {
    "yellow": "#FFEB3B",
    "red": "#FF5252",
    "cyan": "#00E5FF",
    "gradient": "#FFEB3B",  # true gradient needs multi-drawtext trickery;
    # PR 11 approximates with a warm accent. Future PR can wire a
    # multi-color-stop path.
    "none": "white",  # emphasis_color=none means no color pop
}

_DEFAULT_FONTSIZE = 56
_MEME_FONTSIZE = 72
_STROKE_WIDTH = 4
_STROKE_COLOR = "black"


@dataclass
class CaptionAnimationSpec:
    """Parameters for one caption-overlay render."""

    source_video_path: Path
    output_path: Path
    segments: list[CaptionSegment]
    style: str = "word_by_word"
    emphasis_color: str = "yellow"
    pacing_ms: int = 750
    video_duration_s: float = 20.0
    font_path: str = ""  # resolved via _resolve_font() when empty
    target_width: int = 1080
    target_height: int = 1920
    crf: int = 20
    preset: str = "fast"


def _resolve_font() -> str:
    """Resolve font path — reuses FrameCompositor's pattern."""
    from genlab_core.media.frame_compositor import FrameCompositor

    bold, _, _ = FrameCompositor._resolve_fonts()
    return bold


def _escape_drawtext(text: str) -> str:
    """Escape single quotes + colon + backslash for FFmpeg drawtext."""
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
    )


def _emphasis_color_for(
    emphasis_arm: str, style: str
) -> str:
    """Which color to render emphasis words in.

    Hormozi style always yellow (its whole identity). Other styles
    honor the emphasis_color bandit arm.
    """
    if style == "hormozi_yellow":
        return _EMPHASIS_COLOR_MAP["yellow"]
    return _EMPHASIS_COLOR_MAP.get(emphasis_arm, "#FFEB3B")


def _fontsize_for(style: str) -> int:
    return _MEME_FONTSIZE if style == "meme" else _DEFAULT_FONTSIZE


def _segment_time_ranges(
    n_segments: int, video_duration_s: float
) -> list[tuple[float, float]]:
    """Divide the video duration into equal-length segments.

    A 20s video with 3 segments gets ranges [(0, 6.67), (6.67, 13.33),
    (13.33, 20)]. Simple partitioning — no cross-segment gaps.
    """
    if n_segments <= 0 or video_duration_s <= 0:
        return []
    span = video_duration_s / n_segments
    return [(i * span, (i + 1) * span) for i in range(n_segments)]


def _word_is_emphasis(
    word: str, emphasis_words: list[str]
) -> bool:
    """Case-insensitive + punctuation-tolerant emphasis match."""
    core = word.lower().strip(",.!?;:")
    for e in emphasis_words:
        if e.lower().strip(",.!?;:") == core:
            return True
    return False


def _drawtext_for_word(
    word: str,
    start_s: float,
    end_s: float,
    y_expr: str,
    fontsize: int,
    color: str,
    font_path: str,
) -> str:
    """Produce one drawtext filter clause for a single word."""
    escaped = _escape_drawtext(word)
    return (
        f"drawtext=fontfile='{font_path}':text='{escaped}':"
        f"fontsize={fontsize}:fontcolor={color}:"
        f"bordercolor={_STROKE_COLOR}:borderw={_STROKE_WIDTH}:"
        f"x=(w-text_w)/2:y={y_expr}:"
        f"enable='between(t,{start_s:.3f},{end_s:.3f})'"
    )


def build_caption_filter_chain(
    segments: list[CaptionSegment],
    style: str,
    emphasis_color: str,
    pacing_ms: int,
    video_duration_s: float,
    font_path: str,
    target_height: int = 1920,
) -> str:
    """Build the ``-vf`` filter chain for animated captions.

    Returns "" (empty string) for degenerate inputs — no segments,
    non-positive duration, or unknown style. Caller checks and skips
    the render.
    """
    if not segments or video_duration_s <= 0:
        return ""
    if style not in {
        "word_by_word", "hormozi_yellow", "karaoke", "minimal", "meme"
    }:
        logger.warning(
            "[caption_animator] unknown style %r — skipping", style
        )
        return ""

    fontsize = _fontsize_for(style)
    emphasis_hex = _emphasis_color_for(emphasis_color, style)
    # y position: lower third but above the Instagram username strip.
    # Using ``h*0.72`` keeps consistent placement across dimensions.
    y_expr = f"h*0.72"

    time_ranges = _segment_time_ranges(len(segments), video_duration_s)
    filters: list[str] = []

    pacing_s = max(0.1, pacing_ms / 1000.0)

    for seg, (seg_start, seg_end) in zip(segments, time_ranges):
        words = seg.text.split()
        if not words:
            continue

        if style == "minimal":
            # Single drawtext with the whole segment text, no per-word
            # timing. Emphasis words fold into the segment text with
            # the default color (no split-color rendering in minimal).
            escaped = _escape_drawtext(seg.text)
            filters.append(
                f"drawtext=fontfile='{font_path}':text='{escaped}':"
                f"fontsize={fontsize}:fontcolor=white:"
                f"bordercolor={_STROKE_COLOR}:borderw={_STROKE_WIDTH}:"
                f"x=(w-text_w)/2:y={y_expr}:"
                f"enable='between(t,{seg_start:.3f},{seg_end:.3f})'"
            )
            continue

        # Per-word reveal for the other 4 styles.
        for i, word in enumerate(words):
            word_start = seg_start + i * pacing_s
            if word_start >= seg_end:
                # Segment slot exhausted — stop revealing more words.
                break
            # word_by_word/hormozi_yellow/meme: word stays until seg_end
            # karaoke: word disappears when next word appears
            if style == "karaoke" and i < len(words) - 1:
                word_end = seg_start + (i + 1) * pacing_s
            else:
                word_end = seg_end
            word_end = min(word_end, seg_end)

            color = (
                emphasis_hex
                if _word_is_emphasis(word, seg.emphasis_words)
                else "white"
            )
            filters.append(
                _drawtext_for_word(
                    word, word_start, word_end, y_expr,
                    fontsize, color, font_path,
                )
            )

    return ",".join(filters)


def build_ffmpeg_command(
    spec: CaptionAnimationSpec, ffmpeg_binary: str, filter_str: str
) -> list[str]:
    """Compose full argv for a caption-overlay render.

    Pure function — no subprocess invocation.
    """
    return [
        ffmpeg_binary,
        "-y",
        "-i", str(spec.source_video_path),
        "-vf", filter_str,
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", spec.preset,
        "-crf", str(spec.crf),
        "-c:a", "copy",  # audio unchanged
        str(spec.output_path),
    ]


def apply_captions(
    spec: CaptionAnimationSpec,
    *,
    timeout_seconds: int = 300,
) -> bool:
    """Render captions onto the source video. Returns True on success.

    Fail-open on: no segments, unknown style, missing source, missing
    ffmpeg, timeout, nonzero exit, tiny output. Never raises.
    """
    if not spec.font_path:
        spec.font_path = _resolve_font()

    filter_str = build_caption_filter_chain(
        spec.segments,
        spec.style,
        spec.emphasis_color,
        spec.pacing_ms,
        spec.video_duration_s,
        spec.font_path,
        target_height=spec.target_height,
    )
    if not filter_str:
        logger.debug(
            "[caption_animator] empty filter chain — skipping "
            "(style=%s segments=%d duration=%.1fs)",
            spec.style, len(spec.segments), spec.video_duration_s,
        )
        return False

    if not spec.source_video_path.exists():
        logger.warning(
            "[caption_animator] source video missing: %s",
            spec.source_video_path,
        )
        return False

    from genlab_core.media.ffmpeg import get_ffmpeg_binary

    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError as exc:
        logger.warning("[caption_animator] ffmpeg not available: %s", exc)
        return False

    cmd = build_ffmpeg_command(spec, ffmpeg, filter_str)
    logger.info(
        "[caption_animator] applying %d segments (style=%s) -> %s",
        len(spec.segments), spec.style, spec.output_path,
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
            "[caption_animator] ffmpeg timed out after %ds: %s",
            timeout_seconds, spec.output_path,
        )
        return False
    except (OSError, FileNotFoundError) as exc:
        logger.warning(
            "[caption_animator] ffmpeg subprocess failed: %s", exc
        )
        return False

    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        logger.warning(
            "[caption_animator] ffmpeg exit=%d for %s. Last stderr:\n%s",
            result.returncode, spec.output_path, stderr_tail,
        )
        return False

    if not spec.output_path.exists() or spec.output_path.stat().st_size < 1024:
        logger.warning(
            "[caption_animator] output missing/too small: %s",
            spec.output_path,
        )
        return False

    return True


__all__ = [
    "CaptionAnimationSpec",
    "apply_captions",
    "build_caption_filter_chain",
    "build_ffmpeg_command",
]
