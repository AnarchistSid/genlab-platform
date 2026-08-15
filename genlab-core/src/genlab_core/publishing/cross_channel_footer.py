"""Cross-channel funnel footer (2026-08-15).

Appends a "Also on {platform}: {handle}" line to captions so a
large-audience platform (typically Facebook, which has 8.6K-10K
followers on ai_creators + movies) can drive followers to small-
audience sibling platforms (Instagram, YouTube, Threads with 4-13
followers on those niches).

Growth-flywheel gap discovered 2026-08-15:
`[[growth-flywheel-diagnostic-2026-08-15]]` — 30-day follower
deltas per platform: YouTube +2 total subs, Instagram +5 total.
Meanwhile ai_creators FB has 10,032 followers and grew +17 in the
same window. That FB audience is a completely-untapped funnel.

## Design

* **Source-platform aware**: only fires on FB captions (the
  platform with actual funnel-source audience). Fire on IG would
  send FB-follower traffic to a smaller pool, defeating the point.
* **Target-platform rotation**: cycles through the small-audience
  siblings (IG, Threads, YouTube) deterministically per blueprint
  hash so bandit can eventually distinguish which target converts
  best. Today: pick one target per post via hash-mod.
* **Handle-per-platform-per-niche**: a niche's IG @handle differs
  from its Twitter @handle differs from its YouTube channel URL.
  Table below is the source of truth.
* **Flag-gated per niche**: `GENLAB_CROSS_CHANNEL_FOOTER_NICHES`
  comma-list. Empty = off everywhere. Canary rollout matches the
  persona-hint pattern.

## Fail-open

Every layer returns empty string on any error — the caption remains
exactly as-is if handles are missing, flag off, hash unstable, etc.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Final

logger = logging.getLogger(__name__)


_ROLLOUT_ENV: Final[str] = "GENLAB_CROSS_CHANNEL_FOOTER_NICHES"
_ALL_TOKENS: Final[set[str]] = {"all", "*"}
_OFF_TOKENS: Final[set[str]] = {"", "0", "false", "no", "off"}


# Per-niche handles per platform. Handles verified 2026-08-15 via
# collect_audience_metrics live fetch. YouTube uses the channel
# handle format (/@BrandSlug) since the numeric channel_id is
# opaque to viewers.
_HANDLES: Final[dict[str, dict[str, str]]] = {
    "ai_creators": {
        "instagram": "@blackbox.brief",
        "threads": "@blackbox.brief",
        "youtube": "@BlackboxBrief",
    },
    "gaming": {
        "instagram": "@critical_rush",
        "threads": "@critical_rush",
        "youtube": "@CriticalRush",
    },
    "sports": {
        "instagram": "@theclutchwire",
        "threads": "@theclutchwire",
        "youtube": "@ClutchWire",
    },
    "movies": {
        "instagram": "@splicereel",
        "threads": "@splicereel",
        "youtube": "@SpliceReel",
    },
    "anime": {
        "instagram": "@theframedrift",
        "threads": "@theframedrift",
        "youtube": "@FrameDrift",
    },
}


# Target-platform rotation order per source. FB (the primary funnel
# source) rotates through IG → Threads → YouTube because those are
# the smallest-audience platforms per niche. YouTube target is
# emphasized 2x weight (small subs count = most growth headroom).
_TARGETS_FROM_FACEBOOK: Final[tuple[str, ...]] = (
    "instagram", "youtube", "threads", "youtube",
)


def is_enabled_for(niche_id: str) -> bool:
    """True when the cross-channel footer should be injected for
    ``niche_id``. Same env-value semantics as persona_writer_hint."""
    raw = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    if raw in _OFF_TOKENS:
        return False
    if raw in _ALL_TOKENS:
        return True
    allowed = {p.strip() for p in raw.split(",") if p.strip()}
    return niche_id in allowed


def _pick_target(niche_id: str, blueprint_hash: str) -> str | None:
    """Deterministic per-blueprint target-platform selection.

    Uses SHA-256(blueprint_hash) mod rotation length. Same hash →
    same target across re-writes (idempotent). Different blueprints
    hit different targets so the aggregate impression mix funnels
    to all three small platforms."""
    if not blueprint_hash:
        return None
    h = hashlib.sha256(blueprint_hash.encode("utf-8")).digest()
    idx = int.from_bytes(h[:4], "big") % len(_TARGETS_FROM_FACEBOOK)
    target = _TARGETS_FROM_FACEBOOK[idx]
    handles = _HANDLES.get(niche_id) or {}
    if target not in handles:
        return None
    return target


def build_footer(
    niche_id: str,
    source_platform: str,
    blueprint_hash: str = "",
) -> str:
    """Return the footer line for a caption. Empty string when the
    flag is off, the niche is unknown, the source platform isn't a
    supported funnel-source, or handles are missing.

    Args:
        niche_id: canonical niche id.
        source_platform: platform the caption is being written FOR.
            Only 'facebook' currently returns a footer.
        blueprint_hash: deterministic seed for target-platform
            rotation. Any stable per-blueprint string (id, video_id).
    """
    if not is_enabled_for(niche_id):
        return ""
    if source_platform != "facebook":
        # Add IG/Threads sources later once the FB canary proves out.
        return ""
    target = _pick_target(niche_id, blueprint_hash)
    if not target:
        return ""
    handle = (_HANDLES.get(niche_id) or {}).get(target)
    if not handle:
        return ""

    # Platform-specific phrasing so it doesn't read as generic
    # cross-promo boilerplate.
    if target == "instagram":
        return f"\n\n📸 Also on Instagram: {handle}"
    if target == "youtube":
        return f"\n\n🎥 More on YouTube: youtube.com/{handle}"
    if target == "threads":
        return f"\n\n🧵 Follow the takes on Threads: {handle}"
    return ""


def append_footer_if_enabled(
    caption: str,
    niche_id: str,
    source_platform: str,
    blueprint_hash: str = "",
) -> str:
    """Convenience wrapper — returns caption unchanged when disabled.

    Also guards against the footer already being present (idempotent
    for re-renders)."""
    if not caption:
        return caption
    footer = build_footer(niche_id, source_platform, blueprint_hash)
    if not footer:
        return caption
    # Idempotency: if a footer line already appears, don't stack.
    for marker in ("📸 Also on Instagram", "🎥 More on YouTube",
                   "🧵 Follow the takes"):
        if marker in caption:
            return caption
    return caption + footer
