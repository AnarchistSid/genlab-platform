"""Pipeline stage: Score clips across 7 weighted dimensions.

Runs AFTER FILTER_STORIES, BEFORE ENRICH_STORIES. Implements the
genlab-core ScoringStrategy interface.

Scores each clip across 7 dimensions:
  1. virality_potential (0.22) — normalized views + engagement rate
  2. entertainment_value (0.18) — audio energy variance + duration sweet spot
  3. recency (0.13) — exponential decay with 48hr half-life
  4. source_authority (0.13) — platform weights
  5. production_quality (0.09) — resolution, fps, 9:16 bonus
  6. chat_excitement (0.13) — Twitch chat hype analysis (5 sub-signals)
  7. highlight_detection (0.12) — Crispy neural net kill/highlight detection

Applies publisher tier multipliers (GREEN=1.0, YELLOW=0.8, RED=0.0).
Drops clips below min_clip_score threshold.

Usage:
    stage = ScoreGamingClips()
    context = stage.execute(context)
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from genlab_core.strategies import ScoringStrategy
from niches.gaming.tools.audio_analyzer import get_energy_variance
from niches.gaming.tools.chat_excitement_scorer import compute_excitement

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
NICHE_ROOT = Path(__file__).resolve().parent.parent

# Energy variance ceiling for normalization (empirically tuned)
ENERGY_VARIANCE_CEILING = 0.10

# Tier multipliers
TIER_MULTIPLIERS = {
    "GREEN": 1.0,
    "YELLOW": 0.8,
    "RED": 0.0,
    "UNKNOWN": 0.8,
}


def _load_yaml(path: Path) -> dict:
    """Load a YAML config file."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Individual scoring functions
# ---------------------------------------------------------------------------


def score_virality(clip: dict, scoring_config: dict) -> float:
    """Score virality potential based on view count and engagement rate."""
    view_count = clip.get("view_count", 0)
    like_count = clip.get("like_count", 0)
    comment_count = clip.get("comment_count", 0)
    platform = clip.get("source_platform") or clip.get("source", "")

    if view_count == 0:
        return 0.0

    baselines = scoring_config.get("virality", {}).get("view_count_baseline", {})
    baseline = baselines.get(platform, 1000)

    view_score = view_count / baseline
    engagement_rate = (like_count + comment_count) / max(view_count, 1)
    engagement_score = min(1.0, engagement_rate / 0.05)

    return min(1.0, view_score * 0.7 + engagement_score * 0.3)


def score_entertainment(clip: dict, scoring_config: dict) -> float:
    """Score entertainment value based on duration sweet spot and audio energy."""
    ent_config = scoring_config.get("entertainment", {})
    min_dur = ent_config.get("min_duration_seconds", 5)
    max_dur = ent_config.get("max_duration_seconds", 60)
    pref_min = ent_config.get("preferred_min_seconds", 15)
    pref_max = ent_config.get("preferred_max_seconds", 45)

    duration = clip.get("duration_seconds", 0)

    if duration < min_dur or duration > max_dur:
        duration_score = 0.0
    elif pref_min <= duration <= pref_max:
        duration_score = 1.0
    elif duration < pref_min:
        duration_score = (duration - min_dur) / (pref_min - min_dur)
    else:
        duration_score = (max_dur - duration) / (max_dur - pref_max)

    local_path = clip.get("local_path")
    energy_var = None
    if local_path:
        energy_var = get_energy_variance(local_path)

    if energy_var is not None:
        energy_score = min(1.0, energy_var / ENERGY_VARIANCE_CEILING)
        return energy_score * 0.5 + duration_score * 0.5
    else:
        return duration_score


def score_recency(clip: dict, scoring_config: dict) -> float:
    """Score recency using exponential decay with configurable half-life."""
    half_life = scoring_config.get("recency", {}).get("half_life_hours", 48)
    fetched_at_str = clip.get("fetched_at", "")

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


def score_source_authority(clip: dict, scoring_config: dict) -> float:
    """Score based on platform authority weight."""
    platform = clip.get("source_platform") or clip.get("source", "")
    weights = scoring_config.get("source_authority", {}).get("platform_weights", {})
    return weights.get(platform, 0.0)


def score_production_quality(clip: dict, scoring_config: dict) -> float:
    """Score production quality: resolution, fps, vertical bonus."""
    prod_config = scoring_config.get("production", {})
    height = clip.get("resolution_height", 0)
    width = clip.get("resolution_width", 0)
    fps = clip.get("fps", 0.0)

    if height == 0 and fps == 0.0:
        return 0.0

    if height >= 1080:
        res_score = 1.0
    elif height >= 720:
        res_score = 0.7
    elif height >= 480:
        res_score = 0.4
    else:
        res_score = 0.0

    preferred_fps = prod_config.get("preferred_fps", 60)
    if fps >= preferred_fps:
        fps_score = 1.0
    elif fps >= 30:
        fps_score = 0.6
    elif fps >= 24:
        fps_score = 0.4
    else:
        fps_score = 0.0

    vertical_bonus_value = prod_config.get("native_vertical_bonus", 0.10)
    is_vertical = height > width > 0
    vert_bonus = vertical_bonus_value if is_vertical else 0.0

    base_score = res_score * 0.5 + fps_score * 0.3
    return min(1.0, (base_score + vert_bonus) / 0.8)


