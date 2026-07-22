"""Threads metrics fetcher (canonical implementation).

Thin wrapper around the pre-existing `learning/metrics/threads._fetch_threads`
so the peer-fetcher shape (`fetch_facebook` / `fetch_instagram` /
`fetch_youtube` / `fetch_twitter`) is complete. Prior to 2026-07-22 the
canonical dispatcher `run_fetch_insights._fetch_platform_insights` had no
`elif platform == "threads":` branch — Threads posts fell through to
`else: return None`, so every Threads SUCCESS row stayed at SUCCESS forever
(0/5 SUCCESS→INSIGHTS transitions in the 7 days before this fix; verified
via `SELECT niche_id, platform, status FROM publishing_analytics`).

Reward-shape: aligned with `RewardShaper.BASE_WEIGHTS["threads"]` —
views, replies, reposts, discovery_share (omitted; API doesn't expose it,
`compute_reward` redistributes its 0.15 weight to observed metrics).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def fetch_threads(post_id: str, niche_id: str = "") -> dict[str, Any]:
    """Fetch Threads insights (views/likes/replies/reposts/quotes).

    Delegates to the pre-existing implementation in
    `genlab_core.learning.metrics.threads._fetch_threads` to preserve
    the single-source-of-truth semantics of the peer canonical fetchers
    (`fetch_facebook`, `fetch_instagram`, etc.). Do NOT duplicate the
    Graph API call surface here — bug fixes need to land in ONE place.
    """
    from genlab_core.learning.metrics.threads import _fetch_threads

    return _fetch_threads(post_id, niche_id=niche_id)


__all__ = ["fetch_threads"]
