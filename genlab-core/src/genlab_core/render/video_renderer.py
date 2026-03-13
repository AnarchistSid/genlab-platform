"""Shared video renderer for all Gen Lab channels.

All niche renderers delegate FFmpeg encoding to this module.
Niche-specific values (logo, color, handle) come in via NicheVisualConfig.
FFmpeg parameters and layout standards come from genlab_core.video.standards.

Usage::

    from genlab_core.render import VideoRenderer, NicheVisualConfig

    config = NicheVisualConfig(
        niche_id="ai_news",
        channel_name="Blackbox Brief",
        channel_handle="@blackboxbrief",
        accent_color="#3B82F6",
        logo_path="/path/to/logo.png",
    )
    renderer = VideoRenderer(config)
    renderer.render("source.mp4", "Hook text", "output.mp4", platform="instagram")
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from genlab_core.video.standards import Platform, get_standard

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NicheVisualConfig:
    """The only niche-specific values the renderer needs.

    Everything else (frame size, bar heights, encoding params) comes from
    ``genlab_core.video.standards``.
    """

    niche_id: str
    channel_name: str
    channel_handle: str
    accent_color: str
    logo_path: str


# Map string platform names to Platform enum values
_PLATFORM_MAP: dict[str, Platform] = {
    "youtube": Platform.YOUTUBE,
    "instagram": Platform.INSTAGRAM,
    "tiktok": Platform.TIKTOK,
    "facebook": Platform.FACEBOOK,
    "threads": Platform.THREADS,
    "x_standard": Platform.X_STANDARD,
    "x_premium": Platform.X_PREMIUM,
    "twitter": Platform.X_STANDARD,
}


class VideoRenderer:
    """Renders a 9:16 vertical video reel from a source clip + blueprint data.

    Layout (from ``genlab_core.video.standards.LayoutStandard``):
      - Top bar    (12%): channel logo + name + handle
      - Hook zone  (8%):  hook text overlay
      - Video area (62%): source clip, centre-cropped / blurred pillarbox
      - Bottom bar (18%): CTA / empty

    This class is ONLY responsible for:
      1. Composing the FFmpeg filter graph
      2. Executing the encode
      3. Returning the output path

    It does NOT download clips, upload to platforms, or make API calls.
    """

    def __init__(self, config: NicheVisualConfig) -> None:
        self._config = config

    def render(
        self,
        source_clip_path: str,
        hook_text: str,
        output_path: str,
        platform: str = "instagram",
        dry_run: bool = False,
    ) -> str:
        """Render source_clip into a branded vertical reel.

        Args:
            source_clip_path: Local path to source video.
            hook_text: Hook text to overlay in the hook zone.
            output_path: Where to write the output .mp4.
            platform: Target platform (determines codec/crf/preset).
            dry_run: Validate inputs but skip FFmpeg execution.

        Returns:
            Absolute path to rendered video.

        Raises:
            FileNotFoundError: source_clip_path does not exist.
            RuntimeError: FFmpeg returned non-zero exit code.
        """
        if not os.path.exists(source_clip_path):
            raise FileNotFoundError(f"Source clip not found: {source_clip_path}")

        platform_enum = _PLATFORM_MAP.get(platform.lower(), Platform.INSTAGRAM)
        standard = get_standard(platform_enum)
        frame_w = standard.frame.width
        frame_h = standard.frame.height
        layout = standard.layout

        top_bar_h = int(frame_h * layout.top_bar_pct) & ~1
        bottom_bar_h = int(frame_h * layout.bottom_bar_pct) & ~1
        hook_h = int(frame_h * 0.08) & ~1
        video_h = (frame_h - top_bar_h - bottom_bar_h - hook_h) & ~1
        video_y = top_bar_h + hook_h

        safe_hook = _escape_drawtext(hook_text)
        safe_name = _escape_drawtext(self._config.channel_name)
        safe_handle = _escape_drawtext(self._config.channel_handle)

        filter_graph = (
            # Scale source to fill video area
            f"[0:v]scale={frame_w}:{video_h}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={frame_w}:{video_h}[main];"
            # Black canvas
            f"color=black:{frame_w}x{frame_h}:rate={standard.frame.fps}[canvas];"
            # Overlay video in centre zone
            f"[canvas][main]overlay=0:{video_y}[with_video];"
            # Accent strip in top bar
            f"[with_video]"
            f"drawbox=x=0:y=0:w={frame_w}:h={top_bar_h}:"
            f"color={self._config.accent_color}@0.15:t=fill[with_topbar];"
            # Channel name
            f"[with_topbar]"
            f"drawtext=text='{safe_name}':"
            f"fontsize=22:fontcolor=white:x=80:y={top_bar_h // 2 - 14}"
            f"[with_name];"
            # Channel handle
            f"[with_name]"
            f"drawtext=text='{safe_handle}':"
            f"fontsize=16:fontcolor=white@0.6:x=80:y={top_bar_h // 2 + 4}"
            f"[with_handle];"
            # Hook text
            f"[with_handle]"
            f"drawtext=text='{safe_hook}':"
            f"fontsize={layout.hook_font_size}:fontcolor=white:"
            f"x=32:y={top_bar_h + hook_h // 2 - layout.hook_font_size // 2}"
            f"[final]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", source_clip_path,
            "-filter_complex", filter_graph,
            "-map", "[final]",
            "-map", "0:a?",
            "-c:v", standard.video.codec,
            "-crf", str(standard.video.crf),
            "-preset", standard.video.preset,
            "-c:a", "aac", "-b:a", "128k",
            "-r", str(standard.frame.fps),
            "-color_primaries", standard.video.color_space,
            "-color_trc", standard.video.color_space,
            "-colorspace", standard.video.color_space,
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.info(
            "render_start niche=%s platform=%s source=%s output=%s dry_run=%s",
            self._config.niche_id, platform, source_clip_path, output_path, dry_run,
        )

        if dry_run:
            logger.info("render_dry_run — skipping FFmpeg execution")
            return output_path

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error("ffmpeg_error stderr=%s", result.stderr[-500:])
            raise RuntimeError(f"FFmpeg failed (exit {result.returncode})")

        logger.info("render_complete niche=%s output=%s", self._config.niche_id, output_path)
        return output_path


def _escape_drawtext(text: str) -> str:
    """Escape text for use in FFmpeg drawtext filter."""
    return (
        text
        .replace("\\", "\\\\\\\\")
        .replace("'", "'\\\\\\''")
        .replace(":", "\\\\:")
        .replace("%", "%%")
    )
