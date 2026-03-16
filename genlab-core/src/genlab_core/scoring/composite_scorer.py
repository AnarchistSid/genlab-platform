"""Composite quality-gate scorer for trending video content.

Ensures only the highest-rated content per channel per day reaches
the publishing queue. Videos must clear a per-niche composite score
threshold or they are filtered out before blueprint creation.

Formula::

    velocity_score = min(view_velocity / velocity_threshold, 1.0)
    composite      = velocity_score × trend_multiplier × niche_relevance

Where:
    - velocity_score:   normalised views/hour against niche baseline
    - trend_multiplier: Google Trends position multiplier (1.0–3.0)
    - niche_relevance:  binary 1.0 if video matches niche, else 0.0

Usage::

    scorer = CompositeScorer("gaming")
    scored = scorer.score_and_rank(videos)
    # scored contains only videos above the niche threshold, sorted DESC
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Per-niche velocity thresholds (views/hour).
# A video's view_velocity is normalised against this baseline.
# These are intentionally higher than the fetch-stage MIN_VIEW_VELOCITY
# thresholds — fetch casts a wide net, this gate narrows to publishable.
DEFAULT_VELOCITY_THRESHOLDS: Dict[str, float] = {
    "gaming": 1500.0,
    "sports": 2000.0,
    "movies": 800.0,
    "anime": 600.0,
    "ai_creators": 400.0,
    "ai_news": 400.0,  # backward compat alias
}

# Minimum composite score to pass the quality gate.
# Videos below this are filtered out of score_and_rank().
DEFAULT_MIN_COMPOSITE: Dict[str, float] = {
    "gaming": 0.35,
    "sports": 0.35,
    "movies": 0.30,
    "anime": 0.30,
    "ai_creators": 0.25,
    "ai_news": 0.25,  # backward compat alias
}


@dataclass
class VideoScore:
    """Composite score breakdown for a single video candidate."""

    video_id: str
    title: str
    view_velocity: float
    velocity_score: float
    trend_multiplier: float
    niche_relevance: float
    composite: float
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "view_velocity": round(self.view_velocity, 1),
            "velocity_score": round(self.velocity_score, 3),
            "trend_multiplier": round(self.trend_multiplier, 2),
            "niche_relevance": round(self.niche_relevance, 2),
            "composite": round(self.composite, 4),
            "passed": self.passed,
        }


class CompositeScorer:
    """Score and filter trending videos for a specific niche.

    Args:
        niche_id: Channel niche identifier (gaming, sports, movies, anime, ai_news).
        velocity_threshold: Override per-niche velocity baseline.
            If None, uses DEFAULT_VELOCITY_THRESHOLDS.
        min_composite: Override minimum composite score to pass gate.
            If None, uses DEFAULT_MIN_COMPOSITE.
    """

    def __init__(
        self,
        niche_id: str,
        velocity_threshold: Optional[float] = None,
        min_composite: Optional[float] = None,
    ):
        self.niche_id = niche_id
        self.velocity_threshold = (
            velocity_threshold
            if velocity_threshold is not None
            else DEFAULT_VELOCITY_THRESHOLDS.get(niche_id, 500.0)
        )
        self.min_composite = (
            min_composite
            if min_composite is not None
            else DEFAULT_MIN_COMPOSITE.get(niche_id, 0.30)
        )

    def score(
        self,
        video: Dict[str, Any],
        trend_multiplier: float = 1.0,
        niche_relevance: float = 1.0,
    ) -> VideoScore:
        """Compute composite score for a single video.

        Args:
            video: Dict with at least ``video_id``, ``title``, ``view_velocity``.
            trend_multiplier: Google Trends multiplier (1.0–3.0).
            niche_relevance: 1.0 if relevant to niche, 0.0 if not.

        Returns:
            VideoScore with breakdown and pass/fail flag.
        """
        view_velocity = float(video.get("view_velocity", 0))
        velocity_score = min(view_velocity / self.velocity_threshold, 1.0) if self.velocity_threshold > 0 else 0.0

        # Clamp inputs to valid ranges
        trend_mult = max(0.0, min(float(trend_multiplier), 3.0))
        relevance = 1.0 if float(niche_relevance) > 0 else 0.0

        composite = velocity_score * trend_mult * relevance
        passed = composite >= self.min_composite

        return VideoScore(
            video_id=str(video.get("video_id", "")),
            title=str(video.get("title", "")),
            view_velocity=view_velocity,
            velocity_score=velocity_score,
            trend_multiplier=trend_mult,
            niche_relevance=relevance,
            composite=composite,
            passed=passed,
        )

    def score_and_rank(
        self,
        videos: List[Dict[str, Any]],
        trend_multipliers: Optional[Dict[str, float]] = None,
        niche_relevances: Optional[Dict[str, float]] = None,
    ) -> List[VideoScore]:
        """Score all videos, filter by threshold, return sorted DESC by composite.

        Args:
            videos: List of video dicts (from TrendingVideo.to_dict()).
            trend_multipliers: Optional map of video_id → trend multiplier.
                Defaults to 1.0 for all if not provided.
            niche_relevances: Optional map of video_id → niche relevance (0 or 1).
                Defaults to 1.0 for all if not provided.

        Returns:
            List of VideoScore objects that passed the threshold, sorted
            by composite score descending (best first).
        """
        trend_map = trend_multipliers or {}
        relevance_map = niche_relevances or {}

        scored: List[VideoScore] = []
        for video in videos:
            vid = str(video.get("video_id", ""))
            vs = self.score(
                video,
                trend_multiplier=trend_map.get(vid, 1.0),
                niche_relevance=relevance_map.get(vid, 1.0),
            )
            scored.append(vs)

        passed = [s for s in scored if s.passed]
        failed_count = len(scored) - len(passed)

        if failed_count > 0:
            logger.info(
                "[CompositeScorer:%s] %d/%d videos passed quality gate (threshold=%.2f)",
                self.niche_id, len(passed), len(scored), self.min_composite,
            )

        if not passed:
            logger.warning(
                "[CompositeScorer:%s] No videos passed quality gate — "
                "0/%d above %.2f composite threshold",
                self.niche_id, len(scored), self.min_composite,
            )

        passed.sort(key=lambda s: s.composite, reverse=True)
        return passed
