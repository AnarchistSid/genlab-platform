"""Threads hashtag augment (2026-08-15).

Threads captions on Gen Lab prod carry ZERO hashtags (verified
2026-08-15 via publishing_analytics dump). The writer generates
hashtags into a story-level field but the Threads adapter never
appended them to the caption body.

Effect: Threads posts land in NO discovery stream. avg views:
  * gaming/threads:    1.8
  * ai_creators/threads: 14.8
  * anime/threads:     28.7
  * sports/threads:    50.0
  * movies/threads:    51.5

Threads' "For You" algorithm uses hashtag co-occurrence as one of
its discovery signals for small-audience accounts (10-13 followers
per niche). Zero hashtags = zero discovery pool entry.

## Design

Different from IG discovery (which appends 4 structural tags):

  * **1-2 tags max** — Threads community norm is 1-3 total; more
    reads as spammy (Meta's own guidance for Threads).
  * **Niche-anchor only** — no #Reels / #Trending / #Fyp equivalents;
    Threads' text-first nature means generic-hype tags backfire.
  * **In-line placement** — Threads norm is inline hashtags at
    caption end (not trailing block separated by newlines like IG).
  * **Reuse story-level hashtags** — the LLM already picked topical
    tags; use those as the first choice. Fall back to niche-anchor
    only if none exist.

Flag-gated per niche via `GENLAB_THREADS_HASHTAGS_NICHES` — same
canary pattern as ig_discovery_hashtags / persona_writer_hint.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Final

# Match real hashtags: # followed by 2+ letters (so "#1" position
# numbers don't trigger the idempotency guard).
_REAL_HASHTAG_RE = re.compile(r"#[A-Za-z][A-Za-z0-9_]+")

logger = logging.getLogger(__name__)


_ROLLOUT_ENV: Final[str] = "GENLAB_THREADS_HASHTAGS_NICHES"
_ALL_TOKENS: Final[set[str]] = {"all", "*"}
_OFF_TOKENS: Final[set[str]] = {"", "0", "false", "no", "off"}


# Per-niche 1-tag fallback if story-level hashtags are empty.
# Kept intentionally small — Threads community rewards restraint.
_NICHE_FALLBACK: Final[dict[str, str]] = {
    "gaming": "#Gaming",
    "sports": "#Sports",
    "movies": "#Movies",
    "anime": "#Anime",
    "ai_creators": "#AI",
}

_MAX_TAGS: Final[int] = 2


def is_enabled_for(niche_id: str) -> bool:
    """True when Threads should get hashtag augment. Same env
    semantics as sibling augments (persona_writer_hint,
    cross_channel_footer, ig_discovery_hashtags)."""
    raw = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    if raw in _OFF_TOKENS:
        return False
    if raw in _ALL_TOKENS:
        return True
    allowed = {p.strip() for p in raw.split(",") if p.strip()}
    return niche_id in allowed


def _pick_tags(
    source_hashtags: list[str], niche_id: str,
) -> list[str]:
    """Pick up to _MAX_TAGS from source (story-level LLM tags).
    Fall back to niche anchor when source is empty."""
    tags: list[str] = []
    for t in (source_hashtags or []):
        if not isinstance(t, str):
            continue
        clean = t.strip()
        if not clean:
            continue
        if not clean.startswith("#"):
            clean = f"#{clean.lstrip('#')}"
        tags.append(clean)
        if len(tags) >= _MAX_TAGS:
            break
    if not tags:
        fallback = _NICHE_FALLBACK.get(niche_id)
        if fallback:
            tags.append(fallback)
    return tags


def append_niche_hashtags(
    caption: str,
    niche_id: str,
    source_hashtags: list[str] | None = None,
) -> str:
    """Append 1-2 hashtags to a Threads caption. Idempotent — if
    the caption already contains a REAL hashtag inline (# followed
    by letters, not "#1" position numbers), returns as-is (LLM or
    upstream augment did their job)."""
    if not is_enabled_for(niche_id):
        return caption
    if not caption:
        return caption
    # Idempotent guard: skip only when a real hashtag exists. Bare
    # "#" or "#1" position numbers don't count — those are content.
    if _REAL_HASHTAG_RE.search(caption):
        return caption
    tags = _pick_tags(source_hashtags or [], niche_id)
    if not tags:
        return caption
    # Threads convention: inline tag block at end, space-separated,
    # separated from body by a blank line.
    return caption.rstrip() + "\n\n" + " ".join(tags)
