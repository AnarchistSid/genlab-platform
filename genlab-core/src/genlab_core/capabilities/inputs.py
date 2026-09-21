"""Per-app input shaping for registry entries.

Lifted verbatim from ``media/hook_thumbnail_models.py`` and
``media/pruna_video_client_models.py`` when those two registries were
absorbed (Part 33 §1). The bodies are unchanged — each app wants a different
field name for the same idea (``image_size`` vs ``size`` vs ``resolution``),
and that shaping is the only thing those modules held that the registry
could not express as data.
"""

from __future__ import annotations

from typing import Any


def _build_flux_input(
    prompt: str, seed: int, width: int, height: int,
) -> dict[str, Any]:
    """pruna/flux-dev — accepts width/height directly."""
    return {
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_inference_steps": 20,
        "seed": seed,
    }


def _build_gpt_image_input(
    prompt: str, seed: int, width: int, height: int,
) -> dict[str, Any]:
    """openai/gpt-image-2 — caps at 1024×1536 portrait. Downstream
    ffmpeg pass in _overlay_text_and_pad already scale+crops to
    1080×1920 so the actual output size doesn't matter to the caller."""
    # gpt-image-2 supports 1024×1024 (square), 1536×1024 (landscape),
    # 1024×1536 (portrait). Pick portrait for closest match to 9:16.
    return {
        "prompt": prompt,
        "width": 1024,
        "height": 1536,
        "n": 1,
        "quality": "low",  # cheapest tier
        "output_format": "png",
    }


def _build_grok_input(
    prompt: str, seed: int, width: int, height: int,
) -> dict[str, Any]:
    """xai/grok-imagine-image — uses aspect_ratio not w/h."""
    return {
        "prompt": prompt,
        "aspect_ratio": "9:16",
        "n": 1,
    }


def _build_seedream_input(
    prompt: str, seed: int, width: int, height: int,
) -> dict[str, Any]:
    """bytedance/seedream-4-5 — cinematic ByteDance aesthetic.
    Uses `size` (2K default) + `watermark` toggle."""
    return {
        "prompt": prompt,
        "size": "2K",
        "watermark": False,
    }


def _build_gemini_pro_input(
    prompt: str, seed: int, width: int, height: int,
) -> dict[str, Any]:
    """google/gemini-3-pro-image-preview (nano-banana-2) — premium
    Google Gemini image. Uses `aspect_ratio` + `resolution` (1K min).
    Higher-cost tier ($0.134/image) so bandit should learn to only
    use it when it materially outperforms cheaper alternatives."""
    return {
        "prompt": prompt,
        "aspect_ratio": "9:16",
        "resolution": "1K",
        "num_images": 1,
        "output_format": "png",
        "safety_tolerance": "BLOCK_NONE",
        "enable_google_search": False,
        "retry_count": 2,
    }


def _build_reve_input(
    prompt: str, seed: int, width: int, height: int,
) -> dict[str, Any]:
    """falai/reve — strong text-in-image capability (good for
    charts / headline overlays). Uses `mode` (auto) + `output_format`."""
    return {
        "prompt": prompt,
        "mode": "auto",
        "output_format": "png",
    }


def _build_pruna_input(
    prompt: str, seed: int, duration_s: int,
    resolution: str, aspect_ratio: str, draft: bool,
) -> dict[str, Any]:
    """pruna/p-video — direct width/height + draft toggle."""
    return {
        "prompt": prompt,
        "duration": duration_s,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "fps": 24,
        "draft": draft,
        "prompt_upsampling": True,
        "seed": seed,
        "disable_safety_filter": False,
    }


def _build_wan_input(
    prompt: str, seed: int, duration_s: int,
    resolution: str, aspect_ratio: str, draft: bool,
) -> dict[str, Any]:
    """alibaba/wan-2-7-t2v — uses uppercase resolution enum, no
    draft mode, no aspect_ratio (720P/1080P imply 16:9-ish)."""
    # Wan expects 720P or 1080P (uppercase). Map from p-video's
    # lowercase convention.
    wan_res = resolution.upper() if resolution else "720P"
    return {
        "prompt": prompt,
        "duration": duration_s,
        "resolution": wan_res,
        "seed": seed,
        "prompt_extend": True,
        "watermark": False,
    }


def _build_kling_input(
    prompt: str, seed: int, duration_s: int,
    resolution: str, aspect_ratio: str, draft: bool,
) -> dict[str, Any]:
    """klingai/video-v2-6 — uses aspect_ratio + sound toggle. No
    seed field. duration is fixed at 5 or 10 seconds."""
    # Kling only accepts 5 or 10 second durations
    kling_dur = 5 if duration_s <= 5 else 10
    return {
        "prompt": prompt,
        "duration": kling_dur,
        "resolution": resolution or "720p",
        "aspect_ratio": aspect_ratio or "9:16",
        "sound": False,  # avoid double-audio when we overlay TTS
    }


def _build_seedance_input(
    prompt: str, seed: int, duration_s: int,
    resolution: str, aspect_ratio: str, draft: bool,
) -> dict[str, Any]:
    """bytedance/seedance-2-0-fast — action-y ByteDance aesthetic.
    Uses `ratio=adaptive` for auto aspect matching + seed=-1 for
    randomness. Sets generate_audio=False so we don't get double-audio
    when we overlay TTS."""
    return {
        "prompt": prompt,
        "duration": duration_s,
        "resolution": resolution or "720p",
        "ratio": "adaptive",
        "seed": seed if seed >= 0 else -1,
        "watermark": False,
        "generate_audio": False,
    }


def _build_veo_input(
    prompt: str, seed: int, duration_s: int,
    resolution: str, aspect_ratio: str, draft: bool,
) -> dict[str, Any]:
    """google/veo-3 — premium Google Gemini video model. Higher-cost
    tier ($0.20-0.60/sec) so bandit gates when it materially
    outperforms cheaper alternatives. Uses `aspect_ratio` (16:9 or
    9:16 for portrait) + fixed duration=8s."""
    # Veo only supports fixed durations; clamp to 8s (Reels-friendly)
    return {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio or "9:16",
        "duration": 8,
        "resolution": resolution or "720p",
        "num_videos": 1,
        "person_generation": "allow_adult",
        "generate_audio": False,
    }
