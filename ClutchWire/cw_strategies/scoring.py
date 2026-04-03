"""ClutchWire scoring strategy.

Scores sports content across 4 dimensions with:
- 2h half-life exponential decay (NOT 12h)
- magnitude multipliers for championship/playoff/rivalry
- live event bonus (1.5x for in-progress games)
- upset multiplier (1.4x) and record multiplier (1.3x)
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from genlab_core.scoring.composite_scorer import score_visual_potential
from genlab_core.strategies import ScoringStrategy

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class SportScoringStrategy(ScoringStrategy):
    """Score and rank sports content with magnitude + live + upset/record multipliers."""

    def __init__(self) -> None:
        logger.info("[sports] SportScoringStrategy initialized")
        self._config: dict | None = None
        self._weights: dict | None = None
        self._multipliers: dict | None = None
        self._thresholds: dict | None = None

    def _ensure_config(self) -> None:
        if self._config is not None:
            return
        full = _load_yaml(NICHE_ROOT / "config" / "scoring_weights.yaml")
        self._config = full.get("clip_scoring", {})
        self._weights = self._config.get("weights", {})
        self._multipliers = self._config.get("magnitude_multipliers", {})
        self._thresholds = full.get("thresholds", {})

    def _score_recency(self, item: dict) -> float:
        """Exponential decay with 2h half-life."""
        half_life = self._config.get("recency", {}).get("half_life_hours", 2.0)
        fetched_at_str = item.get("fetched_at", "")
        if not fetched_at_str:
            return 0.0
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            now = datetime.now(UTC)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            hours_elapsed = (now - fetched_at).total_seconds() / 3600
            if hours_elapsed < 0:
                return 1.0
            return math.pow(0.5, hours_elapsed / half_life)
        except (ValueError, TypeError):
            return 0.0

    def _score_community_signal(self, item: dict) -> float:
        """Score based on upvote velocity and engagement."""
        upvotes = item.get("upvotes", 0) or item.get("score", 0)
        comments = item.get("comment_count", 0)
        if upvotes == 0 and comments == 0:
            return 0.1
        engagement = (upvotes + comments * 2) / 1000
        return min(1.0, engagement)

    def _score_magnitude(self, item: dict) -> float:
        """Apply magnitude multiplier based on game type."""
        game_type = item.get("game_type", "regular_season")
        multiplier = self._multipliers.get(game_type, 1.0)
        return min(1.0, multiplier / 3.0)

    def _score_novelty(self, item: dict) -> float:
        """Score novelty — lower if similar content already scored."""
        return item.get("novelty_score", 0.5)

    def score_item(self, item: dict) -> dict:
        """Score a single sports content item across 4 dimensions."""
        self._ensure_config()

        scores = {
            "recency": self._score_recency(item),
            "community_signal": self._score_community_signal(item),
            "magnitude": self._score_magnitude(item),
            "novelty": self._score_novelty(item),
        }

        weighted_sum = sum(
            scores[dim] * self._weights.get(dim, 0)
            for dim in scores
        )

        game_type = item.get("game_type", "regular_season")
        magnitude_mult = self._multipliers.get(game_type, 1.0)
        # magnitude is already factored into scores["magnitude"] via _score_magnitude
        final_score = weighted_sum

        # Live event bonus
        live_bonus = self._config.get("live_event_bonus", 1.5)
        if item.get("is_live"):
            final_score *= live_bonus

        # Upset multiplier
        upset_mult = self._config.get("upset_multiplier", 1.4)
        if item.get("is_upset"):
            final_score *= upset_mult

        # Record multiplier
        record_mult = self._config.get("record_multiplier", 1.3)
        if item.get("is_record"):
            final_score *= record_mult

        return {
            **item,
            "scores": scores,
            "score": round(final_score, 4),
            "final_score": round(final_score, 4),
            "game_type": game_type,
            "magnitude_multiplier": magnitude_mult,
            "scored_at": datetime.now(UTC).isoformat(),
        }

    def execute(self, context: Any) -> Any:
        """Score, rank, and filter sports stories."""
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[sports] No stories to score")
            context.setdefault("run_stats", {})["scoring"] = {
                "input_count": 0,
                "scored_count": 0,
                "dropped_count": 0,
            }
            return context

        # Visual potential gate: drop stories that can never become good reels
        min_visual = self._thresholds.get("min_visual_potential", 0.3)
        visual_passed = []
        for s in stories:
            vp = score_visual_potential(s, "sports")
            s["visual_potential"] = vp
            if vp >= min_visual:
                visual_passed.append(s)
            else:
                logger.info("[sports] Visual potential rejected (%.1f): %s", vp, s.get("title", "")[:60])
        stories = visual_passed

        scored = [self.score_item(s) for s in stories]
        scored.sort(key=lambda c: c["final_score"], reverse=True)

        for i, item in enumerate(scored):
            item["rank"] = i + 1

        min_score = self._thresholds.get("min_clip_score", 0.20)
        above = [c for c in scored if c["final_score"] >= min_score]
        dropped = len(scored) - len(above)

        top_n = self._thresholds.get("top_clips_per_run", 20)
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
            "[sports] Scored %d -> %d stories (dropped %d below %.2f)",
            len(stories), len(above), dropped, min_score,
        )
        return context
