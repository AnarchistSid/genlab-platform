"""Multi-model video generation for anime backfill.

## Why this exists

Task #204 (2026-08-18): pruna_video_client uses one video model
(pruna/p-video, ~$0.10/5s draft). Different video models produce
distinct motion aesthetics on short-form content — p-video's
"stable/predictable", wan's "cinematic/painterly", kling's
"dynamic/expressive". Rotation gives us data on which converts
for anime audiences (rule #24 growth-target context).

## Design

Same primitive as ``hook_thumbnail_models.py`` — deterministic-hash
rotation across a registered set. Flag-gated by
``GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED`` (default OFF → always
pruna baseline, zero behavior change).

Each model has a distinct input schema; ``build_input`` normalises
across them so ``generate_backfill_clip`` passes ``(prompt, seed,
duration_s, resolution, aspect_ratio, draft)`` and gets a dict ready
for ``belt app run``.

## Cost picture (per 5s clip)

* pruna/p-video (720p draft)   $0.05  (baseline, current tier-0)
* alibaba/wan-2-7-t2v (720p)   $0.50  (10x baseline, cinematic)
* klingai/video-v2-6            $0.21+ (variable, dynamic)

Uniform rotation → ~$0.25/clip average = $7.50/mo at 1 clip/day.
Still trivial vs total operator budget but 5x the baseline — operator
should flip only if the aesthetic upside is worth the cost bump.

## Why not full bandit tonight

Same reason as hook_thumbnail_models: cold-start uniform gives us
attribution data (model_id logged per generation), reward wire is a
follow-up when engagement signal accumulates.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Final

logger = logging.getLogger(__name__)


_MULTI_MODEL_FLAG: Final[str] = "GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED"


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


@dataclass(frozen=True)
class VideoModel:
    """One registered text-to-video model."""
    model_id: str
    belt_app: str
    build_input: Callable[..., dict[str, Any]]
    cost_per_5s_usd: float  # rough estimate at 720p draft (baseline tier)


_REGISTRY: Final[tuple[VideoModel, ...]] = (
    VideoModel(
        model_id="pruna-p-video",
        belt_app="pruna/p-video",
        build_input=_build_pruna_input,
        cost_per_5s_usd=0.10,  # live-verified 2026-08-18
    ),
    VideoModel(
        model_id="alibaba-wan-2-7",
        belt_app="alibaba/wan-2-7-t2v",
        build_input=_build_wan_input,
        cost_per_5s_usd=0.50,  # $0.10/sec × 5s at 720P
    ),
    VideoModel(
        model_id="kling-v2-6",
        belt_app="klingai/video-v2-6",
        build_input=_build_kling_input,
        cost_per_5s_usd=0.21,  # low end of $0.21-$1.68/video band
    ),
)


def multi_model_enabled() -> bool:
    """Read the canary flag at call time. Off (default) → always
    pruna baseline for zero-regression behavior."""
    val = (os.environ.get(_MULTI_MODEL_FLAG) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _pruna_model() -> VideoModel:
    """The always-available baseline — never returned as None."""
    return _REGISTRY[0]


def pick_model(prompt: str, niche_id: str) -> VideoModel:
    """Deterministic rotation across the registered set.

    Same (prompt, niche) → same model. Same rationale as
    hook_thumbnail_models.pick_model — enables idempotent re-runs
    + future per-blueprint bandit reward attribution.

    When the multi-model flag is off, always returns pruna.
    """
    if not multi_model_enabled():
        return _pruna_model()
    h = hashlib.sha256(
        f"{niche_id}::{prompt}".encode("utf-8"),
    ).digest()
    idx = int.from_bytes(h[:2], "big") % len(_REGISTRY)
    return _REGISTRY[idx]


def arm_id_for(model: VideoModel) -> str:
    """Bandit arm_id encoding for this model — matches the
    ``transform__<dim>__<value>`` convention used by
    transformation_selector so route_dimension_reward picks it up
    at 48h collection.

    Example: ``video_backfill_model__pruna-p-video``.
    """
    return f"video_backfill_model__{model.model_id}"


def extract_video_url(output: dict[str, Any]) -> str | None:
    """Model-agnostic video-URL extraction. Different apps use
    different response key conventions."""
    for key in ("video", "video_output", "output", "videos"):
        val = output.get(key)
        if not val:
            continue
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                for k in ("url", "video_url", "video"):
                    if k in first and isinstance(first[k], str):
                        return first[k]
        elif isinstance(val, str):
            return val
    return None
