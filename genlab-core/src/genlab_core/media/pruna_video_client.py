"""Text-to-video generation via inference.sh's ``pruna/p-video`` app.

## Why this exists

Task #192 (2026-08-18): anime pipeline dark for 8 days (2026-08-10 to
2026-08-18) because 22 YouTube channels + 16 subreddits produced ~11
unique video_ids per 4-day window and all overlapped already-published
blueprints. The dedup fix in commit ``a7d1a8bf`` should recover
throughput naturally, but this module adds a belt-and-suspenders
backfill: when the anime fetch pool comes up dry, generate a fresh
anime-style clip from the trending topic title.

## Design

* Delegates the actual belt subprocess call to
  ``genlab_core.integrations.belt_client.run_app`` (fail-open + cost
  telemetry already handled there).
* Uses ``pruna/p-video`` — $0.005/sec at 720p draft, $0.01/sec at 1080p
  draft (see ``belt app pricing pruna/p-video``). Draft mode is fine
  for the canary; a 5-second draft clip costs ~$0.025.
* Deterministic prompt hashing → deterministic seed → same
  (topic, niche) inputs produce the same clip. Enables idempotent
  re-runs and cache-friendly testing.
* Fail-open at every layer: belt error, missing URL in response,
  download failure — all return ``(False, None)`` so the caller can
  fall back to standard fetcher output.

## Prompt shape

Prompts are built via ``_build_anime_prompt`` — takes the trending
topic title + niche context and produces a short, visually-focused
description. p-video documentation stresses that visual/action language
outperforms narrative language; the built prompt reflects that.

## Not doing here (deferred)

* Image-to-video conditioning (using an anime still as visual anchor)
  — needs a per-topic still library, out of scope tonight.
* Bandit-driven prompt variation — cold-start uniform first.
* Multi-niche support — anime canary only; extend after 7-day signal.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


_ROLLOUT_ENV: Final[str] = "GENLAB_ANIME_BACKFILL_NICHES"
_ALL_TOKENS: Final[set[str]] = {"all", "*"}
_OFF_TOKENS: Final[set[str]] = {"", "0", "false", "no", "off"}

# App choice: pruna/p-video is the cheapest text-to-video option on
# inference.sh (verified 2026-08-18 via `belt app pricing`).
_VIDEO_APP: Final[str] = "pruna/p-video"

# 5s draft clip at 720p is $0.025. Reels min-duration guard requires
# ≥15s (see post_render_transform.py), so we generate longer than 5s
# to survive the guard even after any transformation trim. 8s draft
# at 720p is $0.040 — still under $0.05/clip which is the operator
# guardrail for this canary.
_DEFAULT_DURATION_S: Final[int] = 8
_DEFAULT_RESOLUTION: Final[str] = "720p"
_DEFAULT_ASPECT_RATIO: Final[str] = "9:16"
_DEFAULT_FPS: Final[int] = 24


@dataclass(frozen=True)
class VideoGenResult:
    """Outcome of a single pruna/p-video call.

    ``local_path`` is populated only when the generated video was
    downloaded to disk; None when the call failed or the caller
    passed download=False.
    """
    ok: bool
    prompt: str
    video_url: str | None = None
    local_path: str | None = None
    cost_usd: float | None = None
    task_id: str | None = None
    error: str | None = None


def is_enabled_for(niche_id: str) -> bool:
    """True when video-gen backfill should fire for ``niche_id``.

    Env var uses same canary pattern as GENLAB_HOOK_THUMBNAIL_NICHES,
    GENLAB_PERSONA_HINT_NICHES, etc. Off tokens include empty, 0,
    false, no, off — anything else that isn't ``all`` / ``*`` is
    treated as a comma-separated allowlist.
    """
    raw = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    if raw in _OFF_TOKENS:
        return False
    if raw in _ALL_TOKENS:
        return True
    allowed = {p.strip() for p in raw.split(",") if p.strip()}
    return niche_id in allowed


def _deterministic_seed(prompt: str, niche_id: str) -> int:
    """Same (prompt, niche) → same seed → same video. Enables
    idempotent re-runs and reproducible tests."""
    h = hashlib.sha256(f"{niche_id}::{prompt}".encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big")


def _build_anime_prompt(topic_title: str) -> str:
    """Build a visually-focused pruna/p-video prompt from a trending
    anime topic title. Prompts favor action + composition + style
    keywords over narrative — matches pruna's documented best fit."""
    # Strip common noise from titles for cleaner prompt injection.
    clean = topic_title.strip().rstrip("!.?").strip()
    # Truncate long titles — keep the prompt tight for better gen.
    if len(clean) > 90:
        clean = clean[:87] + "..."
    return (
        f"Anime-style cinematic moment inspired by: {clean}. "
        "Dynamic action, dramatic lighting, vivid saturated colors, "
        "cel-shaded animation style, sharp key-frame composition, "
        "9:16 vertical composition suitable for short-form video."
    )


