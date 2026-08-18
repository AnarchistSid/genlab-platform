"""AI-generated branded first-frame thumbnails for IG / FB reels.

## Why this exists

The 2026-08-15 growth-flywheel diagnostic found IG/FB feed engagement
is thumbnail-driven for small-audience accounts (10-165 followers).
Current pipeline uses whatever mid-action frame happens to be first
in the source clip — often visually unreadable in a fast scroll.

This module generates a **branded background image** via inference.sh
image-gen (pruna/flux-dev at $0.005/image = $0.75/month for 5 posts/day)
then overlays the hook text via ffmpeg drawtext (same proven pattern
already used for on-video hook overlays — Flux can't render readable
text so we separate concerns).

Output: 1080x1920 mp4 loop suitable for prepending as ~0.5-1s intro
to the reel via ffmpeg concat.

## Design

  * Flag-gated per niche via GENLAB_HOOK_THUMBNAIL_NICHES (canary
    pattern shared with persona_hint / cross_channel_footer /
    ig_discovery_hashtags / etc).
  * Fail-open: any belt error, ffmpeg error, or missing binary
    returns None. Caller falls back to legacy behavior (no intro).
  * Deterministic prompt seed: same hook → same background (test
    reproducibility, and idempotent re-renders on retry).
  * Cost telemetry logged via belt_client.task_cost_usd.

## Not doing here (deferred)

  * Full pipeline wire (new stage in render flow) — this module is
    a helper that a future pipeline stage can call.
  * Bandit-driven prompt selection — cold-start uniform for now.
  * Reference-image conditioning (channel logo as init image).
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


_ROLLOUT_ENV: Final[str] = "GENLAB_HOOK_THUMBNAIL_NICHES"
_ALL_TOKENS: Final[set[str]] = {"all", "*"}
_OFF_TOKENS: Final[set[str]] = {"", "0", "false", "no", "off"}

# 2026-08-18 (task #202): brand stinger for the intro. Pre-generated
# once via `belt app run elevenlabs/sound-effects` and shipped as a
# tracked asset (see genlab-core/assets/audio/brand_stinger.mp3, 0.6s
# 44.1kHz stereo, generated cost $0.0013 one-time). If the file is
# missing at runtime the intro falls back to pure silence — same
# behavior as before this stinger existed. Zero regression risk.
#
# 2026-08-18 (task #205): per-niche stinger flavors added alongside
# the universal fallback. Each niche gets a mood-appropriate SFX
# (tech swoosh for ai_creators, esports drop for gaming, etc.).
# _stinger_for_niche picks the niche file if present, falls back to
# the universal brand_stinger.mp3, then to pure lavfi silence.
_ASSETS_AUDIO_DIR: Final[Path] = (
    Path(__file__).resolve().parents[3] / "assets" / "audio"
)
_BRAND_STINGER_PATH: Final[Path] = _ASSETS_AUDIO_DIR / "brand_stinger.mp3"


def _stinger_for_niche(niche_id: str) -> Path | None:
    """Return the best stinger asset for this niche, or None when
    nothing is present. Preference order:
      1. brand_stinger_{niche_id}.mp3 (niche-flavored)
      2. brand_stinger.mp3 (universal fallback)
      3. None (caller falls back to pure silence)
    """
    niche_path = _ASSETS_AUDIO_DIR / f"brand_stinger_{niche_id}.mp3"
    if niche_path.is_file():
        return niche_path
    if _BRAND_STINGER_PATH.is_file():
        return _BRAND_STINGER_PATH
    return None

# App choice: pruna/flux-dev is $0.005/image, 100x cheaper than
# infsh/flux-1-dev ($0.50). Confirmed 2026-08-18 via belt task cost.
_IMAGE_APP: Final[str] = "pruna/flux-dev"

# Per-niche background prompt seeds. Flux cannot render readable text
# reliably; keep prompts as VISUAL AESTHETIC only — the hook text is
# overlaid separately via ffmpeg drawtext.
_NICHE_PROMPT_SEEDS: Final[dict[str, str]] = {
    "ai_creators": (
        "cinematic dark futuristic tech aesthetic, glowing blue and orange "
        "neural network patterns, sharp focus, dramatic rim lighting, "
        "9:16 vertical composition, no text, no words, clean bottom half "
        "for text overlay"
    ),
    "gaming": (
        "high-contrast esports arena background, dramatic stage lighting, "
        "purple and orange gradient sky, blurred crowd bokeh, action energy, "
        "9:16 vertical composition, no text, clean bottom half for overlay"
    ),
    "sports": (
        "dynamic sports stadium at golden hour, dramatic light rays, "
        "blurred crowd energy in background, high contrast, motion feel, "
        "9:16 vertical composition, no text, clean bottom half for overlay"
    ),
    "movies": (
        "cinematic movie theater aesthetic, dramatic red curtain lighting, "
        "film noir shadows, letterbox mood, moody atmospheric, "
        "9:16 vertical composition, no text, clean bottom half for overlay"
    ),
    "anime": (
        "vibrant anime sky background with dynamic speed lines and lens "
        "flare, saturated purples and pinks, cel-shaded aesthetic, "
        "9:16 vertical composition, no text, clean bottom half for overlay"
    ),
}


def is_enabled_for(niche_id: str) -> bool:
    """True when hook-thumbnail should be generated for ``niche_id``."""
    raw = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    if raw in _OFF_TOKENS:
        return False
    if raw in _ALL_TOKENS:
        return True
    allowed = {p.strip() for p in raw.split(",") if p.strip()}
    return niche_id in allowed


def _deterministic_seed(hook: str, niche_id: str) -> int:
    """Same (hook, niche) → same seed → same image. Enables cache-
    friendly re-render behavior + reproducible tests."""
    h = hashlib.sha256(f"{niche_id}::{hook}".encode("utf-8")).digest()
    # Flux accepts uint32-ish seeds; take 4 bytes → int
    return int.from_bytes(h[:4], "big")


def _download(url: str, dest: str) -> bool:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GenLab/1.0 hook_thumbnail"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp, \
                open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hook_thumbnail] download failed: %s", exc)
        return False


def _overlay_text_and_pad(
    background_path: str,
    hook: str,
    output_path: str,
    duration_seconds: float = 0.8,
    niche_id: str = "",
) -> bool:
    """Composite: (1) scale background to 1080x1920 with cover crop,
    (2) draw hook text bottom-third with readable stroke, (3) hold
    for N seconds as an mp4 intro.

    Uses standard ffmpeg drawtext — same escape/positioning as
    genlab_core.media.frame_compositor patterns."""
    if not shutil.which("ffmpeg"):
        logger.warning("[hook_thumbnail] ffmpeg not found")
        return False

    # Wrap text ourselves + stack one drawtext filter per line.
    #
    # Why not `textfile=` or inline `\n` in `text=`: ffmpeg's default
    # font ships without a glyph for U+000A, so the newline renders
    # as a □ .notdef tofu AT THE LINE BREAK (verified 2026-08-18
    # on macOS + Linux runtime images). Emitting a separate drawtext
    # filter per line sidesteps newline handling entirely.
    import textwrap

    # Use `escape_drawtext` (U+2019 apostrophes) — the `_simple`
    # helper's `\'` escape terminates the `text='...'` quoted
    # string prematurely inside a comma-chained `-vf` filter
    # graph, corrupting the next filter's parser state.
    from genlab_core.media.ffmpeg_utils import escape_drawtext

    truncated = hook if len(hook) <= 60 else hook[:57] + "..."
    lines = [ln.strip() for ln in
             (textwrap.wrap(truncated, width=25) or [truncated])]

    # Layered filter graph:
    # 1. scale + crop cover to 1080x1920
    # 2. N drawtext filters, one per wrapped line, centered horizontally
    #    with y stepping down by (fontsize + line_gap). Block is
    #    anchored so its vertical center sits at 55% of frame height.
    fontsize = 54
    line_gap = 14
    line_h = fontsize + line_gap
    block_h = len(lines) * line_h
    start_y = f"h*0.55-{block_h // 2}"

    drawtext_parts: list[str] = []
    for i, line in enumerate(lines):
        escaped = escape_drawtext(line)
        y_expr = f"({start_y})+{i * line_h}"
        drawtext_parts.append(
            f"drawtext=text='{escaped}':"
            f"fontcolor=white:fontsize={fontsize}:"
            f"borderw=5:bordercolor=black@0.9:"
            f"x=(w-text_w)/2:y={y_expr}"
        )

    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        + ",".join(drawtext_parts)
    )

    # Audio track: use per-niche stinger if present, then universal
    # stinger, then pure silence. Retention lever per industry
    # consensus on first-second SFX. Fail-open: missing asset =
    # silent audio, same as pre-stinger behavior.
    stinger_path = _stinger_for_niche(niche_id)
    if stinger_path is not None:
        audio_input = ["-i", str(stinger_path)]
        # apad pads the stinger with silence up to `duration_seconds`
        # so the audio track exactly matches the video duration.
        audio_filter = (
            f"[1:a]apad=whole_dur={duration_seconds},"
            "aresample=48000:async=1[aout]"
        )
        filter_complex = f"[0:v]{vf}[vout];{audio_filter}"
        audio_map = ["-map", "[aout]"]
    else:
        audio_input = [
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
        filter_complex = f"[0:v]{vf}[vout]"
        audio_map = ["-map", "1:a"]

    # Explicit `-map` because `-vf` + two inputs makes ffmpeg's auto-
    # map drop the audio stream (verified 2026-08-18: without maps,
    # ffprobe on the output shows only index=0 video). Spec matches
    # PLATFORM_SPECS in ffmpeg.py: 48kHz stereo, 192 kbps AAC.
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", background_path,
        *audio_input,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        *audio_map,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(duration_seconds),
        "-r", "30",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.warning(
                "[hook_thumbnail] ffmpeg exit=%d stderr=%s vf=%s",
                result.returncode, result.stderr[-1500:], vf,
            )
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hook_thumbnail] ffmpeg error: %s", exc)
        return False
    finally:
        # Clean up the temp text file
        try:
            Path(text_file_path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return True


def prepend_intro_to_composite(
    composite_path: str,
    intro_path: str,
    output_path: str,
    timeout_seconds: int = 60,
) -> bool:
    """Concat ``intro_path`` in front of ``composite_path`` using the
    ffmpeg concat *filter* (re-encoded, not stream copy).

    Why the filter and not the demuxer: even when both inputs are
    ostensibly libx264/AAC/48kHz/1080x1920, subtle SPS/PPS mismatch
    (encoder version, timing base, GOP structure) breaks `-c copy`
    concat at runtime — same class of ``-22 EINVAL`` fault documented
    in ``motion_compositor.py:390``. The filter costs 1-2s of
    re-encoding but is bulletproof for canary-scale usage.

    Returns True on success. Fail-open: any error returns False so the
    caller can keep the original composite unchanged.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("[hook_thumbnail] ffmpeg not found for concat")
        return False
    for p in (composite_path, intro_path):
        if not Path(p).exists():
            logger.warning(
                "[hook_thumbnail] concat input missing: %s", p,
            )
            return False

    filter_complex = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,fps=30[v0];"
        "[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,fps=30[v1];"
        "[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[outv][outa]"
    )
    # `-x264-params` writes color tags directly into the H.264 SPS/VUI
    # (verified 2026-08-18: without it, container-level `-color_*`
    # flags only surface `color_primaries`; `color_trc` + `colorspace`
    # remain `unknown` in ffprobe output and later validation gates
    # per CLAUDE.md "Every rendered video MUST be bt709" trip).
    cmd = [
        "ffmpeg", "-y",
        "-i", intro_path,
        "-i", composite_path,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-x264-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-c:a", "aac",
        "-b:a", "192k",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds,
        )
        if result.returncode != 0:
            logger.warning(
                "[hook_thumbnail] concat ffmpeg exit=%d stderr=%s",
                result.returncode, result.stderr[-500:],
            )
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hook_thumbnail] concat error: %s", exc)
        return False
    return True


