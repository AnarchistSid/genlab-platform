"""FrameDrift scoring strategy.

Scores anime content with:
- 24h half-life decay
- Trend cycle multipliers (EMERGING 1.4x, DECLINING 0.5x)
- Creator spotlight magnitude boost (1.9x)
- Collaboration magnitude boost (1.5x)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genlab_core.scoring.composite_scorer import score_visual_potential
from genlab_core.strategies.base_time_based_scoring import BaseTimeBasedScoringStrategy

from .trend_cycle import TrendCycleStage, get_trend_cycle_multiplier

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


class AnimeScoringStrategy(BaseTimeBasedScoringStrategy):
    """Score and rank anime content with trend cycle multipliers.

    R-70 part 2 PR 5c: inherits from ``BaseTimeBasedScoringStrategy``
    (SR + FD second-tier base). The byte-identical ``__init__`` /
    ``_ensure_config`` / ``_score_timeliness`` are now inherited;
    ``_score_novelty`` is inherited from the narrow ``BaseScoringStrategy``
    via the second-tier base's parent chain. Kept per-channel:
    ``_score_magnitude``, ``_score_engagement_potential``, ``score_item``
    (with trend_cycle multiplier), and ``execute``.
    """

    _log_prefix = "[anime]"
    _default_timeliness_half_life_hours = 24.0

    def __init__(self) -> None:
        super().__init__()
        logger.info("[anime] AnimeScoringStrategy initialized")
        self._scoring_yaml_path = NICHE_ROOT / "config" / "scoring_weights.yaml"

    # ``_ensure_config`` and ``_score_timeliness`` now inherited from
    # ``BaseTimeBasedScoringStrategy``; ``_score_novelty`` inherited from
    # ``BaseScoringStrategy`` via the parent chain.

    def _score_magnitude(self, item: dict) -> float:
        """Score magnitude — creator spotlights, collabs, new releases."""
        is_creator = item.get("is_creator_spotlight", False)
        is_collab = item.get("is_collab", False)
        is_release = item.get("is_new_release", False)
        is_event = item.get("is_event_coverage", False)

        score = 0.5  # baseline

        if is_creator:
            creator_mult = self._scoring.get("magnitude", {}).get("celebrity_multiplier", 1.9)
            score = min(1.0, 0.5 * creator_mult)

        if is_collab:
            collab_mult = self._scoring.get("magnitude", {}).get("collaboration_multiplier", 1.5)
            score = max(score, min(1.0, 0.5 * collab_mult))

        if is_release:
            score = max(score, 0.7)

        if is_event:
            score = max(score, 0.6)

        return min(1.0, score)

    def _score_engagement_potential(self, item: dict) -> float:
        """Score engagement based on cross-mention velocity."""
        mention_count = item.get("source_mention_count", 1)
        if mention_count >= 4:
            return 1.0
        if mention_count >= 3:
            return 0.8
        if mention_count >= 2:
            return 0.6
        return 0.3

    # ``_score_novelty`` inherited from BaseScoringStrategy (PR 5a) —
    # body is ``return item.get("novelty_score", 0.5)``.

    def score_item(self, item: dict) -> dict:
        """Score a single anime content item."""
        self._ensure_config()

        weights = self._scoring.get("weights", {})
        scores = {
            "timeliness": self._score_timeliness(item),
            "magnitude": self._score_magnitude(item),
            "engagement_potential": self._score_engagement_potential(item),
            "novelty": self._score_novelty(item),
        }

        weighted_sum = sum(scores[dim] * weights.get(dim, 0) for dim in scores)

        # Trend cycle multiplier — from config
        cycle_str = item.get("trend_cycle_stage", "unknown")
        try:
            cycle = TrendCycleStage(cycle_str)
        except ValueError:
            cycle = TrendCycleStage.UNKNOWN
        cycle_mult = get_trend_cycle_multiplier(cycle, self._config)

        final_score = weighted_sum * cycle_mult

        return {
            **item,
            "scores": scores,
            "score": round(final_score, 4),
            "final_score": round(final_score, 4),
            # 2026-07-21: mirror final_score into composite_score (shared
            # auto_approval_gate contract). Same class-of-bug as gaming/
            # sports/movies. Agent 2 investigation.
            "composite_score": round(final_score, 4),
            "trend_cycle_multiplier": cycle_mult,
            "scored_at": datetime.now(UTC).isoformat(),
        }

    def execute(self, context: Any) -> Any:
        """Score, rank, and filter anime stories."""
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[anime] No stories to score")
            context.setdefault("run_stats", {})["scoring"] = {
                "input_count": 0,
                "scored_count": 0,
                "dropped_count": 0,
            }
            return context

        # Visual potential gate: drop stories that can never become good reels
        min_visual = self._thresholds.get("min_visual_potential", 0.3)
        visual_passed = []
        visual_rejected = 0
        for s in stories:
            vp = score_visual_potential(s, "anime")
            s["visual_potential"] = vp
            if vp >= min_visual:
                visual_passed.append(s)
            else:
                visual_rejected += 1
                logger.info(
                    "[anime] Visual potential rejected (%.1f): %s",
                    vp,
                    s.get("title", "")[:60],
                )
        stories = visual_passed

        scored = [self.score_item(s) for s in stories]
        scored.sort(key=lambda c: c["final_score"], reverse=True)

        for i, item in enumerate(scored):
            item["rank"] = i + 1

        min_score = self._thresholds.get("min_score", 0.42)
        above = [c for c in scored if c["final_score"] >= min_score]
        dropped = len(scored) - len(above)

        top_n = self._thresholds.get("top_items_per_run", 4)
        if len(above) > top_n:
            # Video-first: trending video stories must survive the top-N cut.
            video_stories = [s for s in above if s.get("_trending_video")]
            rest = [s for s in above if not s.get("_trending_video")]
            above = (video_stories + rest)[:top_n]

        context["stories"] = above
        context.setdefault("run_stats", {})["scoring"] = {
            "input_count": len(stories),
            "scored_count": len(above),
            "dropped_count": dropped,
            "top_score": above[0]["final_score"] if above else 0,
        }

        logger.info(
            "[anime] Scored %d -> %d stories (dropped %d below %.2f)",
            len(stories),
            len(above),
            dropped,
            min_score,
        )
        return context
