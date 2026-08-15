"""YouTube snippet.tags augment (2026-08-15).

YT `snippet.tags` are the video's structured tags (separate from
hashtags in the description). YT publisher currently sends 3-5
tags per video from `payload.hashtags`. YT community consensus is
6-15 well-chosen tags help recommendation surface area — this
adds a per-niche anchor set + shared discovery pool.

Small-audience channels (0-9 subs across all 5 Gen Lab niches
per 2026-08-15 audience_snapshots) get the most marginal benefit
from every discovery boost.

## Design

* Merges: existing tags → per-niche anchor tags → shared discovery pool
* Case-insensitive dedup preserves first-seen order
* Caps at 15 tags total (YT hard limit is 500 chars combined, 15 is
  a safe count under that)
* No flag gate — video tags are broadly considered safe/neutral by
  YT's classifier; risk of misfire is lower than IG hashtag augment
"""
from __future__ import annotations

from typing import Final

# Per-niche anchor pool (broader-first ordering). Mirrors
# _NICHE_ANCHOR_HASHTAGS in youtube_shorts_seo.py but as tag strings
# without the # prefix (YT snippet.tags convention).
_NICHE_ANCHORS: Final[dict[str, tuple[str, ...]]] = {
    "gaming": (
        "gaming", "gaming shorts", "game clips", "esports",
        "gameplay", "twitch clips",
    ),
    "sports": (
        "sports", "sports highlights", "sports shorts",
        "sports moments", "athletic highlights",
    ),
    "movies": (
        "movies", "movie clips", "cinema", "film", "trailers",
        "film reviews",
    ),
    "anime": (
        "anime", "anime clips", "anime shorts", "manga",
        "anime edit", "otaku",
    ),
    "ai_creators": (
        "AI", "AI tools", "artificial intelligence", "tech",
        "AI news", "machine learning",
    ),
}

# Shared discovery pool — niche-agnostic tags that push into broader
# recommendation buckets. YT's algorithm uses these to identify
# "shorts worth showing beyond subscribers".
_DISCOVERY_POOL: Final[tuple[str, ...]] = (
    "shorts", "youtube shorts", "shorts video", "viral",
    "trending", "must watch", "shorts feed",
)

_MAX_TAGS: Final[int] = 15


def _dedupe_case_insensitive(tags: list[str]) -> list[str]:
    """First-seen wins, case-insensitive."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if not t:
            continue
        key = t.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(t.strip())
    return out


def augment_youtube_snippet_tags(
    base_tags: list[str],
    niche_id: str,
) -> list[str]:
    """Return the input tags merged with niche anchors + discovery
    pool, deduped, capped at _MAX_TAGS.

    Idempotent: rerunning on already-augmented output returns the
    same list (up to the cap).
    """
    merged: list[str] = list(base_tags or [])
    merged.extend(_NICHE_ANCHORS.get(niche_id, ()))
    merged.extend(_DISCOVERY_POOL)
    return _dedupe_case_insensitive(merged)[:_MAX_TAGS]