def generate_hook_thumbnail(
    hook: str,
    niche_id: str,
    output_path: str,
    *,
    duration_seconds: float = 0.8,
    prompt_override: str | None = None,
) -> tuple[bool, float | None]:
    """End-to-end: (1) belt run pruna/flux-dev → background image,
    (2) ffmpeg overlay hook text + pad to N-second mp4 intro,
    (3) return (success, cost_usd).

    Returns (False, None) on any failure. Caller falls back to
    legacy behavior (skip intro, use original video's first frame
    as thumbnail).
    """
    if not is_enabled_for(niche_id):
        return False, None
    if not hook or not hook.strip():
        logger.debug("[hook_thumbnail] empty hook — skip")
        return False, None

    from genlab_core.integrations.belt_client import run_app, task_cost_usd

    prompt = prompt_override or _NICHE_PROMPT_SEEDS.get(niche_id)
    if not prompt:
        logger.debug(
            "[hook_thumbnail] no prompt seed for niche=%s — skip", niche_id,
        )
        return False, None

    seed = _deterministic_seed(hook, niche_id)

    # 2026-08-18 (task #203): pick from the multi-model registry when
    # GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED is on; falls back to
    # flux-only otherwise. Deterministic hash so re-renders stay
    # idempotent; model_id is logged for future bandit reward wire.
    from genlab_core.media.hook_thumbnail_models import (
        extract_image_url,
        pick_model,
    )

    model = pick_model(hook, niche_id)
    logger.info(
        "[hook_thumbnail] niche=%s hook=%r selected_model=%s belt_app=%s",
        niche_id, hook[:50], model.model_id, model.belt_app,
    )
    result = run_app(
        model.belt_app,
        model.build_input(prompt, seed, 1080, 1920),
        timeout_seconds=120,
    )
    if not result.ok or not result.output:
        logger.warning(
            "[hook_thumbnail] belt run failed for niche=%s model=%s: %s",
            niche_id, model.model_id, result.error,
        )
        return False, None

    image_url = extract_image_url(result.output)
    if not image_url:
        logger.warning(
            "[hook_thumbnail] no image URL in output for model=%s keys=%s",
            model.model_id, list(result.output.keys()),
        )
        return False, None

    tmp_bg = str(Path(output_path).with_suffix(".bg.jpg"))
    if not _download(image_url, tmp_bg):
        return False, None

    ok = _overlay_text_and_pad(
        tmp_bg, hook, output_path, duration_seconds, niche_id=niche_id,
    )
    # Best-effort cleanup
    try:
        Path(tmp_bg).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass

    if not ok:
        return False, None

    cost = task_cost_usd(result.task_id) if result.task_id else None
    logger.info(
        "[hook_thumbnail] niche=%s cost=%s task=%s output=%s",
        niche_id, f"${cost:.4f}" if cost else "unknown",
        result.task_id, output_path,
    )
    return True, cost
