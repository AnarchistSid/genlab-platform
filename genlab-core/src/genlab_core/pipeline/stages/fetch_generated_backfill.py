"""Pipeline stage: generate an AI backfill video when the fetch pool
comes up dry.

## Why this exists

Task #192 (2026-08-18) — companion to the dedup fix in commit
``a7d1a8bf``. Anime's fetcher pool produced ~11 unique video_ids per
4-day window against 22 YouTube channels + 16 subreddits; when every
fetched story's video_id had already been published, the pipeline
produced 0 blueprints for the day. Belt-and-suspenders backfill: when
the surviving-story list is below threshold, generate one anime clip
via ``pruna/p-video`` and inject it as a new story.

## When it fires

The stage sits AFTER ``PreDownloadDedup`` in the anime pipeline. It:

  1. Checks the ``GENLAB_ANIME_BACKFILL_NICHES`` env flag. Off → return.
  2. Reads ``context["stories"]`` (post-dedup). If length ≥ threshold
     (default 2) → return. Anime is not starved.
  3. Otherwise generates one video via
     ``pruna_video_client.generate_backfill_clip``. Injects a new
     StoryCandidate carrying the local file path + AI-attribution.

The generated story sets ``bypass_video_id_dedup=True`` because it
has no upstream video_id, per the StoryCandidate contract from the
class-of-bug ``fetcher-schema-drift-from-downstream-contract`` memo.
It also sets ``source_credit`` to the AI-generated marker so the L4
publisher validation gate accepts the caption at publish time.

## Cost controls

``GENLAB_ANIME_BACKFILL_MAX_PER_RUN`` — hard cap on generations per
pipeline fire (default 1). Prevents a bad-fetcher-day from burning
$5 in a single run.

## Kill switches

  * Unset ``GENLAB_ANIME_BACKFILL_NICHES`` — stage returns immediately.
  * Set ``GENLAB_ANIME_BACKFILL_MAX_PER_RUN=0`` — flag on, cap zero.
  * ``touch /opt/genlab/.runtime/anime_backfill_kill`` — hard block.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genlab_core.pipeline.models import FetcherStage, merge_stories
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)

# Evergreen prompt seeds — rotated deterministically by (day, niche).
# Kept generic on purpose: specific series names risk brand-safety +
# copyright signal from the video-gen model. Visual-focused vocabulary
# aligns with pruna/p-video's documented best-fit.
_ANIME_TOPIC_ROTATION: tuple[str, ...] = (
    "epic anime fight scene",
    "anime protagonist transformation moment",
    "anime power-up burst of light",
    "anime rival face-off dramatic zoom",
    "anime team victory celebration",
    "anime action clash of blades",
    "anime dragon soaring through clouds",
    "anime speed lines and impact frames",
)

_KILL_SWITCH_PATH: str = "/opt/genlab/.runtime/anime_backfill_kill"
_MAX_PER_RUN_ENV: str = "GENLAB_ANIME_BACKFILL_MAX_PER_RUN"
_THRESHOLD_ENV: str = "GENLAB_ANIME_BACKFILL_MIN_STORIES"


def _kill_switch_active() -> bool:
    return os.path.exists(_KILL_SWITCH_PATH)


def _max_per_run() -> int:
    raw = os.environ.get(_MAX_PER_RUN_ENV, "1").strip()
    try:
        v = int(raw)
    except ValueError:
        return 1
    return max(0, v)


def _threshold() -> int:
    """Stories needed post-dedup to skip backfill entirely.
    Default 2 — if we already have 2+ real videos, don't burn $ on
    AI backfill."""
    raw = os.environ.get(_THRESHOLD_ENV, "2").strip()
    try:
        v = int(raw)
    except ValueError:
        return 2
    return max(0, v)


def _todays_topic(niche_id: str) -> str:
    """Deterministic daily rotation: index into topic bank by (day,
    niche). Same day + niche → same topic across timer fires. Prevents
    the same topic being picked twice on the same day if the stage
    runs multiple times."""
    day_ordinal = datetime.now(UTC).toordinal()
    idx = (day_ordinal + hash(niche_id)) % len(_ANIME_TOPIC_ROTATION)
    return _ANIME_TOPIC_ROTATION[idx]


class FetchGeneratedBackfill(FetcherStage):
    """Backfill the story queue with AI-generated video when fetchers
    return an empty (post-dedup) pool.

    Emits ``genlab_ai_backfill`` — a synthetic source that the
    push_to_backlog L2 gate exempts from the source_channel_id
    requirement (only YouTube-sourced blueprints trip that gate).
    """

    EMITTED_SOURCES = frozenset({"genlab_ai_backfill"})

    def execute(self, context: StageContext) -> StageContext:
        niche_id = context.get("niche_id", "")

        # Flag + kill-switch checks
        from genlab_core.media.pruna_video_client import (
            generate_backfill_clip,
            is_enabled_for,
        )

        if not is_enabled_for(niche_id):
            return context
        if _kill_switch_active():
            logger.warning(
                "[GenBackfill] kill switch %s present — skipping",
                _KILL_SWITCH_PATH,
            )
            return context

        max_gen = _max_per_run()
        if max_gen <= 0:
            logger.info("[GenBackfill] max_per_run=0 — disabled by cap")
            return context

        stories = context.get("stories", []) or []
        threshold = _threshold()
        if len(stories) >= threshold:
            logger.info(
                "[GenBackfill] pool has %d stories ≥ threshold %d — "
                "no backfill needed",
                len(stories), threshold,
            )
            return context

        logger.warning(
            "[GenBackfill] pool has %d stories < threshold %d — "
            "generating up to %d backfill clip(s) for niche=%s",
            len(stories), threshold, max_gen, niche_id,
        )

        topic = _todays_topic(niche_id)
        # Where to save. Runs directory is provided by orchestrator; fall
        # back to tempdir so tests don't need the whole pipeline context.
        run_dir = context.get("run_dir") or tempfile.gettempdir()
        Path(run_dir).mkdir(parents=True, exist_ok=True)

        new_stories: list[dict[str, Any]] = []
        total_cost = 0.0
        for i in range(max_gen):
            # Unique output path per-generation-per-run.
            out_path = str(Path(run_dir) / f"backfill_{niche_id}_{i}.mp4")
            _bandit_ctx: dict = {}
            result = generate_backfill_clip(
                topic_title=topic,
                niche_id=niche_id,
                output_path=out_path,
                blueprint_context=_bandit_ctx,
            )
            if not result.ok:
                logger.warning(
                    "[GenBackfill] generation %d/%d failed: %s",
                    i + 1, max_gen, result.error,
                )
                continue
            if result.cost_usd:
                total_cost += result.cost_usd

            now_iso = datetime.now(UTC).isoformat()
            from genlab_core.cache.stable_ids import generate_story_id

            # Synthetic story_id derived from the pruna task_id (idempotent
            # per generation). generate_story_id requires a real URL —
            # video_url is populated when the belt call succeeded.
            sid = generate_story_id(
                result.video_url or f"https://gen.local/{result.task_id}",
                now_iso,
            )
            title = f"AI-generated: {topic}"
            story = {
                "story_id": sid,
                "title": title,
                # source_type must be non-YouTube so push_to_backlog L2
                # exempts it from the source_channel_id requirement.
                "source": "genlab_ai_backfill",
                "source_type": "genlab_ai_backfill",
                "source_url": result.video_url or f"file://{out_path}",
                "canonical_url": result.video_url or f"file://{out_path}",
                # L1 attribution gate needs BOTH channel_id + channel_name.
                "source_channel_id": "genlab_ai_backfill",
                "source_channel_name": "Gen Lab AI",
                # L4 publisher validation needs a marker in the caption.
                # The writer wires this via story['source_credit'] into
                # the final caption downstream.
                "source_credit": "\U0001f916 AI-generated by Gen Lab",
                "published_at": now_iso,
                "fetched_at": now_iso,
                "summary": (
                    f"An AI-generated anime moment: {topic}. Produced by "
                    "Gen Lab using pruna/p-video (inference.sh). "
                    "No external creator to credit — this is synthetic "
                    "content generated fresh for this reel."
                ),
                # StoryCandidate contract: absent-video_id must set
                # bypass_video_id_dedup + bypass_reason per the class-
                # of-bug fetcher-schema-drift memo (2026-08-10).
                "video_id": None,
                "bypass_video_id_dedup": True,
                "bypass_reason": "ai_generated_backfill",
                "video_source": "genlab_ai_backfill",
                "niche_id": niche_id,
                "_trending_video": True,
                "source_mention_count": 1,
                # Local path so DownloadTopVideos doesn't re-download.
                "local_video_path": result.local_path or out_path,
                "backfill_task_id": result.task_id,
                "backfill_cost_usd": result.cost_usd,
            }
            # Bandit attribution: arm_id from the video model registry.
            # Persisted onto story so push_to_backlog serializes into
            # blueprint.arm_ids_by_dimension → route_dimension_reward
            # auto-updates the Beta posterior at 48h.
            _arm_id = _bandit_ctx.get("_video_backfill_arm_id")
            if _arm_id:
                story.setdefault(
                    "arm_ids_by_dimension", {},
                )["video_backfill_model"] = _arm_id
            new_stories.append(story)

        if new_stories:
            merge_stories(context, new_stories)

        run_stats = context.setdefault("run_stats", {})
        run_stats["genlab_ai_backfill"] = {
            "generated": len(new_stories),
            "requested": max_gen,
            "total_cost_usd": total_cost,
            "topic": topic,
        }
        logger.info(
            "[GenBackfill] niche=%s generated=%d topic=%r cost=$%.4f",
            niche_id, len(new_stories), topic, total_cost,
        )
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
