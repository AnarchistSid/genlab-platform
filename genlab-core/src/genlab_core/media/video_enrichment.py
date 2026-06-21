"""Video enrichment helper — activates the dormant VideoAnalyzer (Lever G1).

``llm/video_analyzer.py`` implements full Gemini 2.5 Flash video
reasoning: description, key_entities, visual_quality (0..1),
suggested_hook (≤60 chars), detected_products, best_moment_seconds.
Pre-2026-06-21 it had ZERO production callers — the multi-modal
infrastructure was built but never wired into the pipeline.

Lever G1 ships the wrapper. The pattern:

1. ``enrich_story_with_video_analysis(story, video_path)`` is the
   single entry point pipeline stages call.
2. Opt-in via ``GENLAB_VIDEO_ANALYSIS_ENABLED=1`` env (default off
   for safe shadow rollout — costs ~$0.005/video).
3. On success: stores result under ``story["video_analysis"]``
   so downstream consumers (G2 LLM judge, gate signal extension,
   thumbnail picker) can read structured vision data.
4. On any failure: returns the story unchanged (no exceptions
   propagate — pipeline stages must never crash on multi-modal infra).

Cost discipline:
- Default disabled means zero baseline cost. Operators flip
  ``GENLAB_VIDEO_ANALYSIS_ENABLED=1`` once they want shadow data.
- ~$0.005 per 60-second video × ~5 publishes/day × 5 niches
  = ~$0.45/month at full activation. Cheap, but worth opting into.
- VideoAnalyzer skips files >100MB internally to avoid upload timeouts.

This PR ships the wrapper only. Downstream consumers (G2 vision judge,
gate signal integration) are separate PRs that can opt-in independently.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Env flag for safe shadow rollout. Default disabled = same behavior
# as pre-Lever-G1 (no video analysis runs anywhere).
_ENABLED_ENV: str = "GENLAB_VIDEO_ANALYSIS_ENABLED"


def is_enabled() -> bool:
    """Single source of truth for the env flag check. Operators can
    flip this per-niche or globally without touching code."""
    return os.environ.get(_ENABLED_ENV, "0") == "1"


def enrich_story_with_video_analysis(
    story: dict[str, Any],
    video_path: str | Path,
    *,
    niche_id: str = "",
    max_duration_seconds: int = 60,
) -> dict[str, Any]:
    """Add structured video analysis to ``story["video_analysis"]``.

    Returns the (mutated) story dict either way:
    - When enabled + analysis succeeds: ``story["video_analysis"]``
      contains the VideoAnalyzer result dict
    - When disabled / analyzer unavailable / file missing / any failure:
      story is returned unchanged

    The function MUST NEVER raise. Pipeline stages calling this can
    assume the story dict is always valid post-return — multi-modal
    infra failures are silent at this layer (logged at debug).

    Args:
        story: Pipeline story dict (mutated in place + returned)
        video_path: Path to the rendered video file (typically from
            ``story["visual_paths"][0]`` after render_visuals stage)
        niche_id: Niche identifier — passed through to VideoAnalyzer
            for niche-aware prompts (future iteration)
        max_duration_seconds: Cap analysis to first N seconds of video
            (default 60, matches VideoAnalyzer default)
    """
    if not is_enabled():
        return story

    try:
        from genlab_core.llm.video_analyzer import VideoAnalyzer

        analyzer = VideoAnalyzer()
        if not analyzer.available:
            # No GOOGLE_API_KEY — log at debug, don't spam warnings
            # since this is the expected state when the env flag is
            # set but the key hasn't been provisioned yet.
            logger.debug(
                "[video_enrichment] enabled but VideoAnalyzer unavailable "
                "(GOOGLE_API_KEY missing) — story unchanged"
            )
            return story

        result = analyzer.analyze(
            video_path,
            niche_id=niche_id,
            max_duration_seconds=max_duration_seconds,
        )

        if not result:
            # Empty result = analyzer failed silently (file missing,
            # too large, upload timeout). Don't pollute story dict
            # with empty values — downstream consumers should use
            # ``story.get("video_analysis")`` and treat None as
            # "no signal available".
            logger.debug(
                "[video_enrichment] analyzer returned empty result for %s — story unchanged",
                video_path,
            )
            return story

        story["video_analysis"] = result
        logger.info(
            "[video_enrichment] enriched story (niche=%s) with %d keys: %s",
            niche_id or "unknown",
            len(result),
            sorted(result.keys()),
        )
        return story

    except Exception as exc:
        # Catch-all for any unexpected failure — import errors,
        # network errors, malformed responses. Pipeline stages MUST
        # NOT crash on multi-modal infra issues.
        logger.debug(
            "[video_enrichment] enrichment failed for niche=%s path=%s: %s",
            niche_id or "unknown",
            video_path,
            exc,
        )
        return story


def get_visual_quality(story: dict[str, Any]) -> float | None:
    """Convenience accessor for the visual_quality signal.

    Returns the visual_quality score (0..1) if VideoAnalyzer has run
    on this story, otherwise None. Designed for downstream consumers
    that want to read the signal without knowing the storage shape:

        from genlab_core.media.video_enrichment import get_visual_quality
        quality = get_visual_quality(story)
        if quality is not None and quality < 0.5:
            # gate-side check, drift signal, etc.
            ...

    Future consumers (G2 LLM judge, gate signal extension, thumbnail
    picker) should add similar accessors as they emerge — keeps the
    storage shape encapsulated.
    """
    analysis = story.get("video_analysis") if isinstance(story, dict) else None
    if not isinstance(analysis, dict):
        return None
    raw = analysis.get("visual_quality")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
