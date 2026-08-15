"""Instagram discovery hashtag augment (2026-08-15).

Small-audience Instagram accounts (10-165 followers on 4 of 5 Gen
Lab niches) grow primarily through algorithmic discovery, not
follower feed. The algorithm surfaces posts to non-followers via:

  1. **Hashtag pools** — every hashtag is a discovery bucket.
     Posts with 5 tags land in 5 buckets; posts with 8-10 land in
     8-10 buckets. More surface = more chance of a curator or
     scroller finding it.

  2. **Discovery tags** — #Reels, #ExplorePage, #ViralReel, etc
     are meta-tags Instagram itself uses to identify content for
     the Explore + Reels feeds. They're not topical — they're
     structural. A niche-hashtags-only post competes only within
     that niche pool; adding discovery tags puts it in the "any
     reel" pool where reach is orders of magnitude larger.

Current state (2026-08-15):
  * Writer generates 3-5 hashtags via LLM (topical).
  * `_adapt_instagram` pads from pool up to 5.
  * ZERO discovery tags in any pool observed on prod (checked
    templates.yaml/captions.hashtag_pool for each niche).

## Design

Appends a small (2-4) rotating set of discovery hashtags to IG
posts, bringing total tag count to ~6-9. Flag-gated per niche via
`GENLAB_IG_DISCOVERY_HASHTAGS_NICHES`.

Rotates the pool across posts (not always the same 4) so the
account doesn't look templated to Meta's spam classifier.

## Fail-open

Every layer returns the input unchanged on any error.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Final

logger = logging.getLogger(__name__)


_ROLLOUT_ENV: Final[str] = "GENLAB_IG_DISCOVERY_HASHTAGS_NICHES"
_ALL_TOKENS: Final[set[str]] = {"all", "*"}
_OFF_TOKENS: Final[set[str]] = {"", "0", "false", "no", "off"}


# Discovery-tag pool: 12 tags rotating, pick 4 per post. Deliberately
# structural/format tags (not topical) — they signal the algorithm
# "this is a reel worth showing to non-followers." Topical tags stay
# LLM-generated + niche-pool sourced.
_DISCOVERY_POOL: Final[tuple[str, ...]] = (
    "#Reels", "#Reel", "#InstaReels", "#ReelsInstagram",
    "#ExplorePage", "#Explore", "#Trending", "#ViralReel",
    "#Viral", "#ContentCreator", "#Shorts", "#Fyp",
)

# How many discovery tags to append per post (target: ~6-9 total tags
# after adding to LLM's 3-5). 4 is aggressive-but-safe — Meta's
# anti-spam classifier flags 15+ generic tags but 8-10 is well within
# normal-reel range.
_TAGS_TO_APPEND: Final[int] = 4


def is_enabled_for(niche_id: str) -> bool:
    """True when discovery tags should be appended for ``niche_id``.
    Same env value semantics as persona_writer_hint and
    cross_channel_footer."""
    raw = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    if raw in _OFF_TOKENS:
        return False
    if raw in _ALL_TOKENS:
        return True
    allowed = {p.strip() for p in raw.split(",") if p.strip()}
    return niche_id in allowed


def _select_discovery_tags(seed: str, existing: set[str]) -> list[str]:
    """Deterministic per-seed rotation. Same seed → same picks
    (idempotent for re-renders). Different seeds cycle through the
    pool so aggregate impressions cover all discovery buckets."""
    if not seed:
        return []
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    # Use 4-byte chunks to derive N indices into the pool
    picks: list[str] = []
    pool = list(_DISCOVERY_POOL)
    existing_lc = {t.lower().lstrip("#") for t in existing}
    for i in range(_TAGS_TO_APPEND):
        idx = int.from_bytes(h[i * 4: i * 4 + 4], "big") % len(pool)
        tag = pool.pop(idx)
        # Skip if operator/LLM already placed this tag
        if tag.lstrip("#").lower() in existing_lc:
            continue
        picks.append(tag)
        # If pool exhausted, stop
        if not pool:
            break
    return picks


def augment_ig_hashtags(
    hashtags: list[str],
    niche_id: str,
    blueprint_seed: str = "",
) -> list[str]:
    """Return the input hashtag list with discovery tags appended
    when the flag is on. Idempotent — never adds a tag that's
    already present.

    Args:
        hashtags: current tag list (LLM + pool-padded).
        niche_id: canonical niche id.
        blueprint_seed: stable per-blueprint string for rotation.
    """
    if not is_enabled_for(niche_id):
        return hashtags
    if not blueprint_seed:
        # Without a seed we'd always pick the same 4 → templated
        # signature. Fail-open to caller's input.
        return hashtags
    existing = set(hashtags or [])
    picks = _select_discovery_tags(blueprint_seed, existing)
    if not picks:
        return hashtags
    return list(hashtags) + picks
