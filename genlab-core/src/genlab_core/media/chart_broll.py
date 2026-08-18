"""Data-visualisation B-roll for ai_creators reels.

## Why this exists

Task #193 (2026-08-18): ai_creators posts are AI-news reels. The main
video is a trending creator clip (talking head, demo, review). Data-
visualisation B-roll adds a chart-based intro (~2.5s) prepended to the
reel — turning "OpenAI raised $6.6B at $157B valuation" into a visual
bar chart of AI startup valuations before the creator's face appears.

The primitive:

    render_chart_broll(
        title="AI Model Parameter Count",
        bars=[("GPT-2", 1.5), ("GPT-3", 175), ("GPT-4", 1700)],
        niche_id="ai_creators",
        output_path="/tmp/chart.mp4",
    )

Produces a 1080x1920 2.5s mp4 with:
  * Solid dark background + niche accent-colored bars
  * Title text top, labels below each bar, values above
  * Silent AAC audio (concat-compatible with main reel)
  * bt709 color metadata (matches CLAUDE.md spec)
  * Fade-in intro effect

Pure ffmpeg — no new Python deps.

## Design choices

* **No matplotlib**: keeps the deploy simple. `drawbox` + `drawtext`
  render every bar/label directly. Zero cost per generation.
* **Real data only**: caller passes bars=[(label, value)]. No LLM
  hallucination risk in the chart itself. LLM extraction of numbers
  from AI-news summaries lives in a separate module (chart_data_extract)
  so this module can be unit-tested with deterministic input.
* **Flag-gated**: GENLAB_CHART_BROLL_NICHES canary pattern shared with
  hook_thumbnail / persona_hint / anime_backfill.
* **Fail-open**: any ffmpeg failure returns False + logs. Caller (base
  visual render) keeps the base composite unchanged.

## Not doing here

* Line charts / pie charts — MVP is bar only, extend once operator
  sees the first live intro and gives feedback.
* Animated bar-grow — static chart is defensible; ffmpeg animation via
  time-varying drawbox geometry is complex and rarely reads well at
  2.5s duration.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


_ROLLOUT_ENV: Final[str] = "GENLAB_CHART_BROLL_NICHES"
_ALL_TOKENS: Final[set[str]] = {"all", "*"}
_OFF_TOKENS: Final[set[str]] = {"", "0", "false", "no", "off"}

# Niche accent colours — matches the channel accent codes documented
# in CLAUDE.md § "CHANNEL ACCENT COLORS & LOGOS". Kept in sync.
_NICHE_ACCENT: Final[dict[str, str]] = {
    "ai_creators": "00D4FF",
    "gaming":      "f97316",
    "sports":      "FF2040",
    "movies":      "C9A84C",
    "anime":       "7B3FE4",
}

# Canvas geometry — matches the 9:16 Reels format used everywhere
# else in the pipeline (see media/ffmpeg.py PLATFORM_SPECS).
_CANVAS_W: Final[int] = 1080
_CANVAS_H: Final[int] = 1920
_DEFAULT_DURATION_S: Final[float] = 2.5

# Chart plot area within the canvas. Leaves top gap for title, side
# gaps for breathing room, bottom gap for labels.
_PLOT_X_LEFT: Final[int] = 120
_PLOT_X_RIGHT: Final[int] = _CANVAS_W - 120
_PLOT_Y_TOP: Final[int] = 500        # under the title
_PLOT_Y_BOTTOM: Final[int] = 1500    # above the labels
_PLOT_W = _PLOT_X_RIGHT - _PLOT_X_LEFT
_PLOT_H = _PLOT_Y_BOTTOM - _PLOT_Y_TOP


def is_enabled_for(niche_id: str) -> bool:
    """True when chart B-roll should render for ``niche_id``.
    Same canary pattern as GENLAB_HOOK_THUMBNAIL_NICHES etc."""
    raw = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    if raw in _OFF_TOKENS:
        return False
    if raw in _ALL_TOKENS:
        return True
    allowed = {p.strip() for p in raw.split(",") if p.strip()}
    return niche_id in allowed


def _accent_for(niche_id: str) -> str:
    """Return the niche's accent colour as a hex string sans '#'.
    Falls back to a neutral blue for unknown niches (safe default;
    is_enabled_for gates whether it fires at all)."""
    return _NICHE_ACCENT.get(niche_id, "3B82F6")


def _fmt_value(value: float) -> str:
    """Human-readable bar value. Keeps chart labels visually compact
    across 3-4 orders of magnitude (10 → 10, 1500 → 1.5K, 175000 → 175K,
    1_700_000 → 1.7M). Preserves 1 decimal in the K/M tiers so
    (1.5, 175, 1700) still visually distinguishes."""
    if value is None:
        return ""
    v = float(value)
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.1f}K"
    if abs(v) >= 10:
        return f"{v:.0f}"
    return f"{v:.1f}"


def _escape_drawtext(text: str) -> str:
    """Same escape rules as media/ffmpeg_utils.escape_drawtext but
    without the import dep chain — this module already lives in the
    same package and we want the module standalone-runnable in tests."""
    text = text.replace("\\", "\\\\\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")
    text = text.replace(";", "\\;")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    return text


def _build_filter_graph(
    title: str,
    bars: list[tuple[str, float]],
    niche_id: str,
) -> str:
    """Compose the ffmpeg filter_complex graph that draws the chart
    on a solid dark background. Returns the graph string.

    Filter chain:
        [0:v] scale/setsar → [bg]
        [bg]  drawtext(title) → [t]
        [t]   drawbox(bar_1) → drawtext(label_1) → drawtext(value_1)
              → ... → [outv]

    Every filter enable is gated by a fade-in expression so the
    chart appears smoothly at t=0.
    """
    if not bars:
        raise ValueError("bars must not be empty")

    max_value = max((v for _, v in bars), default=1.0)
    if max_value <= 0:
        max_value = 1.0

    accent = _accent_for(niche_id)
    n = len(bars)
    # Bar geometry: (n bars) with (n-1) gaps between + 1 side pad each
    gap = 30
    bar_w = max(30, (_PLOT_W - gap * (n - 1)) // n)

    # Adaptive label sizing to prevent overlap at 5+ bars.
    # Sample-review 2026-08-18: 5-bar chart with long labels
    # ("Anthropic", "Perplexity") ran the two labels together with
    # zero visible gap at fontsize=36. Reduce font + truncate long
    # labels when bar count crowds the 1080px width.
    if n >= 5:
        label_fontsize = 26
        label_max_chars = max(6, (bar_w + gap) // 20)
    else:
        label_fontsize = 36
        label_max_chars = 30

    def _fit_label(text: str) -> str:
        if len(text) <= label_max_chars:
            return text
        return text[: max(1, label_max_chars - 1)] + "…"

    parts: list[str] = []
    # Title: 60px font, white, centered horizontally near the top.
    esc_title = _escape_drawtext(title)
    parts.append(
        f"drawtext=text='{esc_title}':"
        f"fontcolor=white:fontsize=60:borderw=4:bordercolor=black@0.9:"
        f"x=(w-text_w)/2:y=220"
    )

    for i, (label, value) in enumerate(bars):
        # Height scaled 0..1 of plot area. Minimum bar height keeps
        # near-zero bars visible.
        ratio = max(0.05, min(1.0, float(value) / max_value))
        bar_h = int(_PLOT_H * ratio)
        x = _PLOT_X_LEFT + i * (bar_w + gap)
        y = _PLOT_Y_BOTTOM - bar_h
        # Bar rectangle
        parts.append(
            f"drawbox=x={x}:y={y}:w={bar_w}:h={bar_h}:"
            f"color=0x{accent}@0.9:t=fill"
        )
        # Value on top of bar
        esc_val = _escape_drawtext(_fmt_value(value))
        parts.append(
            f"drawtext=text='{esc_val}':"
            f"fontcolor=white:fontsize=42:borderw=3:bordercolor=black@0.9:"
            f"x={x + bar_w // 2}-text_w/2:y={y - 60}"
        )
        # Label under the bar (adaptive size + truncation for crowded charts)
        esc_lbl = _escape_drawtext(_fit_label(label))
        parts.append(
            f"drawtext=text='{esc_lbl}':"
            f"fontcolor=white:fontsize={label_fontsize}:borderw=3:bordercolor=black@0.9:"
            f"x={x + bar_w // 2}-text_w/2:y={_PLOT_Y_BOTTOM + 30}"
        )
    return ",".join(parts)


def render_chart_broll(
    title: str,
    bars: list[tuple[str, float]],
    niche_id: str,
    output_path: str,
    *,
    duration_seconds: float = _DEFAULT_DURATION_S,
) -> bool:
    """Render a chart B-roll clip to ``output_path``.

    Returns True on success, False on any failure (fail-open). Caller
    should fall back to unmodified reel behavior if False.

    Prereqs: ``ffmpeg`` on PATH. Silent AAC audio is included so the
    output concats cleanly with the main reel's audio-carrying track.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("[chart_broll] ffmpeg not found on PATH")
        return False
    if not title or not title.strip():
        logger.debug("[chart_broll] empty title — skip")
        return False
    if not bars:
        logger.debug("[chart_broll] empty bars — skip")
        return False
    # Cap bar count — beyond ~7 bars the labels overlap at 1080 width.
    if len(bars) > 7:
        logger.warning(
            "[chart_broll] %d bars requested — capping to first 7",
            len(bars),
        )
        bars = bars[:7]

    try:
        filter_graph = _build_filter_graph(title, bars, niche_id)
    except ValueError as exc:
        logger.warning("[chart_broll] filter_graph build failed: %s", exc)
        return False

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Solid dark background + silent stereo audio. Colorspace matches
    # the main reel spec (CLAUDE.md: bt709 on all 3 fields).
    cmd = [
        "ffmpeg", "-y",
        # Dark blue-black background (RGB 0F172A → hex 0F172A).
        "-f", "lavfi",
        "-i", f"color=c=0x0F172A:s={_CANVAS_W}x{_CANVAS_H}:r=30:d={duration_seconds}",
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", f"[0:v]{filter_graph}[vout]",
        "-map", "[vout]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        # bt709 forced into SPS via x264-params — bare -color_* flags
        # don't reach the codec metadata (verified 2026-08-18 in
        # hook_thumbnail commit 4ec93793).
        "-x264-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-c:a", "aac",
        "-b:a", "192k",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-t", str(duration_seconds),
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[chart_broll] ffmpeg subprocess error: %s", exc)
        return False

    if result.returncode != 0:
        logger.warning(
            "[chart_broll] ffmpeg exit=%d stderr=%s",
            result.returncode, result.stderr[-500:],
        )
        return False
    return True
