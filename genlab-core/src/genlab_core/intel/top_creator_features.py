"""Structural feature extraction for top-creator uploads (B.1, 2026-07-07).

Given a YouTube video's public metadata (title, description, tags,
publish time, view count), extract a fixed set of structural features
suitable for correlation analysis. B.2's runner will compute
feature ↔ view-velocity correlations from the top-100 per-niche
clips fetched via ``mostPopular``. B.3 will feed those correlations
into bandit arm priors as WEAK signals.

## Design honesty

* **Not causal.** Feature-view correlation is not evidence that
  a feature CAUSES views on a small channel. Top-clip signal is
  Simpson's-paradox-prone.
* **Weak prior only.** B.3 will map correlations to Beta prior
  adjustments in the range `alpha += 0..2, beta += 0..2` — an
  order of magnitude weaker than actual observed reward. The
  bandit's own observations quickly override.
* **Structural only.** Features chosen are things GenLab CAN
  actually manipulate at content-writer time:
  - title length + case
  - question-format signal
  - number/emoji density
  - description first-line hook
  - publish timing (hour + weekday)
  Features excluded because we can't control them:
  - creator channel size
  - creator subscriber growth rate
  - thumbnail A/B tests

## Function contract

`extract_features(video_meta: dict) -> VideoFeatures` — pure function.
Missing fields → sensible defaults (0 for numeric, "" for str). No
exceptions escape.

`compute_feature_view_correlation(videos: list[dict]) -> dict[str, float]`
— Spearman rank correlation of each numeric feature with view velocity
(views ÷ hours since publish). Spearman is more robust to view-count
outliers than Pearson.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# Emoji detection — matches the common Unicode emoji ranges. Not
# exhaustive (there are hundreds of blocks + variation selectors)
# but catches the ones creators actually use in titles.
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA6F☀-⛿✀-➿]")

# Number detection in titles ("Top 10", "5 things"). We count
# standalone integers of 1-4 digits; larger numbers are usually
# years or view counts (part of a viral pitch, not structure).
_NUMBER_RE = re.compile(r"\b\d{1,4}\b")

# All-caps word detection — words of 2+ letters, all uppercase.
# Signals TITLE SHOUTING (creator convention for intensity).
_ALLCAPS_WORD_RE = re.compile(r"\b[A-Z]{2,}\b")


@dataclass(frozen=True)
class VideoFeatures:
    """Structural features extracted from a video's metadata.

    All fields are the format the correlation runner (B.2) expects
    — bool coerces to int (0/1) at correlation time; strings are
    counted, not compared.
    """

    # Title
    title_length_chars: int
    title_length_words: int
    title_ends_with_question: bool
    title_starts_with_number: bool
    title_number_count: int
    title_emoji_count: int
    title_allcaps_word_count: int

    # Description
    description_length_chars: int
    description_first_line_length: int
    description_has_timestamps: bool

    # Tags
    tags_count: int

    # Publish timing (from creator's audience-optimal window POV)
    publish_hour_utc: int  # 0..23
    publish_day_of_week: int  # 0=Mon..6=Sun

    # Engagement — reference metric for correlation (view velocity)
    views: int
    hours_since_publish: float

    def view_velocity(self) -> float:
        """Views per hour since publish. Falls back to 0 to avoid
        div-by-zero for videos published in the same second we
        fetched (extremely rare)."""
        if self.hours_since_publish <= 0.01:
            return 0.0
        return self.views / self.hours_since_publish

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _detect_timestamps(description: str) -> bool:
    """Timestamps in a YouTube description signal chapter formatting
    (0:00 Intro, 1:30 First point, etc.). Creators who chapter their
    videos care about retention — this is a proxy for production
    polish."""
    return bool(re.search(r"^\d{1,2}:\d{2}", description, re.MULTILINE))


def _parse_iso_utc(iso_str: str) -> datetime:
    """Parse YouTube's ``2026-07-07T14:23:00Z`` format. Returns
    epoch UTC on failure so the caller can still compute deltas
    (they'll just be 55+ years and get filtered out by the runner)."""
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)


