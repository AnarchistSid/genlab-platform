"""chart-broll-renderer — bar chart → 1080x1920 vertical mp4.

## What it does

Renders a bar chart as a short-form video clip suitable for use as an
intro or B-roll segment in Reels, TikTok, Shorts, or Threads posts.
Pure ffmpeg drawbox + drawtext — no matplotlib, no external Python
deps, ~500ms per generation.

Input:
  * title (str): chart title displayed at the top
  * bars (list of {label, value}): 2 to 7 bars; values >0
  * accent_color_hex (optional): 6-char hex without #, default "3B82F6"
  * duration_seconds (optional): clip length, default 2.5
  * background_color_hex (optional): dark bg for readable white bars,
    default "0F172A" (slate-950)

Output:
  * mp4 file: 1080x1920 h264/AAC with bt709 color metadata
  * bars_rendered: count actually drawn (caps at 7)

## Design

* Adaptive font sizing: at ≥5 bars, labels shrink from 36→26px and
  long labels get an ellipsis truncation. Prevents overlap.
* Values formatted with K/M suffixes above 1000/1M so charts remain
  legible across 3-4 orders of magnitude (1.5, 175, 1.7K, 1.7M).
* Silent AAC 48kHz stereo audio track baked in so the output concats
  cleanly with a main reel that has audio.
* bt709 color tags forced into the H.264 SPS via `-x264-params` —
  container-level `-color_*` flags alone don't reach the codec
  metadata reliably.

Extracted from the Gen Lab video pipeline (github.com/AnarchistSid/
genlab-platform) where it powers the ai_creators data-viz intro
canary. Zero-dep single-file design so any inference.sh agent can
call it without pulling in the whole GenLab dependency tree.
"""
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup, File
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AppSetup(BaseAppSetup):
    """No setup config needed — the app is a thin ffmpeg wrapper."""
    pass


class Bar(BaseModel):
    """One bar in the chart."""
    label: str = Field(description="Short label under the bar (e.g. 'GPT-4', '2024', 'OpenAI'). Truncated with ellipsis at 5+ bars.")
    value: float = Field(description="Numeric value above the bar. Must be positive; values ≤0 are dropped.", gt=0)


class RunInput(BaseModel):
    """Inputs for chart rendering."""
    title: str = Field(
        description="Chart title displayed at the top (max ~50 chars for clean fit)",
        min_length=1,
        max_length=100,
    )
    bars: List[Bar] = Field(
        description="2-7 bars. First 7 rendered; extras silently dropped.",
        min_length=2,
        max_length=7,
    )
    accent_color_hex: str = Field(
        default="3B82F6",
        description="Bar fill color as 6-char hex without leading # (e.g. '00D4FF' for cyan, 'FF2040' for red)",
        pattern=r"^[0-9A-Fa-f]{6}$",
    )
    background_color_hex: str = Field(
        default="0F172A",
        description="Background color as 6-char hex without leading #. Default is slate-950 for high contrast with white text.",
        pattern=r"^[0-9A-Fa-f]{6}$",
    )
    duration_seconds: float = Field(
        default=2.5,
        description="Clip length in seconds (0.5 to 15.0)",
        ge=0.5,
        le=15.0,
    )


class RunOutput(BaseModel):
    """Rendered mp4 + rendering metadata."""
    video: File = Field(
        description="Rendered 1080x1920 h264/AAC mp4 with bt709 color metadata and silent stereo audio track."
    )
    bars_rendered: int = Field(
        description="Number of bars actually drawn (input capped at 7)."
    )
    duration_seconds: float = Field(
        description="Actual output duration in seconds (matches input)."
    )


# ── canvas geometry (matches 9:16 short-form video conventions) ─────
_CANVAS_W = 1080
_CANVAS_H = 1920
_PLOT_X_LEFT = 120
_PLOT_X_RIGHT = _CANVAS_W - 120
_PLOT_Y_TOP = 500
_PLOT_Y_BOTTOM = 1500
_PLOT_W = _PLOT_X_RIGHT - _PLOT_X_LEFT
_PLOT_H = _PLOT_Y_BOTTOM - _PLOT_Y_TOP
_MAX_BARS = 7


def _fmt_value(value: float) -> str:
    """Human-readable bar value across 4 orders of magnitude."""
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K"
    if abs(value) >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}"


def _escape_drawtext(text: str) -> str:
    """Escape for ffmpeg drawtext text= inside filter_complex.
    Uses U+2019 for apostrophes so they don't terminate the quoted
    string in the outer -vf parser."""
    text = text.replace("\\", "\\\\\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")
    text = text.replace(";", "\\;")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    return text


