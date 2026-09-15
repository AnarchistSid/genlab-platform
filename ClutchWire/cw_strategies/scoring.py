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
from genlab_core.strategies.base_scoring import BaseScoringStrategy

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class SportScoringStrategy(BaseScoringStrategy):
    """Score and rank sports content with magnitude + live + upset/record multipliers.

    R-70 part 2 PR 5c: inherits from the **narrow** ``BaseScoringStrategy``
    (NOT the SR + FD second-tier base). Sports uses a different scoring
    axis set (``recency`` + ``community_signal`` instead of
    ``timeliness`` + ``engagement_potential``) and a different
    ``scoring_weights.yaml`` shape (``clip_scoring`` sub-key,
    ``_weights`` + ``_multipliers`` instance attrs instead of
    ``_scoring`` / ``_thresholds``). The only base body that lifts
    is ``_score_novelty`` — the byte-identical 1-line method.
    """

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

    # ``_score_novelty`` inherited from BaseScoringStrategy (PR 5a) —
    # body is ``return item.get("novelty_score", 0.5)``.

    def score_item(self, item: dict) -> dict:
        """Score a single sports content item across 4 dimensions."""
        self._ensure_config()

        scores = {
            "recency": self._score_recency(item),
            "community_signal": self._score_community_signal(item),
            "magnitude": self._score_magnitude(item),
            "novelty": self._score_novelty(item),
        }

        weighted_sum = sum(scores[dim] * self._weights.get(dim, 0) for dim in scores)

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
            # 2026-07-21: mirror final_score into composite_score so
            # shared auto_approval_gate reads a real value instead of
            # falling back to 0.5 neutral. Same class-of-bug as gaming
            # ScoreGamingClips (fixed same commit). Agent 2 investigation.
            "composite_score": round(final_score, 4),
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
                logger.info(
                    "[sports] Visual potential rejected (%.1f): %s", vp, s.get("title", "")[:60]
                )
        stories = visual_passed

        # PUBLISH-03 §1: dump what a YouTube-sourced story actually carries at
        # THIS boundary. On 2026-09-14, 13 YouTube highlights passed relevance
        # and the quality gate and won none of the five slots, and no blueprint
        # existed from them to inspect afterwards — so the only place the two
        # fields can be observed is here, on the way in.
        # `is_highlight` drives the highlight-first cut; `like_count` drives
        # CompositeScorer's engagement term (absent -> engagement floors at 0.5
        # and the composite pins at a constant, measured 0.4823 across the set).
        for _s in stories:
            _src = str(_s.get("source") or "")
            if "youtube" not in _src.lower() and "channel" not in str(
                _s.get("search_query") or ""
            ).lower():
                continue
            logger.info(
                "[sports][boundary] source=%s search_query=%s is_highlight=%r "
                "like_count=%r view_count=%r title=%.40s",
                _src or "(none)",
                _s.get("search_query") or "(none)",
                _s.get("is_highlight"),
                _s.get("like_count"),
                _s.get("view_count"),
                str(_s.get("title") or ""),
            )

        scored = [self.score_item(s) for s in stories]
        scored.sort(key=lambda c: c["final_score"], reverse=True)

        for i, item in enumerate(scored):
            item["rank"] = i + 1

        # 2026-07-14 fix: bypass min_clip_score for trending-video stories.
        # The sports scoring dimensions (recency, community_signal, magnitude,
        # novelty) assume ESPN-format metadata (game_type, is_live, is_upset,
        # is_record) which content_pool YouTube trending stories DO NOT have —
        # so they all score near 0 and get filtered by min_clip_score=0.30.
        # But content_pool stories ALREADY passed the routing niche_score gate
        # at 0.30+ (that's what routed them to 'sports' in the first place),
        # so re-filtering here is a double-gate that starves the pipeline.
        #
        # Sports has had 0 blueprints for 2+ consecutive days as of today —
        # this fix restores the flow. RSS stories (ESPN etc.) still respect
        # the threshold since their fields are populated.
        min_score = self._thresholds.get("min_clip_score", 0.20)
        above = [c for c in scored if c.get("_trending_video") or c["final_score"] >= min_score]
        dropped = len(scored) - len(above)
        trending_bypassed = sum(
            1 for c in scored if c.get("_trending_video") and c["final_score"] < min_score
        )

        top_n = self._thresholds.get("top_clips_per_run", 20)
        # SOURCE-04 (2026-09-14): HIGHLIGHT-first, not video-first.
        #
        # The video-first cut below was a no-op. `_trending_video` means "has a
        # video" and is set by six fetchers — reddit, tmdb, twitch, steam,
        # anime_promos, backfill — so every candidate landed in `video_stories`
        # and the order fell back to `final_score`. Reddit discussion posts
        # outscore YouTube highlights on the sports dimensions (which assume
        # ESPN metadata highlights lack), so Reddit took all five slots from
        # July onward. Measured: youtube_trending 189/month in May -> 5 in
        # September, while the fetcher still returns ~25 action clips per run.
        #
        # `is_highlight` is a declared content claim set only by sources that
        # know they carry highlights (subscribed league RSS today, ScoreBat when
        # it returns). `highlight_slot_min` reserves slots for them so a
        # genuinely hot discussion post can still take one, but cannot take all.
        highlight_min = int(self._thresholds.get("highlight_slot_min", 0) or 0)
        highlights = [s for s in above if s.get("is_highlight")]
        others = [s for s in above if not s.get("is_highlight")]
        if highlight_min > 0 and highlights:
            reserved = highlights[: min(highlight_min, top_n)]
            filler = [s for s in (others + highlights[len(reserved) :])]
            above = (reserved + filler)[:top_n]
        elif len(above) > top_n:
            # No highlight sources present (or the knob is off): preserve the
            # previous video-first behaviour so the other four niches are
            # unchanged by this commit.
            video_stories = [s for s in above if s.get("_trending_video")]
            rest = [s for s in above if not s.get("_trending_video")]
            above = (video_stories + rest)[:top_n]

        highlights_selected = sum(1 for s in above if s.get("is_highlight"))

        context["stories"] = above
        context.setdefault("run_stats", {})["scoring"] = {
            "input_count": len(stories),
            "scored_count": len(above),
            "dropped_count": dropped,
            "trending_bypassed": trending_bypassed,
            # SOURCE-04: `trending_bypassed` alone cannot distinguish "none
            # needed the bypass" from "none were recognised" — it emits 0 for
            # both. These two make the denominator visible.
            "trending_recognised": sum(1 for c in scored if c.get("_trending_video")),
            "highlights_recognised": sum(1 for c in scored if c.get("is_highlight")),
            "highlights_selected": highlights_selected,
            "top_score": above[0]["final_score"] if above else 0,
        }

        logger.info(
            # SOURCE-04/PUBLISH-03: the denominators belong HERE, not only in
            # run_stats. `trending_bypassed` emits 0 both when nothing needed
            # the bypass and when nothing was recognised — indistinguishable
            # without a count of what WAS recognised. On 2026-09-14 the sports
            # fire printed "0 bypassed", 13 YouTube highlights had passed
            # relevance and the quality gate, and all five slots still went to
            # Reddit. The counter that would have said why was in run_stats,
            # which nobody reads at 00:30.
            "[sports] Scored %d -> %d stories (dropped %d below %.2f; "
            "trending_recognised=%d bypassed=%d | highlights_recognised=%d "
            "selected=%d of top_n=%d, slot_min=%d)",
            len(stories),
            len(above),
            dropped,
            min_score,
            sum(1 for c in scored if c.get("_trending_video")),
            trending_bypassed,
            sum(1 for c in scored if c.get("is_highlight")),
            highlights_selected,
            top_n,
            highlight_min,
        )
        return context
