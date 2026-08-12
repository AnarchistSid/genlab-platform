"""Meta Reels trending audio catalog — interface + stub.

## Status: SPIKE (interface only)

This module ships the interface + integration wire for a Meta Reels
trending-audio fetcher. The actual scraping implementation is
DELIBERATELY STUBBED — returns empty results — because:

  * Meta does NOT expose the Reels Music Library via any official API
  * Scraping requires headless-browser automation (Playwright)
  * Meta TOS restricts programmatic access to trending audio data —
    operator must review + acknowledge before enabling
  * The scraper is fragile: Meta's UI/API changes silently, breaks
    without warning, requires monitoring

By landing the interface first, the LLM music-mood matcher (`music_
mood_llm_fit.suggest_mood`) can start accepting a `trending_moods`
context parameter. When the real scraper lands, it's plug-and-play
— no wire changes needed.

## Interface contract

`get_trending_moods_for_niche(niche_id) -> list[TrendingAudioMood]`

Returns a list of mood labels currently trending on Meta Reels for
the given niche. The mood labels are drawn from the SAME vocabulary
as the niche's `visuals.yaml` music_mood dimension arms — so the
LLM's suggestion (based on trending context) is always a valid arm
the transformation orchestrator can consume.

Fail-open contract: returns `[]` on ANY failure (fetcher not
implemented, cache miss, network error, TOS not acknowledged, flag
off). Callers treat empty list as "no trending signal available"
and fall back to the baseline LLM/bandit selection.

## Cache strategy (documented for future implementation)

  * TTL: 6 hours (trending audio churns hourly on Meta but 6h aligns
    with publish cadence — no benefit refreshing more often)
  * Storage: `.tmp/cache/trending_audio_meta/<niche_id>.json`
  * Format: `{"fetched_at": iso8601, "moods": [{"mood": str,
    "trend_rank": int, "meta_audio_id": str}]}`

## Future work — implementation checklist

Tracking here so the next session can pick up cleanly:

  1. Playwright headless-browser scrape of Meta Reels Music Library
     UI (mobile.facebook.com/reels/audio — verify path)
  2. Parse audio track name → derive mood tag via secondary LLM
     classification (audio track names aren't standardized to our
     mood vocab)
  3. Wire cache write in `get_trending_moods_for_niche` real impl
  4. Add scraper systemd timer: `genlab-fetch-trending-audio-meta.timer`
     4× daily to keep cache fresh
  5. Add flag `GENLAB_TRENDING_AUDIO_META_ENABLED` for full activation
     (this stub already respects it — returns empty when off)
  6. Add TOS acknowledgement config key
     `trending_audio_meta.acknowledged_tos_risk: true` in
     `genlab-core/config/trending_audio_meta.yaml`
  7. Monitoring: alarm when cache is >24h stale (indicates scraper
     is silently broken — same class-of-bug as tmpfiles.org URL
     scheme change from 2026-07-19)

## Consumer wire

`music_mood_llm_fit.suggest_mood` accepts `trending_moods: list[str]`
kwarg. When populated, the LLM prompt injects a "TRENDING NOW: ..."
line that biases the model toward currently-viral moods when the
content tone is ambiguous.

Zero effect from this module today — stub returns `[]` unless the
flag is on AND the real scraper is implemented AND its cache has
data. Every layer fails open.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrendingAudioMood:
    """One trending-audio mood entry.

    Fields
    ------
    mood : str
        Mood label from the niche's declared music_mood vocabulary
        (e.g., "hype", "dramatic", "chill"). MUST match a value in
        the niche's `visuals.yaml` music_mood.moods list — the
        transformation orchestrator won't accept anything else.
    trend_rank : int
        1-indexed position on Meta's trending list. Lower = hotter.
    meta_audio_id : str
        Meta's internal ID for the specific audio track that anchors
        this mood. Kept for future auditing / attribution — not used
        by the LLM prompt (LLM only sees mood name).
    """

    mood: str
    trend_rank: int
    meta_audio_id: str


_CACHE_TTL_HOURS: Final[int] = 6


def _is_enabled() -> bool:
    """Env kill switch. Default OFF — stub is a no-op unless operator
    (a) acknowledges TOS risk in config, (b) flips this flag, (c) real
    scraper is deployed and its cache is populated. Any one missing
    -> return []."""
    from genlab_core.settings import env_true

    return env_true("GENLAB_TRENDING_AUDIO_META_ENABLED")


def get_trending_moods_for_niche(niche_id: str) -> list[TrendingAudioMood]:
    """Return the currently trending audio moods for a niche on Meta
    Reels.

    THIS IS A STUB. Returns `[]` unconditionally today. When the real
    scraper is implemented, this function reads from the on-disk
    cache populated by the scheduled fetcher.

    Fail-open at every layer:
      * Flag off -> []
      * Cache miss -> []
      * Cache stale (>_CACHE_TTL_HOURS old) -> []
      * Parse error -> [] + WARN log
      * Any exception -> [] + WARN log
    """
    if not _is_enabled():
        return []

    try:
        return _read_cache(niche_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[trending_audio_meta] cache read failed niche=%s: %s", niche_id, exc,
        )
        return []


def _read_cache(niche_id: str) -> list[TrendingAudioMood]:
    """Read the cache file for a niche. Returns [] on cache miss
    or stale entries.

    TODO(scraper session): implement the real cache read once the
    scraper writes to disk. For now: always returns [] with an INFO
    log so operators can see the stub is being called.
    """
    logger.info(
        "[trending_audio_meta] STUB: no scraper implementation yet niche=%s",
        niche_id,
    )
    return []


def moods_as_prompt_context(trending: list[TrendingAudioMood]) -> str:
    """Convert a list of trending moods to a compact string for
    injection into an LLM prompt.

    Used by `music_mood_llm_fit.suggest_mood` when the consumer
    passes a `trending_moods` kwarg. Returns empty string when input
    is empty — caller uses that to decide whether to inject the
    context block at all.

    Format:
        "TRENDING ON META REELS: hype (rank 1), dramatic (rank 3)"

    Ranks preserved to give the LLM a strength signal (rank 1 mood
    is much more viral than rank 5).
    """
    if not trending:
        return ""
    ordered = sorted(trending, key=lambda t: t.trend_rank)
    parts = [f"{t.mood} (rank {t.trend_rank})" for t in ordered]
    return "TRENDING ON META REELS: " + ", ".join(parts)


__all__ = [
    "TrendingAudioMood",
    "get_trending_moods_for_niche",
    "moods_as_prompt_context",
]