def _download(url: str, dest: str) -> bool:
    """Fetch a video URL to a local file. GenLab UA + short timeout.
    Fail-open — logs and returns False on any error."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GenLab/1.0 pruna_video_client"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[pruna_video] download failed: %s", exc)
        return False


def generate_backfill_clip(
    topic_title: str,
    niche_id: str,
    output_path: str,
    *,
    duration_seconds: int = _DEFAULT_DURATION_S,
    resolution: str = _DEFAULT_RESOLUTION,
    aspect_ratio: str = _DEFAULT_ASPECT_RATIO,
    draft: bool = True,
    blueprint_context: dict | None = None,
) -> VideoGenResult:
    """End-to-end: build prompt → belt run pruna/p-video → download.

    Returns a VideoGenResult. Fail-open — any failure surfaces as
    ``ok=False`` with an error string. Caller falls back to standard
    fetcher output (no synthesized story).

    ``draft=True`` uses the cheaper draft tier (~$0.025/clip at 720p
    for 5s). Set False for higher-quality generations (~4x cost).
    """
    if not is_enabled_for(niche_id):
        return VideoGenResult(
            ok=False, prompt="",
            error=f"niche_id={niche_id!r} not enabled via {_ROLLOUT_ENV}",
        )
    if not topic_title or not topic_title.strip():
        return VideoGenResult(
            ok=False, prompt="", error="empty topic_title",
        )

    from genlab_core.integrations.belt_client import run_app, task_cost_usd

    prompt = _build_anime_prompt(topic_title)
    seed = _deterministic_seed(prompt, niche_id)

    # 2026-08-18 (task #204): pick from the video model registry when
    # GENLAB_ANIME_BACKFILL_MULTI_MODEL_ENABLED is on; falls back to
    # pruna-only otherwise. Same primitive as hook_thumbnail_models —
    # deterministic hash, model_id logged for future bandit reward wire.
    from genlab_core.media.pruna_video_client_models import (
        arm_id_for,
        extract_video_url,
        pick_model,
    )

    model = pick_model(prompt, niche_id)
    logger.info(
        "[pruna_video] niche=%s selected_model=%s belt_app=%s",
        niche_id, model.model_id, model.belt_app,
    )
    result = run_app(
        model.belt_app,
        model.build_input(
            prompt=prompt, seed=seed, duration_s=duration_seconds,
            resolution=resolution, aspect_ratio=aspect_ratio, draft=draft,
        ),
        # Video gen is slower than image; give it 5 minutes headroom.
        timeout_seconds=300,
    )
    if not result.ok or not result.output:
        logger.warning(
            "[pruna_video] belt run failed for niche=%s model=%s: %s",
            niche_id, model.model_id, result.error,
        )
        return VideoGenResult(
            ok=False, prompt=prompt,
            task_id=result.task_id, error=result.error,
        )

    # Different models emit different response keys; the shared
    # extractor handles 4 shapes (video / video_output / output / list-of).
    video_url = extract_video_url(result.output)
    if not video_url or not isinstance(video_url, str):
        logger.warning(
            "[pruna_video] no video URL in output for model=%s keys=%s",
            model.model_id, list(result.output.keys()),
        )
        return VideoGenResult(
            ok=False, prompt=prompt, task_id=result.task_id,
            error=f"no video URL; keys={list(result.output.keys())}",
        )

    if not _download(video_url, output_path):
        return VideoGenResult(
            ok=False, prompt=prompt, video_url=video_url,
            task_id=result.task_id, error="download failed",
        )

    cost = task_cost_usd(result.task_id) if result.task_id else None
    logger.info(
        "[pruna_video] niche=%s cost=%s task=%s output=%s",
        niche_id,
        f"${cost:.4f}" if cost else "unknown",
        result.task_id, output_path,
    )
    # Bandit attribution: write arm_id into caller-provided dict so
    # caller can persist to story["arm_ids_by_dimension"] and the
    # existing reward router auto-updates the arm at 48h. Same
    # pattern as hook_thumbnail (task #206, 2026-08-18).
    if blueprint_context is not None:
        blueprint_context["_video_backfill_arm_id"] = arm_id_for(model)
    return VideoGenResult(
        ok=True, prompt=prompt,
        video_url=video_url,
        local_path=str(Path(output_path).resolve()),
        cost_usd=cost,
        task_id=result.task_id,
    )
