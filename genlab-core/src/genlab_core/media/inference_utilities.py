"""Wrappers for inference.sh utility apps.

Four helpers, one module. Each delegates to the shared
``genlab_core.integrations.belt_client.run_app`` and follows the
same fail-open contract as ``pruna_video_client`` /
``hook_thumbnail`` — any belt error, download failure, or missing
output returns a ``UtilityResult(ok=False, ...)``. Caller falls back
to legacy behavior (skip the enhancement).

## Wrapped apps

  * ``elevenlabs/voice-isolator`` — remove background noise / isolate
    vocals from an audio file. Real utility for downloaded creator
    clips that have music underscoring or room echo — cleaner
    voice before we overlay our TTS narration.
    Cost: $0.12/min.

  * ``infsh/birefnet`` — background removal with transparent alpha
    or replacement colour fill. Enables subject-isolation compositing
    for the split_screen / storytime variants.
    Cost: n/a (compute-based, ~cents per image).

  * ``falai/topaz-image-upscaler`` — Topaz Gigapixel-style upscale
    with optional face enhancement. Sharpens hook_thumbnail
    backgrounds when the source model caps below 1080x1920 (e.g.
    gpt-image-2 tops at 1024x1536).
    Cost: $0.08 for up to 24MP.

  * ``pruna/p-image-edit`` — image editing via prompt instructions
    (e.g. "add a subtle vignette", "brighten the background").
    Useful for retouching hook_thumbnail backgrounds without
    regenerating.
    Cost: $0.01/image.

## Design

Zero pipeline wire in this module — each helper is callable by
future pipeline stages / strategies when a specific need surfaces.
Ships the primitives so downstream feature work costs <30 min
instead of days of "wire the belt call first."

All helpers are flag-gated by a single ``GENLAB_INFERENCE_UTILITIES_
ENABLED`` env var (default off). Belt cost is real for each call
— gate prevents accidental enable during dev.
"""
from __future__ import annotations

import logging
import os
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_ROLLOUT_ENV = "GENLAB_INFERENCE_UTILITIES_ENABLED"


@dataclass(frozen=True)
class UtilityResult:
    """Outcome of a single utility-app call."""
    ok: bool
    output_path: str | None = None
    output_url: str | None = None
    cost_usd: float | None = None
    task_id: str | None = None
    error: str | None = None


