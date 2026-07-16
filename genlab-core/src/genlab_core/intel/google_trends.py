"""Google Trends integration for trending topic discovery.

Used to:
1. Inform which YouTube search keywords to prioritize
2. Score content relevance (trending topics get higher priority)
3. Generate trend-aware hooks ("everyone is talking about X right now")

Usage:
    intel = GoogleTrendsIntel()
    trending = intel.get_trending_topics("gaming", top_n=10)
    # Returns: ["Marvel Rivals", "GTA 6 release", "Elden Ring DLC", ...]

Backend status (U-10, 2026-06-18)
=================================
The TIER ORDER inside ``get_trending_topics`` is:

  Tier 0  Fresh cache (6h TTL) — always tried first
  Tier 1  Google Trends official RSS feed (zero auth, reliable) — PRIMARY
  Tier 2  pytrends ``trending_searches`` — flaky in prod (rate-limited),
          now graceful-skip on ImportError
  Tier 3  pytrends ``related_queries`` — same flakiness, same graceful-skip
  Tier 4  Stale cache — best-effort fallback
  Tier 5  Hardcoded seed keywords — final safety net

The pytrends upstream (PyPI v4.9.2, 2023-04-13) is ARCHIVED. We
deliberately keep it as an optional dep — if installed, tiers 2/3
still try; if not, the function falls straight through to RSS + cache.

Replacement candidates evaluated 2026-06-18:
  * trendspyg (v0.6.1, active fork) — DIFFERENT API (functional, not
    OO TrendReq); needs adapter; planned for follow-up PR
  * pytrends-modern (v0.2.11, early-stage) — too new to depend on

This file's API surface is small enough that a clean
``GoogleTrendsBackend`` ABC will be a natural follow-up: RSSBackend
(primary) + PytrendsLegacyBackend (deprecated) + TrendspygBackend
(future). For now: soft-import keeps prod safe.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Google Trends category IDs
TRENDS_CATEGORIES = {
    "gaming": 8,  # Video Games
    "sports": 20,  # Sports
    "movies": 34,  # Movies
    "anime": 0,  # No direct category — use keyword filtering
    "ai_creators": 5,  # Computers & Electronics
}

NICHE_SEED_KEYWORDS = {
    "gaming": [
        "gaming",
        "video games",
        "esports",
        "playstation",
        "xbox",
        "nintendo",
        "steam",
        "twitch",
        "fortnite",
        "valorant",
        "minecraft",
        "call of duty",
        "gta",
        "elden ring",
    ],
    "sports": [
        "sports",
        "NBA",
        "NFL",
        "soccer",
        "premier league",
        "MLB",
        "NHL",
        "UFC",
        "tennis",
        "cricket",
        "march madness",
        "champions league",
        "world cup",
    ],
    "movies": [
        "movies",
        "film",
        "cinema",
        "trailer",
        "box office",
        "oscar",
        "marvel",
        "disney",
        "netflix",
        "streaming",
        "director",
        "actor",
        "sequel",
    ],
    "anime": [
        "anime",
        "manga",
        "crunchyroll",
        "one piece",
        "dragon ball",
        "naruto",
        "jujutsu kaisen",
        "demon slayer",
        "my hero academia",
        "studio ghibli",
        "isekai",
    ],
    "ai_creators": [
        "artificial intelligence",
        "AI",
        "machine learning",
        "chatgpt",
        "openai",
        "claude",
        "gemini",
        "llm",
        "deep learning",
        "neural network",
        "midjourney",
        "sora",
    ],
}


_CACHE_DIR = Path(os.environ.get("GENLAB_PROJECT_ROOT", ".")) / ".tmp" / "cache"
_CACHE_TTL_HOURS = 6


def _read_cache(niche_id: str) -> list[str] | None:
    """Read cached trends if fresh (< TTL hours old)."""
    cache_file = _CACHE_DIR / f"trends_{niche_id}.json"
    if not cache_file.exists():
        return None
    try:
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours > _CACHE_TTL_HOURS:
            return None
        data = json.loads(cache_file.read_text())
        return data if isinstance(data, list) and data else None
    except Exception:  # logging happens at caller
        return None


def _write_cache(niche_id: str, topics: list[str]) -> None:
    """Write trends to cache file."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _CACHE_DIR / f"trends_{niche_id}.json"
        cache_file.write_text(json.dumps(topics))
    except Exception as exc:
        logger.debug("[trends] Cache write failed: %s", exc)