def _build_filter_graph(
    title: str, bars: List[Bar], accent_hex: str,
) -> str:
    """Compose the ffmpeg filter_complex graph.

    Adaptive layout:
      * n≥5 bars → label fontsize drops 36→26 and long labels
        get ellipsis truncation so they don't collide.
      * bar geometry recomputed per n.
    """
    max_value = max((b.value for b in bars), default=1.0)
    if max_value <= 0:
        max_value = 1.0

    n = len(bars)
    gap = 30
    bar_w = max(30, (_PLOT_W - gap * (n - 1)) // n)

    if n >= 5:
        label_fontsize = 26
        label_max_chars = max(6, (bar_w + gap) // 20)
    else:
        label_fontsize = 36
        label_max_chars = 30

    def fit_label(text: str) -> str:
        if len(text) <= label_max_chars:
            return text
        return text[: max(1, label_max_chars - 1)] + "…"

    parts: List[str] = []
    esc_title = _escape_drawtext(title)
    parts.append(
        f"drawtext=text='{esc_title}':"
        f"fontcolor=white:fontsize=60:borderw=4:bordercolor=black@0.9:"
        f"x=(w-text_w)/2:y=220"
    )

    for i, bar in enumerate(bars):
        ratio = max(0.05, min(1.0, float(bar.value) / max_value))
        bar_h = int(_PLOT_H * ratio)
        x = _PLOT_X_LEFT + i * (bar_w + gap)
        y = _PLOT_Y_BOTTOM - bar_h

        parts.append(
            f"drawbox=x={x}:y={y}:w={bar_w}:h={bar_h}:"
            f"color=0x{accent_hex}@0.9:t=fill"
        )
        esc_val = _escape_drawtext(_fmt_value(bar.value))
        parts.append(
            f"drawtext=text='{esc_val}':"
            f"fontcolor=white:fontsize=42:borderw=3:bordercolor=black@0.9:"
            f"x={x + bar_w // 2}-text_w/2:y={y - 60}"
        )
        esc_lbl = _escape_drawtext(fit_label(bar.label))
        parts.append(
            f"drawtext=text='{esc_lbl}':"
            f"fontcolor=white:fontsize={label_fontsize}:"
            f"borderw=3:bordercolor=black@0.9:"
            f"x={x + bar_w // 2}-text_w/2:y={_PLOT_Y_BOTTOM + 30}"
        )
    return ",".join(parts)


class App(BaseApp):
    async def setup(self, config: AppSetup):
        """Verify ffmpeg is available at startup, not per-request."""
        which = shutil.which("ffmpeg")
        if not which:
            raise RuntimeError(
                "ffmpeg binary not found on PATH — this app requires "
                "ffmpeg in the runtime image."
            )
        logger.info(f"chart-broll-renderer ready; ffmpeg at {which}")

    async def run(self, input_data: RunInput) -> RunOutput:
        # Cap bar count to _MAX_BARS (pydantic already enforces ≤7 but
        # this guards against schema drift and is explicit for readers).
        bars = input_data.bars[:_MAX_BARS]
        n = len(bars)

        filter_graph = _build_filter_graph(
            title=input_data.title,
            bars=bars,
            accent_hex=input_data.accent_color_hex.lstrip("#"),
        )

        out_path = "/tmp/chart_broll.mp4"

        cmd = [
            "ffmpeg", "-y",
            # Video: solid background color for the requested duration.
            "-f", "lavfi",
            "-i", (
                f"color=c=0x{input_data.background_color_hex.lstrip('#')}"
                f":s={_CANVAS_W}x{_CANVAS_H}:r=30"
                f":d={input_data.duration_seconds}"
            ),
            # Audio: silent stereo 48kHz so concat-with-audio works.
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-filter_complex", f"[0:v]{filter_graph}[vout]",
            "-map", "[vout]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            # Force bt709 into the H.264 SPS. Container-level -color_*
            # flags alone don't reach codec metadata reliably.
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-c:a", "aac",
            "-b:a", "192k",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
            "-t", str(input_data.duration_seconds),
            "-shortest",
            "-movflags", "+faststart",
            out_path,
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg render failed (exit={result.returncode}): "
                f"{result.stderr[-500:]}"
            )
        if not Path(out_path).exists():
            raise RuntimeError("ffmpeg reported success but no output file")

        return RunOutput(
            video=File(path=out_path),
            bars_rendered=n,
            duration_seconds=input_data.duration_seconds,
        )
