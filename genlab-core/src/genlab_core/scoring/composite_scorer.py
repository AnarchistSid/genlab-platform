"""Composite quality-gate scorer for trending video content.

Ensures only the highest-rated content per channel per day reaches
the publishing queue. Videos must clear a per-niche composite score
threshold or they are filtered out before blueprint creation.

Formula::

    velocity_score    = min(view_velocity / velocity_threshold, 1.0)
    engagement_score  = min(like_count/view_count / target_like_ratio, 1.0)
    engagement_factor = ENGAGEMENT_FLOOR + (1 - ENGAGEMENT_FLOOR) * engagement_score
    composite         = velocity_score × trend_multiplier × niche_relevance
                        × engagement_factor

Where:
    - velocity_score:    normalised views/hour against niche baseline (reach/recency)
    - engagement_factor: rewards genuine virality (like/view ratio) so a clip that
      people actually engaged with outranks a raw view-spike or official-account
      promo (R-?? selection-quality). Neutral (1.0) when engagement data is
      absent, so un-enriched/RSS candidates aren't penalised for missing metrics.
    - trend_multiplier:  Google Trends position multiplier (1.0–3.0)
    - niche_relevance:   binary 1.0 if video matches niche, else 0.0

A video is also rejected outright if it reports a KNOWN view_count below the
per-niche ``min_view_count`` floor — an absolute-reach gate that catches
recency-gamed clips (e.g. 200 views in the first hour → high velocity, trivial
reach). A view_count of 0/absent is treated as "unknown" and not floored.

Usage::

    scorer = CompositeScorer("gaming")
    scored = scorer.score_and_rank(videos)
    # scored contains only videos above the niche threshold, sorted DESC
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from genlab_core.config.tuning import get_tuning_config

logger = logging.getLogger(__name__)

# Per-niche velocity thresholds (views/hour).
# A video's view_velocity is normalised against this baseline.
# These are intentionally higher than the fetch-stage MIN_VIEW_VELOCITY
# thresholds — fetch casts a wide net, this gate narrows to publishable.
DEFAULT_VELOCITY_THRESHOLDS: dict[str, float] = {
    "gaming": 1500.0,
    "sports": 2000.0,
    "movies": 800.0,
    "anime": 600.0,
    "ai_creators": 400.0,
}

# Minimum composite score to pass the quality gate.
# Videos below this are filtered out of score_and_rank().
DEFAULT_MIN_COMPOSITE: dict[str, float] = {
    "gaming": 0.35,
    "sports": 0.35,
    "movies": 0.30,
    "anime": 0.30,
    "ai_creators": 0.25,
}

# Composite-scoring calibration is loaded once from the platform-wide tuning
# config (genlab-core/config/tuning.yaml). Module-level aliases below
# preserve the previous public surface for callers + tests; the values
# themselves are now ops-editable without a code change. See
# genlab_core/config/tuning.py for the schema and migration plan.
_TUNING = get_tuning_config().composite_scoring

# Absolute view-count floor per niche. A video reporting a KNOWN view_count
# below this is rejected regardless of velocity — it catches recency-gamed
# spikes. 0/absent view_count = "unknown" and is NOT floored, so un-enriched
# candidates fall back to velocity-only ranking.
DEFAULT_MIN_VIEW_COUNT: dict[str, int] = _TUNING.default_min_view_count

# like/view ratio at which engagement_score saturates to 1.0. ~3% is a healthy
# viral clip (YouTube average is ~1–2%); above this adds no further boost.
_TARGET_LIKE_RATIO: float = _TUNING.target_like_ratio

# Floor of the engagement multiplier: a zero-engagement clip still keeps this
# fraction of its composite (velocity remains a real reach signal), while a
# high-engagement clip keeps the full score. So engagement RE-RANKS toward
# genuinely-engaging clips without hard-filtering on it.
_ENGAGEMENT_FLOOR: float = _TUNING.engagement_floor


# 2026-08-12: source_reach_multiplier — per-niche per-source calibration
# from observed publish-side reach. Motivating investigation: composite_score
# had Pearson r=-0.44 (log r=-0.75) against anime/facebook reach over 30d
# because velocity_score measures SOURCE-PLATFORM virality (YouTube view
# velocity) but doesn't predict DESTINATION-PLATFORM reach (Facebook).
#
# Data per source × platform (n=5-7 each, 30d anime window):
#
#   source           facebook   instagram   threads   youtube
#   ---------------  --------   ---------   -------   -------
#   anilist            695         134         51        4
#   youtube_trending     4          23          8        0
#
# Blended reach ratio (mean-across-4-platforms vs platform-baseline)
# per source, applied as a multiplier to composite_score at score time.
# Only niches with strong signal are populated; everything else defaults
# to 1.0 (no effect). Refit periodically as more data accumulates.
#
# Conservative bounds: no boost > 2.0x, no penalty < 0.4x. Even with
# strong signal, small sample sizes (n<10 per cell) mean the point
# estimates are noisy — the multiplier should nudge, not overwrite.
_SOURCE_REACH_MULTIPLIER: dict[str, dict[str, float]] = {
    "anime": {
        # anilist wins across all 4 target platforms (5x more Facebook
        # reach than the mean; only source with n>3 hits >500 views).
        "anilist": 1.5,
        # youtube_trending: 90% of anime posts, avg 15 views across
        # non-Threads platforms. Not zero-value (Threads works OK) —
        # nudge down, don't kill.
        "youtube_trending": 0.6,
    },
}


def _source_reach_multiplier(niche_id: str, source: str) -> float:
    """Look up per-(niche, source) reach multiplier. Returns 1.0 (no
    effect) when the cell is unpopulated — safe default keeps the
    existing scoring behavior for niches without calibration data."""
    return _SOURCE_REACH_MULTIPLIER.get(niche_id, {}).get(source, 1.0)


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
    # Engagement multiplier applied to the composite (ENGAGEMENT_FLOOR..1.0).
    # Defaults to 1.0 (neutral) so callers/tests that don't supply engagement
    # data are unaffected.
    engagement_score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "view_velocity": round(self.view_velocity, 1),
            "velocity_score": round(self.velocity_score, 3),
            "trend_multiplier": round(self.trend_multiplier, 2),
            "niche_relevance": round(self.niche_relevance, 2),
            "engagement_score": round(self.engagement_score, 3),
            "composite": round(self.composite, 4),
            "passed": self.passed,
        }


class CompositeScorer:
    """Score and filter trending videos for a specific niche.

    Args:
        niche_id: Channel niche identifier (gaming, sports, movies, anime, ai_creators).
        velocity_threshold: Override per-niche velocity baseline.
            If None, uses DEFAULT_VELOCITY_THRESHOLDS.
        min_composite: Override minimum composite score to pass gate.
            If None, uses DEFAULT_MIN_COMPOSITE.
    """

    def __init__(
        self,
        niche_id: str,
        velocity_threshold: float | None = None,
        min_composite: float | None = None,
        min_view_count: int | None = None,
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
        self.min_view_count = (
            min_view_count
            if min_view_count is not None
            else DEFAULT_MIN_VIEW_COUNT.get(niche_id, 2000)
        )

    def score(
        self,
        video: dict[str, Any],
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
        velocity_score = (
            min(view_velocity / self.velocity_threshold, 1.0)
            if self.velocity_threshold > 0
            else 0.0
        )

        # Clamp inputs to valid ranges
        trend_mult = max(0.0, min(float(trend_multiplier), 3.0))
        relevance = 1.0 if float(niche_relevance) > 0 else 0.0

        # Engagement: like/view ratio as a virality proxy. Neutral (1.0) when no
        # engagement data is present, so un-enriched candidates aren't penalised.
        view_count = int(video.get("view_count", 0) or 0)
        like_count = int(video.get("like_count", 0) or 0)
        if view_count > 0:
            like_ratio = like_count / view_count
            engagement_score = min(like_ratio / _TARGET_LIKE_RATIO, 1.0)
            engagement_factor = _ENGAGEMENT_FLOOR + (1.0 - _ENGAGEMENT_FLOOR) * engagement_score
        else:
            engagement_factor = 1.0

        # 2026-08-12: per-source reach calibration. Only nudges when
        # the (niche, source) cell has strong observed signal
        # (currently anime.anilist=1.5x, anime.youtube_trending=0.6x).
        # All other cells return 1.0 = no effect.
        source_mult = _source_reach_multiplier(
            self.niche_id, str(video.get("source", ""))
        )

        composite = velocity_score * trend_mult * relevance * engagement_factor * source_mult

        # Absolute-reach gate: reject a KNOWN-low view_count regardless of score.
        passed = composite >= self.min_composite
        if 0 < view_count < self.min_view_count:
            passed = False

        return VideoScore(
            video_id=str(video.get("video_id", "")),
            title=str(video.get("title", "")),
            view_velocity=view_velocity,
            velocity_score=velocity_score,
            trend_multiplier=trend_mult,
            niche_relevance=relevance,
            composite=composite,
            passed=passed,
            engagement_score=round(engagement_factor, 3),
        )

    def score_and_rank(
        self,
        videos: list[dict[str, Any]],
        trend_multipliers: dict[str, float] | None = None,
        niche_relevances: dict[str, float] | None = None,
    ) -> list[VideoScore]:
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

        scored: list[VideoScore] = []
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
                self.niche_id,
                len(passed),
                len(scored),
                self.min_composite,
            )

        if not passed:
            logger.warning(
                "[CompositeScorer:%s] No videos passed quality gate — "
                "0/%d above %.2f composite threshold",
                self.niche_id,
                len(scored),
                self.min_composite,
            )

        passed.sort(key=lambda s: s.composite, reverse=True)
        return passed


# ---------------------------------------------------------------------------
# Visual potential scoring — used by RSS-sourced stories BEFORE video sourcing
# ---------------------------------------------------------------------------

# Stories with no visual hook waste YouTube API quota and produce mismatches.
# Score 0.0–1.0; stories below the threshold are dropped before VideoSourcer.

_ZERO_VISUAL_PATTERNS = [
    "opinion:",
    "editorial:",
    "weekly releases",
    "release schedule",
    "manga releases",
    "podcast",
    "interview:",
    "analysis:",
    "doesn't need to",
    "should give up",
    "here's why",
    "the case for",
    "the case against",
    "north american releases",
    "adds digitally",
    "web novels",
    "buying guide",
    "best of 20",
]

_STRONG_VISUAL_SIGNALS: dict[str, list[str]] = {
    "sports": [
        "highlights",
        "scored",
        "dunk",
        "play",
        "win",
        "loss",
        "record",
        "comeback",
        "ejected",
        "clutch",
        "game winner",
    ],
    "gaming": [
        "gameplay",
        "clip",
        "stream",
        "tournament",
        "patch",
        "banned",
        "viral",
        "world record",
        "speedrun",
        "trailer",
    ],
    "movies": [
        "trailer",
        "clip",
        "scene",
        "teaser",
        "footage",
        "box office",
        "premiere",
        "first look",
    ],
    "anime": [
        "episode",
        "fight",
        "scene",
        "finale",
        "trailer",
        "moment",
        "animation",
        "arc",
        "premiere",
    ],
    "ai_creators": [
        "demo",
        "tool",
        "generates",
        "creates",
        "watch",
        "shows",
        "reveals",
        "launches",
    ],
}


def score_visual_potential(story: dict, niche_id: str) -> float:
    """Score 0.0–1.0 based on how likely this story has usable video footage.

    Stories scoring below 0.3 should be rejected before VideoSourcer runs.
    Prevents opinion articles, release schedules, and weekly roundups from
    entering the video pipeline and wasting API quota.

    **2026-06-18 (sports fixture detection)**: ESPN scoreboard already
    skips ``state='pre'`` (scheduled) games at fetch time. But RSS
    sources (BBC Sport, Sky Sports, Bleacher Report, The Athletic)
    don't tag game state — they publish PREVIEW articles for upcoming
    fixtures with titles like "Atletico Madrid - Athletic Bilbao" or
    "Koln - Bayer Leverkusen". These have no game-result content,
    therefore no video clip will ever exist, and they died at
    DownloadTopVideos / VideoGate for two consecutive days producing
    zero sports blueprints. Detect them HERE so the visual gate
    (min_visual_potential=0.3) zero-rejects them before they displace
    real video stories from the top-N cut.

    Fixture-title pattern: ``Team - Team`` or ``Team vs Team`` with no
    score-like digits. Match the unscored-team-vs-team shape; any
    "Final" / "Live" / digit-bearing variant skips this check.
    """
    title = (story.get("title") or "").lower()
    description = (story.get("description") or story.get("summary") or "").lower()
    text = f"{title} {description}"

    for pattern in _ZERO_VISUAL_PATTERNS:
        if pattern in text:
            logger.debug(
                "[VISUAL_SCORE] 0.0 (matched '%s'): %s",
                pattern,
                story.get("title", "")[:60],
            )
            return 0.0

    # Sports fixture-preview detection (2026-06-18). Pattern: a title
    # that's just "Team A - Team B" or "Team A vs Team B" with no
    # score digits AND no Final/Live indicators. Limited to sports —
    # other niches don't have this shape.
    #
    # **2026-06-18 follow-up**: ScoreBat publishes highlights videos
    # with the matchup AS the title ("Angers - PSG" IS the title of
    # the highlights video, not a preview article). FetchTrendingVideos
    # / FetchScoreBatHighlights both populate ``video_id`` or
    # ``download_url``. When a story has a verified video field, the
    # fixture pattern is a video TITLE, not a preview ARTICLE — skip
    # the zero-reject. The historical bug this guards: PR #320 dropped
    # ALL of the 10 ScoreBat highlights + 12 YouTube trending videos
    # in today's run because their titles all match the matchup
    # pattern.
    if (
        niche_id == "sports"
        and not _has_video_field(story)
        and _is_fixture_preview(story.get("title") or "", text)
    ):
        logger.debug(
            "[VISUAL_SCORE] 0.0 (fixture preview — no clip yet): %s",
            story.get("title", "")[:60],
        )
        return 0.0

    niche_signals = _STRONG_VISUAL_SIGNALS.get(niche_id, [])
    strong_matches = sum(1 for sig in niche_signals if sig in text)

    if strong_matches >= 2:
        return 1.0
    elif strong_matches == 1:
        return 0.7
    else:
        return 0.4  # Unknown — let through at lower priority


_VIDEO_BEARING_SOURCES = frozenset(
    {
        "scorebat",  # ScoreBat highlights — embed provided but
        # not propagated to story dict; the source name itself
        # is the marker.
        "youtube",
        "youtube_subscribed_channel",
        "youtube_channel_rss",
        "content_pool",
        "reddit_video",
        "tiktok",
        "twitch_clip",
    }
)


def _has_video_field(story: dict) -> bool:
    """True iff the story carries a video identifier OR comes from
    a known video-bearing source.

    Used by ``score_visual_potential`` to skip the fixture-preview
    check for stories that ALREADY represent a real video — those
    "Team A - Team B" titles ARE the matchup highlights video itself
    (ScoreBat, YouTube channel RSS, content_pool), not preview text
    articles.

    Two detection paths:
      1. Direct URL/ID fields (``video_id``, ``video_url``,
         ``download_url``, ``embed``) — populated by
         ``FetchTrendingVideos`` + ``FetchRedditClips``.
      2. Source-name match (``source`` or ``video_source`` field in
         ``_VIDEO_BEARING_SOURCES``) — used for sources like ScoreBat
         that intentionally don't propagate their embed URL into the
         story dict (the embed lives elsewhere and VideoGate matches
         it later).

    Either path passing → the story is video-bearing → fixture check
    must skip.
    """
    for key in ("video_id", "video_url", "download_url", "embed"):
        v = story.get(key)
        if v and isinstance(v, str) and v.strip():
            return True
    for key in ("source", "video_source"):
        v = story.get(key)
        if v and isinstance(v, str) and v.strip().lower() in _VIDEO_BEARING_SOURCES:
            return True
    return False


def _is_fixture_preview(title: str, text: str) -> bool:
    """True iff title looks like a not-yet-played sports fixture.

    Examples that match (return True):
        ``Atletico Madrid - Athletic Bilbao``
        ``Koln - Bayer Leverkusen``
        ``Lakers vs Celtics``

    Examples that DON'T match (return False — let the normal scoring
    proceed):
        ``Lakers 102, Celtics 98``                  (has scores)
        ``Lakers vs Celtics: Final``                (has Final marker)
        ``Lakers vs Celtics — LIVE updates``        (Live marker)
        ``Premier League round-up``                 (not a fixture shape)
        ``Atletico Madrid extend win streak to 8``  (has video signal)

    Designed to be cheap (regex on the title only) and conservative
    (false negatives are fine — they fall through to normal scoring).
    """
    import re

    # Result/live indicators anywhere in title or description means
    # the game has happened — don't treat as fixture preview.
    result_indicators = (
        "final",
        "live",
        "ft ",
        " ft",
        "in-progress",
        "in progress",
        "highlights",
        "recap",
        "won ",
        " won",
        "beats",
        "beat ",
        "extends",
        "comeback",
        "victory",
        "loss to",
        "defeats",
        "defeated",
    )
    text_lower = text
    if any(ind in text_lower for ind in result_indicators):
        return False

    # Score-like digits (e.g. "102-98", "2-1", "3:0") = game played.
    if re.search(r"\b\d+\s*[-:]\s*\d+\b", title):
        return False
    if re.search(r"\b\d+\s*,\s*\d+\b", title):  # "Lakers 102, Celtics 98"
        return False

    # Fixture shapes: "Team A - Team B" or "Team A vs Team B"
    # (allowing team names with spaces). The title must consist
    # ONLY of the matchup (with optional emoji/decoration before/after).
    # 80 char cap keeps the regex bounded.
    if len(title) > 80:
        return False
    fixture_re = re.compile(
        r"^[\W]*[A-Z][A-Za-zÀ-ÿ\s.&]{2,30}\s+(?:-|–|vs\.?|v\.?)\s+[A-Z][A-Za-zÀ-ÿ\s.&]{2,30}[\W]*$",
        re.IGNORECASE,
    )
    return bool(fixture_re.match(title.strip()))
