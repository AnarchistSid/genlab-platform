"""Multi-model image generation for hook_thumbnail backgrounds.

## Why this exists

Task #203 (2026-08-18): the hook_thumbnail intro uses one image model
(pruna/flux-dev, $0.005/image, since #191). Different image models
produce visibly different aesthetics — flux's "cinematic realism",
gpt-image-2's "polished editorial", grok's "energetic saturated".
IG-feed CTR is aesthetic-sensitive on cold-audience accounts (rule
#24 growth-target context), so a rotation across models gives us
data on which aesthetic converts.

## Design

Deterministic-hash rotation across the registered model set. Same
``(hook, niche)`` inputs always pick the same model — enables
idempotent re-renders and unit-test reproducibility. When the
canary flag ``GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED`` is off
(default), always returns the flux baseline — zero behavior change.

Each model has a distinct input schema; ``build_input`` normalises
across them so the caller passes ``(prompt, seed, w, h)`` and gets
back a dict ready for ``belt app run``.

## Why not full bandit tonight

Cold-start uniform is fine for the first ~50 posts across the
canary. Once operator has aesthetic-vs-CTR data, wiring reward flow
into the transformation orchestrator's dimension system is a
follow-up (~2h). Tonight ships the diversity; reward-loop is
task #204.

## Cost picture (per image)

* pruna/flux-dev            $0.005  (baseline, current tier-0)
* openai/gpt-image-2 low     $0.006
* xai/grok-imagine-image     $0.020

Uniform rotation → average $0.010/image = $0.30/mo per niche at
5 posts/day. Trivial.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Final

logger = logging.getLogger(__name__)


_MULTI_MODEL_FLAG: Final[str] = "GENLAB_HOOK_THUMBNAIL_MULTI_MODEL_ENABLED"


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


@dataclass(frozen=True)
class ImageModel:
    """One registered background-image model."""
    model_id: str  # short identifier for logging + bandit
    belt_app: str  # namespace/name for `belt app run`
    build_input: Callable[..., dict[str, Any]]
    cost_per_image_usd: float


_REGISTRY: Final[tuple[ImageModel, ...]] = (
    ImageModel(
        model_id="flux",
        belt_app="pruna/flux-dev",
        build_input=_build_flux_input,
        cost_per_image_usd=0.005,
    ),
    ImageModel(
        model_id="gpt-image-2",
        belt_app="openai/gpt-image-2",
        build_input=_build_gpt_image_input,
        cost_per_image_usd=0.006,
    ),
    ImageModel(
        model_id="grok-imagine",
        belt_app="xai/grok-imagine-image",
        build_input=_build_grok_input,
        cost_per_image_usd=0.020,
    ),
)


def multi_model_enabled() -> bool:
    """Read the canary flag at call time. Off (default) → always
    return the flux baseline for zero-regression behavior."""
    val = (os.environ.get(_MULTI_MODEL_FLAG) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _flux_model() -> ImageModel:
    """The always-available baseline — never returned as None."""
    return _REGISTRY[0]


def pick_model(hook: str, niche_id: str) -> ImageModel:
    """Deterministic rotation across the registered set.

    Same (hook, niche) → same model. Enables:
      * idempotent re-renders on retry
      * unit-test reproducibility
      * per-blueprint arm attribution (the model_id flows to the
        blueprint's extra JSONB so future bandit reward can join
        engagement metrics ← model_id → posterior update).

    When the multi-model flag is off, always returns flux.
    """
    if not multi_model_enabled():
        return _flux_model()
    h = hashlib.sha256(
        f"{niche_id}::{hook}".encode("utf-8"),
    ).digest()
    idx = int.from_bytes(h[:2], "big") % len(_REGISTRY)
    return _REGISTRY[idx]


def extract_image_url(output: dict[str, Any]) -> str | None:
    """Model-agnostic image-URL extraction. Different apps use
    different response key conventions."""
    for key in ("image", "image_output", "output", "images"):
        val = output.get(key)
        if not val:
            continue
        # Some apps return a list; take the first URL.
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                for k in ("url", "image_url", "image"):
                    if k in first and isinstance(first[k], str):
                        return first[k]
        elif isinstance(val, str):
            return val
    return None