def get_publisher_tier(
    game_title: str,
    game_registry: dict,
) -> str:
    """Look up publisher tier for a game title from game_registry.yaml."""
    for _game_key, game_info in game_registry.get("games", {}).items():
        if game_info.get("display_name", "").lower() == game_title.lower():
            tier = game_info.get("publisher_tier")
            if tier:
                return tier
    return "UNKNOWN"


def apply_tier_multiplier(score: float, tier: str) -> float:
    """Apply publisher tier multiplier to a final score."""
    multiplier = TIER_MULTIPLIERS.get(tier, TIER_MULTIPLIERS["UNKNOWN"])
    return score * multiplier


# ---------------------------------------------------------------------------
# Stage class
# ---------------------------------------------------------------------------


class ScoreGamingClips(ScoringStrategy):
    """Score and rank gaming clips across 7 weighted dimensions.

    Implements genlab-core ScoringStrategy. Loads config from YAML,
    scores each clip, applies publisher tier multipliers, drops clips
    below threshold, and validates output against schema.
    """

    def __init__(self) -> None:
        self._scoring_config: dict | None = None
        self._game_registry: dict | None = None
        self._weights: dict | None = None
        self._thresholds: dict | None = None
        self._crispy_scorer = None

    def _ensure_config(self) -> None:
        """Lazy-load config files."""
        if self._scoring_config is not None:
            return

        config_dir = NICHE_ROOT / "config"
        full_config = _load_yaml(config_dir / "scoring_weights.yaml")
        self._scoring_config = full_config.get("clip_scoring", {})
        self._weights = self._scoring_config.get("weights", {})
        self._thresholds = full_config.get("thresholds", {})
        self._game_registry = _load_yaml(config_dir / "game_registry.yaml")

        # Try to initialize CrispyScorer
        crispy_config_path = config_dir / "crispy_models.yaml"
        if crispy_config_path.exists():
            try:
                from niches.gaming.tools.crispy_scorer import CrispyScorer

                crispy_config = _load_yaml(crispy_config_path)
                self._crispy_scorer = CrispyScorer(crispy_config, str(PROJECT_ROOT))
            except ImportError:
                logger.warning("CrispyScorer dependencies not available (cv2/scipy)")

    def _score_highlight_detection(self, clip: dict) -> float:
        """Score highlight detection using CrispyScorer if available."""
        fallback = 0.5

        if self._crispy_scorer is None:
            return fallback
        if not self._crispy_scorer.is_enabled():
            return fallback

        local_path = clip.get("local_path")
        if not local_path:
            return fallback

        game_title = clip.get("game_title", "")
        return self._crispy_scorer.score_video(local_path, game_title)

    def _score_clip(self, clip: dict, emote_config: dict) -> dict:
        """Score a single clip across all 7 dimensions."""
        chat_data = clip.get("chat_data")
        chat_config = self._scoring_config.get("chat_excitement", {})
        chat_score = compute_excitement(chat_data, chat_config, emote_config)

        highlight_score = self._score_highlight_detection(clip)

        scores = {
            "virality_potential": score_virality(clip, self._scoring_config),
            "entertainment_value": score_entertainment(clip, self._scoring_config),
            "recency": score_recency(clip, self._scoring_config),
            "source_authority": score_source_authority(clip, self._scoring_config),
            "production_quality": score_production_quality(clip, self._scoring_config),
            "chat_excitement": chat_score,
            "highlight_detection": highlight_score,
        }

        weighted_sum = sum(scores[dim] * self._weights.get(dim, 0) for dim in scores)

        game_title = clip.get("game_title", "")
        tier = get_publisher_tier(game_title, self._game_registry)
        final_score = apply_tier_multiplier(weighted_sum, tier)

        now = datetime.now(UTC).isoformat()

        return {
            **clip,
            "scores": scores,
            "publisher_tier": tier,
            "final_score": round(final_score, 4),
            "scored_at": now,
        }

    def _apply_median_fallback(
        self,
        scored: list[dict],
        clips: list[dict],
    ) -> None:
        """Apply median fallback for chat_excitement and highlight_detection.

        Clips without real data get sentinel values (0.0 for chat, 0.5 for
        highlight). After first pass, compute median of real scores and
        backfill sentinels with that median, then recompute final scores.
        """
        # Chat excitement median fallback
        chat_weight = self._weights.get("chat_excitement", 0.0)
        if chat_weight > 0:
            real_chat_scores = [
                c["scores"]["chat_excitement"]
                for c in scored
                if c["scores"]["chat_excitement"] > 0.0
            ]
            median_chat = statistics.median(real_chat_scores) if real_chat_scores else 0.5

            for c in scored:
                if c["scores"]["chat_excitement"] == 0.0:
                    c["scores"]["chat_excitement"] = median_chat
                    self._recompute_final_score(c)

        # Highlight detection median fallback
        hl_weight = self._weights.get("highlight_detection", 0.0)
        if hl_weight > 0:
            real_hl_scores = []
            fallback_indices = []
            for i, (c, orig) in enumerate(zip(scored, clips, strict=False)):
                had_local = orig.get("local_path") is not None
                has_crispy = self._crispy_scorer is not None and (
                    hasattr(self._crispy_scorer, "is_enabled") and self._crispy_scorer.is_enabled()
                )
                if had_local and has_crispy:
                    real_hl_scores.append(c["scores"]["highlight_detection"])
                else:
                    fallback_indices.append(i)

            if real_hl_scores and fallback_indices:
                median_hl = statistics.median(real_hl_scores)
                for idx in fallback_indices:
                    scored[idx]["scores"]["highlight_detection"] = median_hl
                    self._recompute_final_score(scored[idx])

    def _recompute_final_score(self, clip_result: dict) -> None:
        """Recompute weighted sum + final score after a fallback substitution."""
        weighted_sum = sum(
            clip_result["scores"][dim] * self._weights.get(dim, 0) for dim in clip_result["scores"]
        )
        tier = clip_result["publisher_tier"]
        clip_result["final_score"] = round(
            apply_tier_multiplier(weighted_sum, tier),
            4,
        )

    def _validate_schema(self, scored_clips: list[dict]) -> None:
        """Validate scored clips against scored_clip.schema.json."""
        schema_path = NICHE_ROOT / "schemas" / "scored_clip.schema.json"
        if not schema_path.exists():
            logger.warning("Schema file not found: %s", schema_path)
            return

        try:
            import jsonschema

            with open(schema_path) as f:
                schema = json.load(f)

            for clip in scored_clips:
                jsonschema.validate(clip, schema)
        except ImportError:
            logger.debug("jsonschema not installed — skipping validation")
        except jsonschema.ValidationError as e:
            logger.warning("Schema validation failed: %s", e.message)

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Score, rank, and filter clips.

        Reads clips from context["stories"], scores each across 7 dimensions,
        applies median fallback, drops below-threshold clips, sorts by score,
        and writes back to context["stories"].
        """
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[SCORE] No stories to score")
            context.setdefault("run_stats", {})["scoring"] = {
                "input_count": 0,
                "scored_count": 0,
                "dropped_count": 0,
            }
            return context

        # Load emote config if available
        emote_config_path = NICHE_ROOT / "config" / "chat_emotes.yaml"
        emote_config = _load_yaml(emote_config_path) if emote_config_path.exists() else {}

        # Score all clips
        scored = [self._score_clip(clip, emote_config) for clip in stories]

        # Apply median fallback for chat and highlight dimensions
        self._apply_median_fallback(scored, stories)

        # Sort by final_score descending
        scored.sort(key=lambda c: c["final_score"], reverse=True)

        # Assign ranks
        for i, clip in enumerate(scored):
            clip["rank"] = i + 1

        # Drop below threshold
        min_score = self._thresholds.get("min_clip_score", 0.25)
        above = [c for c in scored if c["final_score"] >= min_score]
        dropped = len(scored) - len(above)

        if dropped > 0:
            logger.info(
                "[SCORE] Dropped %d clips below min_clip_score=%.2f",
                dropped,
                min_score,
            )

        # Respect top_clips_per_run limit
        top_n = self._thresholds.get("top_clips_per_run", 20)
        if len(above) > top_n:
            # Video-first: trending video stories must survive the top-N cut.
            video_stories = [s for s in above if s.get("_trending_video")]
            rest = [s for s in above if not s.get("_trending_video")]
            above = (video_stories + rest)[:top_n]
            logger.info("[SCORE] Capped to top %d clips", top_n)

        # Validate against schema
        self._validate_schema(above)

        # Build stats
        tier_dist: dict[str, int] = {}
        for c in above:
            t = c["publisher_tier"]
            tier_dist[t] = tier_dist.get(t, 0) + 1

        context["stories"] = above
        context.setdefault("run_stats", {})["scoring"] = {
            "input_count": len(stories),
            "scored_count": len(above),
            "dropped_count": dropped,
            "top_score": above[0]["final_score"] if above else 0,
            "bottom_score": above[-1]["final_score"] if above else 0,
            "tier_distribution": tier_dist,
        }

        logger.info(
            "[SCORE] %d -> %d clips (dropped %d below %.2f, tier dist: %s)",
            len(stories),
            len(above),
            dropped,
            min_score,
            tier_dist,
        )
        return context