def _read_stale_cache(niche_id: str) -> list[str] | None:
    """Read cached trends even if expired — better than seed keywords."""
    cache_file = _CACHE_DIR / f"trends_{niche_id}.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text())
        return data if isinstance(data, list) and data else None
    except Exception:  # logging happens at caller
        return None


class GoogleTrendsIntel:
    """Fetches trending topics from Google Trends per niche.

    Results are cached to .tmp/cache/trends_{niche}.json with a 6-hour TTL.
    On failure, stale cache is used as a fallback (better than seed keywords).
    """

    def __init__(self, geo: str = "US", tz: int = 330):
        self.geo = geo
        self.tz = tz
        self._pytrends = None

    # Sentinel: distinguishes "client never tried to import" (None) from
    # "import failed, don't keep retrying" (False). Same pattern
    # ``metric_collector._PG_POOL`` uses for its psycopg_pool init.
    _PYTRENDS_UNAVAILABLE = False

    # 2026-07-14: per-niche cooldown after pytrends failure. 5 pipeline
    # niches run in parallel and each hits pytrends when Tier-1 RSS is
    # niche-mismatched (returns 0 filtered topics). All 5 simultaneous
    # requests to pytrends trigger 429 rate-limits on Google's side +
    # bounce to Tier-4 stale cache. This cooldown remembers "pytrends
    # failed for this niche recently" and skips Tier-2/3 for
    # ``_PYTRENDS_COOLDOWN_S`` seconds — falls straight through to
    # Tier-4/5 instead of burning another 429. Process-local (dict) so
    # each pipeline's fresh process starts clean; the disk cache
    # (6h TTL) is the persistent layer.
    _pytrends_failure_cooldown: dict[str, float] = {}
    _PYTRENDS_COOLDOWN_S = 1800  # 30 min — matches typical rate-limit windows

    def _get_client(self):
        if self._pytrends is None:
            try:
                from pytrends.request import TrendReq

                self._pytrends = TrendReq(hl="en-US", tz=self.tz)
            except ImportError:
                # U-10 (2026-06-18): pytrends upstream archived. Log
                # once at WARN so operators see the skip in journalctl;
                # subsequent calls hit this branch and short-circuit
                # without re-logging (the sentinel avoids spam every
                # call from get_trending_score_multiplier).
                if self._pytrends is None and not self._PYTRENDS_UNAVAILABLE:
                    logger.warning(
                        "[google_trends] pytrends not installed — Tier-2/3 "
                        "(realtime + daily) paths will be skipped; falling "
                        "through to Tier-1 RSS. To enable, install "
                        "``pytrends`` (archived 2023-04-13, optional dep) "
                        "or wait for the trendspyg backend in a follow-up."
                    )
                    type(self)._PYTRENDS_UNAVAILABLE = True
                self._pytrends = False  # type: ignore[assignment]
        # Distinguishing False (import failed) from None (untried) so
        # the caller's ``if pt is None`` check still triggers the
        # skip path — False is falsy too.
        return self._pytrends if self._pytrends else None

    def get_trending_topics(
        self,
        niche_id: str,
        top_n: int = 10,
    ) -> list[str]:
        """Get top trending topics for a niche right now.

        Falls back gracefully: cache → RSS → pytrends → stale cache → seeds.
        Results are cached for 6 hours to avoid repeated failures.
        """
        # Tier 0: Fresh cache (< 6 hours old)
        cached = _read_cache(niche_id)
        if cached:
            logger.info("[%s] Google Trends (cached): %s", niche_id, cached[:3])
            return cached[:top_n]

        # Tier 1: Google Trends RSS (zero cost, no auth)
        try:
            rss_topics = self._get_rss_trending(niche_id)
            if rss_topics:
                _write_cache(niche_id, rss_topics)
                logger.info("[%s] Google Trends RSS: %s", niche_id, rss_topics[:3])
                return rss_topics[:top_n]
        except Exception as e:
            logger.warning("[%s] Trends RSS failed: %s", niche_id, e)

        # 2026-07-14: pytrends cooldown check. If Tier-2/3 failed for
        # this niche within the last 30 min, skip them entirely to
        # avoid burning another 429 on Google's side + the 5-way
        # concurrent stampede when all niche pipelines fire.
        cooldown_until = self._pytrends_failure_cooldown.get(niche_id, 0)
        now = time.time()
        pytrends_in_cooldown = now < cooldown_until
        if pytrends_in_cooldown:
            logger.debug(
                "[%s] pytrends in cooldown (%.0fs remaining) — skipping Tier-2/3 attempts",
                niche_id,
                cooldown_until - now,
            )

        # Tier 2: pytrends real-time (often rate-limited)
        if not pytrends_in_cooldown:
            try:
                realtime = self._get_realtime_trending(niche_id)
                realtime = [t for t in realtime if t and t.strip()] if realtime else []
                if realtime:
                    _write_cache(niche_id, realtime)
                    logger.info("[%s] Google Trends real-time: %s", niche_id, realtime[:3])
                    return realtime[:top_n]
            except Exception as e:
                logger.warning("[%s] Real-time trends failed: %s", niche_id, e)
                self._pytrends_failure_cooldown[niche_id] = now + self._PYTRENDS_COOLDOWN_S

        # Tier 3: pytrends daily (also often rate-limited)
        if not pytrends_in_cooldown:
            try:
                daily = self._get_daily_trending(niche_id)
                daily = [t for t in daily if t and t.strip()] if daily else []
                if daily:
                    _write_cache(niche_id, daily)
                    logger.info("[%s] Google Trends daily: %s", niche_id, daily[:3])
                    return daily[:top_n]
            except Exception as e:
                logger.warning("[%s] Daily trends failed: %s", niche_id, e)
                self._pytrends_failure_cooldown[niche_id] = now + self._PYTRENDS_COOLDOWN_S

        # Tier 4: Stale cache (expired but better than nothing)
        stale = _read_stale_cache(niche_id)
        if stale:
            logger.warning(
                "[%s] Google Trends unavailable — using stale cache (%d topics)",
                niche_id,
                len(stale),
            )
            return stale[:top_n]

        seeds = NICHE_SEED_KEYWORDS.get(niche_id, ["trending", niche_id])
        logger.warning(
            "[%s] Google Trends unavailable — using seed keywords (top %d of %d)",
            niche_id,
            top_n,
            len(seeds),
        )
        # Respect the caller's top_n — pre-2026-06-18 this returned the
        # full list which caused log spam and silently leaked more seeds
        # into downstream search than top_n=5 expected (12 for AI etc.).
        return seeds[:top_n]

    def _get_rss_trending(self, niche_id: str) -> list[str]:
        """Fetch daily trending Google searches via official RSS feed.

        Zero cost, no auth, no rate limiting. Returns top 20 topics.
        Uses Google Trends category parameter for niche-specific results
        when available, then falls back to general trends with keyword filtering.
        """
        import urllib.request
        import xml.etree.ElementTree as ET

        # Google Trends RSS category IDs (different from YouTube categories)
        _RSS_CATEGORIES = {
            "gaming": "8",  # Games
            "sports": "20",  # Sports
            "movies": "34",  # Movies
            "ai_creators": "5",  # Computers & Electronics
            # anime: no direct category — use general + keyword filter
        }

        topics: list[str] = []

        # **2026-06-18**: the niche-specific RSS endpoint
        # (``?geo=US&cat=5``/``cat=8``/``cat=20``) silently returns
        # the general feed — verified by ``curl`` test confirming
        # identical bodies across all cat values. We keep the attempt
        # for the eventual day Google honours ``cat=`` again, but apply
        # the same niche-filter as the general fallback so a passing
        # response that's actually general data doesn't leak.
        cat_id = _RSS_CATEGORIES.get(niche_id)
        if cat_id:
            try:
                cat_url = f"https://trends.google.com/trending/rss?geo={self.geo}&cat={cat_id}"
                req = urllib.request.Request(cat_url, headers={"User-Agent": "GenLab/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                root = ET.fromstring(data)
                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is not None and title_el.text:
                        topics.append(title_el.text.strip())
                # Pre-2026-06-18 this early-returned ``topics[:20]``
                # unconditionally. That was the source of the
                # niche-pollution complaint — category-RSS was
                # silently general → top results passed through with
                # no niche check. Now we fall through to the shared
                # filter at the bottom of the function.
                if topics:
                    logger.info(
                        "[%s] Trends RSS category %s: %d topics (will filter)",
                        niche_id,
                        cat_id,
                        len(topics),
                    )
            except Exception as e:
                logger.debug("[%s] Category RSS failed (cat=%s): %s", niche_id, cat_id, e)

        # Fall back to general RSS if category RSS returned nothing.
        # Always filter at the end regardless of which feed we used.
        if not topics:
            rss_url = f"https://trends.google.com/trending/rss?geo={self.geo}"
            req = urllib.request.Request(
                rss_url,
                headers={"User-Agent": "GenLab/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()

            root = ET.fromstring(data)
            for item in root.iter("item"):
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    topics.append(title_el.text.strip())

            if not topics:
                return []

        # Filter for niche relevance. **2026-06-18 fix**: the category-
        # specific Google Trends RSS endpoint (``?cat=5``/``?cat=8``/
        # ``?cat=20``) silently returns general trends — verified by
        # curl test 2026-06-18 returning identical bodies across all
        # cat values + the no-cat baseline. So this fallback path
        # ALWAYS runs against the general feed (gas, moscow, tornado,
        # etc.). The previous implementation appended ``general_terms``
        # after ``niche_terms`` which meant niches with zero matches
        # (most days) returned ENTIRELY off-topic keywords — those got
        # prepended to NICHE_SEARCH_KEYWORDS in the trending fetcher
        # and pushed the niche-correct searches past the per-run cap
        # of 2-3 searches. Result: AI niche fetched Verge tech news,
        # sports fetched soccer-stadium news, etc.
        #
        # The fix: drop ``general_terms`` from the return. When the
        # general feed has zero niche matches, return empty and let the
        # next tier (pytrends realtime → daily → stale cache → SEEDS)
        # take over. NICHE_SEED_KEYWORDS is niche-correct by definition
        # so the fall-through is safer than the pollution.
        niche_keywords = NICHE_SEED_KEYWORDS.get(niche_id, [])
        niche_terms = [
            term for term in topics if any(kw.lower() in term.lower() for kw in niche_keywords)
        ]
        if not niche_terms:
            logger.debug(
                "[%s] General RSS had 0 niche-matching terms (out of %d) — "
                "returning empty to let Tier-5 seed keywords take over",
                niche_id,
                len(topics),
            )
            return []
        return niche_terms[:20]

    def _get_realtime_trending(self, niche_id: str) -> list[str]:
        """Get today's real-time trending searches via pytrends.

        Same niche-pollution fix as ``_get_rss_trending`` (2026-06-18):
        when no terms match the niche, return empty so the chain falls
        through to seeds rather than returning off-topic US trends.
        """
        pt = self._get_client()
        if pt is None:
            return []
        trending_df = pt.trending_searches(pn="united_states")
        topics = trending_df[0].tolist()

        niche_keywords = NICHE_SEED_KEYWORDS.get(niche_id, [])
        niche_terms = [
            str(term)
            for term in topics
            if any(kw.lower() in str(term).lower() for kw in niche_keywords)
        ]
        if not niche_terms:
            logger.debug(
                "[%s] pytrends realtime had 0 niche-matching terms (out of %d) — "
                "returning empty to let Tier-5 seed keywords take over",
                niche_id,
                len(topics),
            )
            return []
        return niche_terms[:20]

    def _get_daily_trending(self, niche_id: str) -> list[str]:
        """Get related queries from Google Trends for seed keywords."""
        pt = self._get_client()
        if pt is None:
            return []
        seeds = NICHE_SEED_KEYWORDS.get(niche_id, [niche_id])[:3]

        pt.build_payload(
            seeds,
            cat=TRENDS_CATEGORIES.get(niche_id, 0),
            timeframe="now 1-d",
            geo=self.geo,
        )
        related = pt.related_queries()

        trending_terms = []
        for seed in seeds:
            if seed in related and related[seed].get("rising") is not None:
                rising = related[seed]["rising"]
                if rising is not None and not rising.empty:
                    trending_terms.extend(rising["query"].tolist()[:5])

        return trending_terms if trending_terms else seeds

    def get_trending_score_multiplier(
        self,
        topic: str,
        niche_id: str,
    ) -> float:
        """Return a score multiplier (1.0–3.0) based on how trending a topic is."""
        try:
            trending = self.get_trending_topics(niche_id, top_n=20)
            topic_lower = topic.lower()

            for i, trend in enumerate(trending):
                if trend.lower() in topic_lower or topic_lower in trend.lower():
                    if i < 5:
                        return 3.0
                    elif i < 10:
                        return 2.0
                    else:
                        return 1.5

            return 1.0
        except Exception:  # logging happens at caller
            return 1.0