def _flag_enabled() -> bool:
    """Read the enable flag at call time."""
    val = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _download(url: str, dest: str) -> bool:
    """Fetch a belt-hosted URL to a local file. GenLab UA + short
    timeout. Fail-open — logs and returns False on any error."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GenLab/1.0 inference_utilities"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[inference_utilities] download failed: %s", exc)
        return False


def _run_and_download(
    app: str,
    input_data: dict[str, Any],
    output_path: str,
    output_url_keys: tuple[str, ...] = ("output", "image", "audio", "video"),
    timeout_seconds: int = 120,
) -> UtilityResult:
    """Shared workhorse: run app, extract output URL, download.

    All 4 utility helpers use this same recipe — only the app name +
    input dict + output-key preferences differ.
    """
    if not _flag_enabled():
        return UtilityResult(
            ok=False,
            error=f"{_ROLLOUT_ENV} not enabled",
        )

    from genlab_core.integrations.belt_client import run_app, task_cost_usd

    result = run_app(app, input_data, timeout_seconds=timeout_seconds)
    if not result.ok or not result.output:
        logger.warning(
            "[inference_utilities] %s belt run failed: %s",
            app, result.error,
        )
        return UtilityResult(
            ok=False, task_id=result.task_id, error=result.error,
        )

    # Try each output key in order — different apps use different names
    output_url: str | None = None
    for key in output_url_keys:
        val = result.output.get(key)
        if isinstance(val, str) and val:
            output_url = val
            break
        if isinstance(val, list) and val and isinstance(val[0], str):
            output_url = val[0]
            break
        if isinstance(val, dict):
            for k in ("url", "image_url", "audio_url", "video_url"):
                v = val.get(k)
                if isinstance(v, str) and v:
                    output_url = v
                    break
            if output_url:
                break

    if not output_url:
        return UtilityResult(
            ok=False, task_id=result.task_id,
            error=f"no output URL in keys={list(result.output.keys())}",
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if not _download(output_url, output_path):
        return UtilityResult(
            ok=False, output_url=output_url, task_id=result.task_id,
            error="download failed",
        )

    cost = task_cost_usd(result.task_id) if result.task_id else None
    logger.info(
        "[inference_utilities] %s ok cost=%s task=%s output=%s",
        app, f"${cost:.4f}" if cost else "unknown",
        result.task_id, output_path,
    )
    return UtilityResult(
        ok=True, output_path=output_path, output_url=output_url,
        cost_usd=cost, task_id=result.task_id,
    )


def isolate_voice(
    audio_url_or_path: str,
    output_path: str,
    *,
    timeout_seconds: int = 180,
) -> UtilityResult:
    """Clean up an audio track — isolate vocals, remove background
    music/noise via ``elevenlabs/voice-isolator``.

    Args:
        audio_url_or_path: URL of a hosted audio file, OR a local
            file path (belt CLI auto-uploads local paths).
        output_path: where to save the cleaned audio (MP3).
        timeout_seconds: max wait for the belt call.

    Cost: $0.12/min of input audio.
    """
    return _run_and_download(
        app="elevenlabs/voice-isolator",
        input_data={"audio": audio_url_or_path},
        output_path=output_path,
        output_url_keys=("audio", "output"),
        timeout_seconds=timeout_seconds,
    )


def remove_background(
    image_url_or_path: str,
    output_path: str,
    *,
    background_color: str | None = None,
    return_mask: bool = False,
    timeout_seconds: int = 120,
) -> UtilityResult:
    """Remove background from an image via ``infsh/birefnet``.

    Args:
        image_url_or_path: URL or local path of the input image.
        output_path: where to save the transparent PNG (or masked
            image if ``return_mask=True``).
        background_color: when provided, fills the removed area
            with this colour (hex ``#RRGGBBAA``). Default transparent.
        return_mask: True → return the alpha mask instead of the
            processed image. Useful for downstream custom compositing.
    """
    input_data: dict[str, Any] = {
        "image": image_url_or_path,
        "return_mask": return_mask,
        "fill_background": bool(background_color),
    }
    if background_color:
        input_data["background_color"] = background_color
    return _run_and_download(
        app="infsh/birefnet",
        input_data=input_data,
        output_path=output_path,
        output_url_keys=("image", "output", "mask"),
        timeout_seconds=timeout_seconds,
    )


def upscale_image(
    image_url_or_path: str,
    output_path: str,
    *,
    upscale_factor: int = 2,
    face_enhancement: bool = True,
    model: str = "Standard V2",
    timeout_seconds: int = 300,
) -> UtilityResult:
    """Upscale (and optionally face-enhance) an image via
    ``falai/topaz-image-upscaler``.

    Args:
        upscale_factor: 2/4/6 — factor to multiply resolution by.
            Default 2 is safe/cheap (24MP tier at $0.08).
        face_enhancement: True → enhance detected faces (Topaz's
            face-restoration model). Turn OFF for non-portrait
            content to save a small amount of compute.
        model: Topaz model preset — "Standard V2" is a good all-
            rounder; other options include "High Fidelity V2",
            "Text Refine", "Low Resolution V2".

    Cost: $0.08 for up to 24MP, scales up per output pixel count.
    """
    return _run_and_download(
        app="falai/topaz-image-upscaler",
        input_data={
            "image": image_url_or_path,
            "upscale_factor": upscale_factor,
            "face_enhancement": face_enhancement,
            "face_enhancement_creativity": 0,
            "face_enhancement_strength": 0.8,
            "model": model,
            "output_format": "jpeg",
            "subject_detection": "All",
            "crop_to_fill": False,
        },
        output_path=output_path,
        output_url_keys=("image", "output"),
        timeout_seconds=timeout_seconds,
    )


def edit_image(
    prompt: str,
    image_urls_or_paths: list[str],
    output_path: str,
    *,
    aspect_ratio: str = "match_input_image",
    seed: int = 0,
    turbo: bool = True,
    timeout_seconds: int = 180,
) -> UtilityResult:
    """Edit an image via prompt instruction — ``pruna/p-image-edit``.

    Args:
        prompt: natural-language edit instruction (e.g. "add subtle
            vignette", "brighten the background", "replace sky with
            sunset").
        image_urls_or_paths: list of source images (typically just
            one but the app accepts multiple for composite edits).
        aspect_ratio: default "match_input_image" preserves source
            dimensions. Override with "9:16" / "16:9" / "1:1" to
            reshape.
        seed: deterministic seed (0 = random each call).
        turbo: True → faster variant (recommended for iteration).

    Cost: $0.01/image.
    """
    return _run_and_download(
        app="pruna/p-image-edit",
        input_data={
            "prompt": prompt,
            "images": image_urls_or_paths,
            "aspect_ratio": aspect_ratio,
            "seed": seed,
            "turbo": turbo,
            "disable_safety_checker": False,
        },
        output_path=output_path,
        output_url_keys=("image", "output"),
        timeout_seconds=timeout_seconds,
    )
