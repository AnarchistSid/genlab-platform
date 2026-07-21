"""SpliceReel scoring strategy.

Scores film content with:
- 48h half-life decay (NOT 72h from original stub)
- Film lifecycle multipliers (opening_weekend 1.6x, long_tail 0.7x)
- Franchise multipliers from config (MCU 1.5x, Star_Wars 1.4x, etc.)
- Trailer drop magnitude boost
- Selective OMDb enrichment for high-scoring candidates
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genlab_core.scoring.composite_scorer import score_visual_potential
from genlab_core.strategies.base_time_based_scoring import BaseTimeBasedScoringStrategy

from .film_lifecycle import (
    FilmLifecycleStage,
    get_lifecycle_score_multiplier,
)
from .omdb_client import OMDbClient

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


class MovieScoringStrategy(BaseTimeBasedScoringStrategy):
    """Score and rank movie content with lifecycle + franchise multipliers.

    R-70 part 2 PR 5b pilot: inherits from ``BaseTimeBasedScoringStrategy``.
    The byte-identical ``__init__`` / ``_ensure_config`` / ``_score_timeliness``
    are now inherited from the second-tier base; ``_score_novelty`` is
    inherited from the narrow ``BaseScoringStrategy`` shipped in PR 5a.
    Kept per-channel: ``_score_magnitude``, ``_score_engagement_potential``,
    ``score_item`` orchestration (with lifecycle + franchise multipliers),
    and ``execute`` (with the SR-specific cross-niche filter + OMDb
    enrichment pass).
    """

    _log_prefix = "[movies]"
    _default_timeliness_half_life_hours = 48.0

    def __init__(self) -> None:
        # Base __init__ sets ``_config = _scoring = _thresholds = None``;
        # ``_ensure_config`` reads ``self._scoring_yaml_path`` lazily so
        # set it before any inherited method runs.
        super().__init__()
        logger.info("[movies] MovieScoringStrategy initialized")
        self._scoring_yaml_path = NICHE_ROOT / "config" / "scoring_weights.yaml"

    # ``_ensure_config`` and ``_score_timeliness`` now inherited from
    # ``BaseTimeBasedScoringStrategy``; ``_score_novelty`` inherited from
    # ``BaseScoringStrategy``.

    def _score_magnitude(self, item: dict) -> float:
        """Score magnitude — RT scores, trailer drops, box office news."""
        rt_score = item.get("rt_score")
        is_trailer = item.get("is_trailer_drop", False)
        is_box_office = item.get("is_box_office_news", False)

        score = 0.5  # baseline

        if rt_score is not None:
            excellent = self._scoring.get("magnitude", {}).get("rt_score_excellent", 0.90)
            if rt_score >= excellent * 100:
                score = 1.0
            elif rt_score >= self._scoring.get("magnitude", {}).get("rt_score_good", 0.75) * 100:
                score = 0.8
            else:
                score = rt_score / 100.0

        if is_trailer:
            score = max(score, 0.85)

        if is_box_office:
            score = max(score, 0.7)

        return min(1.0, score)

    def _score_engagement_potential(self, item: dict) -> float:
        """Score engagement potential based on TMDB popularity."""
        popularity = item.get("tmdb_popularity", 0)
        if popularity >= 100:
            return 1.0
        if popularity >= 50:
            return 0.8
        if popularity >= 20:
            return 0.6
        if popularity > 0:
            return 0.4
        return 0.2

    # ``_score_novelty`` inherited from BaseScoringStrategy (PR 5a) —
    # body is ``return item.get("novelty_score", 0.5)``.

    def score_item(self, item: dict) -> dict:
        """Score a single film content item."""
        self._ensure_config()

        weights = self._scoring.get("weights", {})
        scores = {
            "timeliness": self._score_timeliness(item),
            "magnitude": self._score_magnitude(item),
            "engagement_potential": self._score_engagement_potential(item),
            "novelty": self._score_novelty(item),
        }

        weighted_sum = sum(scores[dim] * weights.get(dim, 0) for dim in scores)

        # Lifecycle multiplier — from config, NOT hardcoded
        lifecycle_str = item.get("lifecycle_stage", "unknown")
        try:
            lifecycle = FilmLifecycleStage(lifecycle_str)
        except ValueError:
            lifecycle = FilmLifecycleStage.UNKNOWN
        lifecycle_mult = get_lifecycle_score_multiplier(lifecycle, self._config)

        # Franchise multiplier — from config
        franchise = item.get("franchise", "")
        franchise_mults = self._config.get("franchise_multipliers", {})
        franchise_mult = float(franchise_mults.get(franchise, franchise_mults.get("default", 1.0)))

        final_score = weighted_sum * lifecycle_mult * franchise_mult

        return {
            **item,
            "scores": scores,
            "score": round(final_score, 4),
            "final_score": round(final_score, 4),
            # 2026-07-21: mirror final_score into composite_score (shared
            # auto_approval_gate contract). Same class-of-bug as gaming +
            # sports. Agent 2 investigation.
            "composite_score": round(final_score, 4),
            "lifecycle_multiplier": lifecycle_mult,
            "franchise_multiplier": franchise_mult,
            "scored_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _is_wrong_niche(story: dict) -> bool:
        """Reject stories that belong to gaming/tech niches, not movies."""
        title = (story.get("title", "") + " " + story.get("summary", "")).lower()
        # Gaming keywords that indicate wrong niche
        gaming_signals = [
            "marvel rivals",
            "fortnite",
            "call of duty",
            "cod ",
            "gta ",
            "overwatch",
            "valorant",
            "league of legends",
            "apex legends",
            "nintendo switch",
            "playstation",
            "xbox",
            "steam deck",
            "esports",
            "e-sports",
            "game pass",
            "patch notes",
            "nerf ",
            "buff ",
            "season pass",
            "battle royale",
            "video game",
        ]
        movie_rescue = ["movie", "film", "trailer", "box office", "cinema", "director"]
        if any(g in title for g in gaming_signals):
            if not any(m in title for m in movie_rescue):
                return True
        return False

    def execute(self, context: Any) -> Any:
        """Score, rank, and filter movie stories."""
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[movies] No stories to score")
            context.setdefault("run_stats", {})["scoring"] = {
                "input_count": 0,
                "scored_count": 0,
                "dropped_count": 0,
            }
            return context

        # Filter out cross-niche contamination (gaming/tech in movie feeds)
        pre_filter = len(stories)
        stories = [s for s in stories if not self._is_wrong_niche(s)]
        niche_rejected = pre_filter - len(stories)
        if niche_rejected:
            logger.info("[movies] Rejected %d cross-niche stories", niche_rejected)

        # Visual potential gate
        min_visual = self._thresholds.get("min_visual_potential", 0.3)
        visual_passed = []
        for s in stories:
            vp = score_visual_potential(s, "movies")
            s["visual_potential"] = vp
            if vp >= min_visual:
                visual_passed.append(s)
            else:
                logger.info(
                    "[movies] Visual potential rejected (%.1f): %s", vp, s.get("title", "")[:60]
                )
        stories = visual_passed

        scored = [self.score_item(s) for s in stories]

        # OMDb enrichment — selective, only for high-scoring candidates
        pre_enrich_threshold = self._thresholds.get("pre_enrich_threshold", 0.45)
        enriched_count = self._enrich_with_omdb(scored, pre_enrich_threshold)

        scored.sort(key=lambda c: c["final_score"], reverse=True)

        for i, item in enumerate(scored):
            item["rank"] = i + 1

        min_score = self._thresholds.get("min_score", 0.40)
        above = [c for c in scored if c["final_score"] >= min_score]
        dropped = len(scored) - len(above)

        top_n = self._thresholds.get("top_items_per_run", 5)
        if len(above) > top_n:
            # Video-first: trending video stories must survive the top-N cut.
            # Under video_gate: require, RSS stories without video can never
            # become reels — so prioritise stories that already have clips.
            video_stories = [s for s in above if s.get("_trending_video")]
            rest = [s for s in above if not s.get("_trending_video")]
            above = (video_stories + rest)[:top_n]

        context["stories"] = above
        context.setdefault("run_stats", {})["scoring"] = {
            "input_count": pre_filter,
            "niche_rejected": niche_rejected,
            "scored_count": len(above),
            "dropped_count": dropped,
            "top_score": above[0]["final_score"] if above else 0,
            "omdb_enriched": enriched_count,
        }

        logger.info(
            "[movies] Scored %d -> %d stories (dropped %d below %.2f, enriched %d via OMDb)",
            len(stories),
            len(above),
            dropped,
            min_score,
            enriched_count,
        )
        return context

    def _enrich_with_omdb(self, scored: list[dict], threshold: float) -> int:
        """Enrich high-scoring items with OMDb data and re-score magnitude."""
        omdb_key = os.environ.get("OMDB_API_KEY", "")
        if not omdb_key or omdb_key.startswith("${"):
            logger.debug("[score] OMDB_API_KEY not set — skipping enrichment")
            return 0

        candidates = [s for s in scored if s["final_score"] >= threshold and s.get("film_title")]
        if not candidates:
            return 0

        enriched = 0
        with OMDbClient(api_key=omdb_key) as client:
            for item in candidates:
                # Skip if already has RT score from TMDB
                if item.get("rt_score") is not None:
                    continue

                year = None
                release_str = item.get("release_date", "")
                if release_str and len(release_str) >= 4:
                    try:
                        year = int(release_str[:4])
                    except ValueError:
                        pass

                result = client.enrich_by_title(item["film_title"], year=year)
                if result is None:
                    continue

                # Update item metadata
                if result.rt_score is not None:
                    item["rt_score"] = result.rt_score
                if result.imdb_rating is not None:
                    item["imdb_score"] = result.imdb_rating
                if result.metacritic_score is not None:
                    item["metacritic_score"] = result.metacritic_score

                # Re-score magnitude with new RT data and re-derive final_score
                new_magnitude = self._score_magnitude(item)
                item["scores"]["magnitude"] = new_magnitude

                weights = self._scoring.get("weights", {})
                weighted_sum = sum(
                    item["scores"][dim] * weights.get(dim, 0) for dim in item["scores"]
                )
                final = weighted_sum * item["lifecycle_multiplier"] * item["franchise_multiplier"]
                item["score"] = round(final, 4)
                item["final_score"] = round(final, 4)
                item["omdb_enriched"] = True
                enriched += 1

                logger.info(
                    "[score] OMDb enriched '%s': RT=%s IMDB=%s",
                    item["film_title"],
                    result.rt_score,
                    result.imdb_rating,
                )

        return enriched