def extract_features(video_meta: dict[str, Any]) -> VideoFeatures:
    """Extract structural features from a single video's metadata.

    Accepts either the raw YouTube API ``videos.list`` response shape
    (with ``snippet`` + ``statistics`` sub-dicts) OR a flatter dict
    with the same top-level keys. Both formats work because the
    A.2 poll runner uses the flat form.

    Missing / malformed fields default to sensible values — never
    raises.
    """
    # Handle both shapes — each sub-dict is picked independently
    # so a partial input (only ``statistics``, only ``snippet``, or
    # neither) is handled without silently dropping fields.
    snippet = video_meta.get("snippet") or (video_meta if "snippet" not in video_meta else {})
    stats = video_meta.get("statistics") or (video_meta if "statistics" not in video_meta else {})

    title = str(snippet.get("title") or "")
    description = str(snippet.get("description") or "")
    tags = snippet.get("tags") or []
    published_at = str(snippet.get("publishedAt") or snippet.get("published_at") or "")

    # View count comes as string in raw API responses ("125431"), int elsewhere
    try:
        views = int(stats.get("viewCount") or stats.get("views") or 0)
    except (TypeError, ValueError):
        views = 0

    # Publish timing
    if published_at:
        pub_dt = _parse_iso_utc(published_at)
        hours_since = max(0.0, (datetime.now(UTC) - pub_dt).total_seconds() / 3600.0)
        publish_hour = pub_dt.hour
        publish_dow = pub_dt.weekday()
    else:
        hours_since = 0.0
        publish_hour = 0
        publish_dow = 0

    # Title analysis
    words = title.split()
    title_length_words = len(words)
    title_ends_with_q = title.rstrip().endswith("?")
    first_token = words[0] if words else ""
    title_starts_with_number = bool(re.match(r"^\d+\b", first_token))
    title_number_count = len(_NUMBER_RE.findall(title))
    title_emoji_count = len(_EMOJI_RE.findall(title))
    title_allcaps_count = len(_ALLCAPS_WORD_RE.findall(title))

    # Description analysis
    first_line = description.split("\n", 1)[0] if description else ""
    return VideoFeatures(
        title_length_chars=len(title),
        title_length_words=title_length_words,
        title_ends_with_question=title_ends_with_q,
        title_starts_with_number=title_starts_with_number,
        title_number_count=title_number_count,
        title_emoji_count=title_emoji_count,
        title_allcaps_word_count=title_allcaps_count,
        description_length_chars=len(description),
        description_first_line_length=len(first_line),
        description_has_timestamps=_detect_timestamps(description),
        tags_count=len(tags) if isinstance(tags, list) else 0,
        publish_hour_utc=publish_hour,
        publish_day_of_week=publish_dow,
        views=views,
        hours_since_publish=hours_since,
    )


# The list of numeric-typed features we compute correlations over.
# Kept as a module constant so B.2's runner iterates deterministically.
_NUMERIC_FEATURES: tuple[str, ...] = (
    "title_length_chars",
    "title_length_words",
    "title_ends_with_question",
    "title_starts_with_number",
    "title_number_count",
    "title_emoji_count",
    "title_allcaps_word_count",
    "description_length_chars",
    "description_first_line_length",
    "description_has_timestamps",
    "tags_count",
    "publish_hour_utc",
    "publish_day_of_week",
)


def _spearman_correlation(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation. Robust to view-count outliers
    (top clips have log-normal distribution).

    Returns 0.0 on any degenerate input (too few points, all equal).
    Small dependency footprint — implemented in pure Python so the
    intel module doesn't drag scipy in.
    """
    n = len(xs)
    if n != len(ys) or n < 3:
        return 0.0

    def _rank(vals: list[float]) -> list[float]:
        """Average-rank for ties. O(n log n)."""
        indexed = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[indexed[j + 1]] == vals[indexed[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0  # 1-based
            for k in range(i, j + 1):
                ranks[indexed[k]] = avg
            i = j + 1
        return ranks

    rx, ry = _rank(xs), _rank(ys)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    var_x = sum((r - mean_x) ** 2 for r in rx)
    var_y = sum((r - mean_y) ** 2 for r in ry)
    denom = (var_x * var_y) ** 0.5
    if denom == 0:
        return 0.0
    return cov / denom


def compute_feature_view_correlation(
    videos: list[dict[str, Any]],
) -> dict[str, float]:
    """For each numeric feature, compute Spearman correlation with
    view velocity across the given video set.

    Returns dict of ``{feature_name: correlation}``. Missing keys
    only when the input list is empty or has fewer than 3 videos
    (correlation is undefined).

    Bool features are treated as {0, 1} — Spearman handles that
    correctly.
    """
    if len(videos) < 3:
        logger.debug(
            "[top_creator_features] need >= 3 videos for correlation; got %d",
            len(videos),
        )
        return {}

    all_features = [extract_features(v) for v in videos]
    velocities = [f.view_velocity() for f in all_features]

    correlations: dict[str, float] = {}
    for feat in _NUMERIC_FEATURES:
        xs = [float(getattr(f, feat)) for f in all_features]
        correlations[feat] = round(_spearman_correlation(xs, velocities), 4)
    return correlations


__all__ = [
    "VideoFeatures",
    "compute_feature_view_correlation",
    "extract_features",
]
