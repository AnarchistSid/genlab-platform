"""Pipeline stage: Fetch gaming stories from Steam, Twitch, and RSS.

Populates context["stories"] from three parallel sources, merges,
deduplicates by game title, and sorts by score descending.

Usage:
    stage = FetchGamingStories()
    context = stage.execute(context)
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser
import requests
import yaml
from genlab_core.intelligence.dedup_engine import DedupEngine
from genlab_core.ratelimit.token_bucket import TokenBucket
from genlab_core.strategies import ContentResearchStrategy

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _normalize_title(title: str) -> str:
    """Lowercase and strip punctuation for dedup comparison."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# SOURCE 1 — Steam concurrent player spike detector
# ---------------------------------------------------------------------------


class SteamSpikeFetcher:
    """Detect games with player count spikes on Steam."""

    FEATURED_URL = "https://store.steampowered.com/api/featuredcategories"
    PLAYERS_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"

    def __init__(self, config: dict[str, Any]):
        self._multiplier = config.get("spike_threshold_multiplier", 1.5)
        self._max_stories = config.get("max_stories", 5)
        self._baseline_path = PROJECT_ROOT / ".tmp" / "steam_baseline.json"
        # Steam Web API: 1 req/sec conservative rate limit
        self._rate_limiter = TokenBucket(rate=1.0, burst=10)

    def _load_baseline(self) -> dict[str, float]:
        if self._baseline_path.exists():
            try:
                with open(self._baseline_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_baseline(self, baseline: dict[str, float]) -> None:
        self._baseline_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._baseline_path, "w") as f:
            json.dump(baseline, f, indent=2)

    def fetch(self) -> list[dict[str, Any]]:
        stories: list[dict[str, Any]] = []
        try:
            self._rate_limiter.acquire()
            resp = requests.get(self.FEATURED_URL, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            # Extract top sellers
            top_sellers = data.get("top_sellers", {}).get("items", [])
            if not top_sellers:
                logger.info("[Steam] No top_sellers in featured categories")
                return []

            baseline = self._load_baseline()

            for item in top_sellers[:10]:
                app_id = str(item.get("id", ""))
                name = item.get("name", "")
                if not app_id or not name:
                    continue

                # Get current player count
                try:
                    self._rate_limiter.acquire()
                    players_resp = requests.get(
                        self.PLAYERS_URL,
                        params={"appid": app_id},
                        timeout=10,
                    )
                    players_resp.raise_for_status()
                    current = players_resp.json().get("response", {}).get("player_count", 0)
                except Exception as e:
                    logger.debug("[Steam] Failed to get players for %s: %s", app_id, e)
                    time.sleep(0.5)
                    continue

                avg = baseline.get(app_id, 0)

                if avg == 0:
                    # First time — store baseline, neutral score
                    baseline[app_id] = float(current)
                    score = 0.5
                else:
                    ratio = current / avg if avg > 0 else 1.0
                    if ratio >= self._multiplier:
                        score = min(ratio, 3.0) / 3.0
                    else:
                        # Update rolling average and skip (not spiking)
                        baseline[app_id] = (avg * 0.8) + (current * 0.2)
                        time.sleep(0.5)
                        continue
                    # Update baseline with slow EMA
                    baseline[app_id] = (avg * 0.8) + (current * 0.2)

                stories.append(
                    {
                        "title": name,
                        "source": "steam_spike",
                        "source_url": f"https://store.steampowered.com/app/{app_id}",
                        "score": round(score, 3),
                        "published_at": _now_utc().isoformat(),
                        "summary": f"Currently {current:,} players (baseline ~{int(avg):,})",
                        "steam_app_id": app_id,
                        "igdb_game_id": None,
                        "developer": None,
                        "thumbnail_url": f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg",
                    }
                )

                if len(stories) >= self._max_stories:
                    break

                time.sleep(0.5)

            self._save_baseline(baseline)
            logger.info("[Steam] Found %d spiking games", len(stories))

        except Exception as e:
            logger.warning("[Steam] Fetch failed: %s", e)

        return stories


# ---------------------------------------------------------------------------
# SOURCE 2 — Twitch top games by viewer count
# ---------------------------------------------------------------------------


class TwitchTrendingFetcher:
    """Fetch top games from Twitch Helix API."""

    TOP_GAMES_URL = "https://api.twitch.tv/helix/games/top"

    def __init__(self):
        from genlab_core.settings import settings

        self._client_id = settings.twitch_client_id or ""
        self._client_secret = settings.twitch_client_secret or ""

    def fetch(self) -> list[dict[str, Any]]:
        if not self._client_id or not self._client_secret:
            logger.warning("[Twitch] TWITCH_CLIENT_ID not set, skipping trending fetch")
            return []

        try:
            from niches.gaming.tools._twitch_auth import TwitchTokenManager

            token_mgr = TwitchTokenManager(self._client_id, self._client_secret)
            token = token_mgr.get_token()

            resp = requests.get(
                self.TOP_GAMES_URL,
                params={"first": 10},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Client-Id": self._client_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            games = resp.json().get("data", [])

            stories: list[dict[str, Any]] = []
            for rank, game in enumerate(games[:5], start=1):
                score = round(1.0 - (rank - 1) * 0.18, 3)  # rank 1=1.0, 5=0.28
                # Twitch provides box_art_url with {width}x{height} placeholders
                box_art = (
                    (game.get("box_art_url") or "")
                    .replace("{width}", "285")
                    .replace("{height}", "380")
                )
                stories.append(
                    {
                        "title": game["name"],
                        "source": "twitch_trending",
                        "source_url": f"https://www.twitch.tv/directory/game/{game['name'].replace(' ', '%20')}",
                        "score": max(score, 0.1),
                        "published_at": _now_utc().isoformat(),
                        "summary": f"Twitch trending rank #{rank}",
                        "steam_app_id": None,
                        "igdb_game_id": game.get("igdb_id") or game.get("id"),
                        "developer": None,
                        "thumbnail_url": box_art or None,
                    }
                )

            logger.info("[Twitch] Found %d trending games", len(stories))
            return stories

        except Exception as e:
            logger.warning("[Twitch] Fetch failed: %s", e)
            return []


# ---------------------------------------------------------------------------
# SOURCE 3 — RSS feed aggregator
# ---------------------------------------------------------------------------


class RSSFeedAggregator:
    """Fetch and score stories from gaming RSS feeds."""

    def __init__(self, feeds_config: list[dict[str, Any]]):
        self._feeds = feeds_config

    def _parse_published(self, entry: Any) -> datetime | None:
        """Extract publish date from a feedparser entry."""
        published = getattr(entry, "published_parsed", None)
        if published:
            try:
                from calendar import timegm

                ts = timegm(published)
                return datetime.fromtimestamp(ts, tz=UTC)
            except (ValueError, TypeError, OverflowError):
                pass
        return None

    def _recency_multiplier(self, published_at: datetime) -> float:
        """Score recency: 0-6h=1.0, 6-24h=0.8, 24-48h=0.5."""
        age = _now_utc() - published_at
        hours = age.total_seconds() / 3600
        if hours <= 6:
            return 1.0
        elif hours <= 24:
            return 0.8
        elif hours <= 48:
            return 0.5
        return 0.0  # Too old

    def fetch(self, trending_titles: list[str]) -> list[dict[str, Any]]:
        """Fetch RSS feeds, filter to 48h, score, return up to 10 stories."""
        cutoff = _now_utc() - timedelta(hours=48)
        normalized_trending = {_normalize_title(t) for t in trending_titles}

        stories: list[dict[str, Any]] = []

        for feed_config in self._feeds:
            feed_name = feed_config.get("name", "Unknown")
            feed_url = feed_config.get("url", "")
            feed_weight = feed_config.get("weight", 0.5)

            if not feed_url:
                continue

            try:
                parsed = feedparser.parse(feed_url)
                for entry in parsed.entries[:20]:
                    title = getattr(entry, "title", "") or ""
                    link = getattr(entry, "link", "") or ""
                    summary = getattr(entry, "summary", "") or ""

                    published_at = self._parse_published(entry)
                    if not published_at or published_at < cutoff:
                        continue

                    recency = self._recency_multiplier(published_at)
                    if recency == 0.0:
                        continue

                    base_score = feed_weight * recency

                    # Cross-source mention boost
                    norm_title = _normalize_title(title)
                    boost = 0.0
                    for trending in normalized_trending:
                        if trending in norm_title or norm_title in trending:
                            boost = 0.2
                            break

                    score = min(base_score + boost, 1.0)

                    # Truncate summary
                    if len(summary) > 200:
                        summary = summary[:197] + "..."

                    stories.append(
                        {
                            "title": title,
                            "source": "rss",
                            "source_url": link,
                            "score": round(score, 3),
                            "published_at": published_at.isoformat(),
                            "summary": summary,
                            "steam_app_id": None,
                            "igdb_game_id": None,
                            "developer": None,
                        }
                    )

                logger.debug("[RSS] %s: %d entries parsed", feed_name, len(parsed.entries))

            except Exception as e:
                logger.warning("[RSS] Failed to fetch %s: %s", feed_name, e)

        # Sort by score and return top 10
        stories.sort(key=lambda s: s["score"], reverse=True)
        logger.info("[RSS] Found %d stories from %d feeds", len(stories), len(self._feeds))
        return stories[:10]


# ---------------------------------------------------------------------------
# Orchestrator stage
# ---------------------------------------------------------------------------


class FetchGamingStories(ContentResearchStrategy):
    """Pipeline stage: fetch, merge, deduplicate gaming stories."""

    def _load_sources_config(self) -> dict[str, Any]:
        config_path = PROJECT_ROOT / "niches" / "gaming" / "config" / "sources.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _apply_source_filters(
        self,
        stories: list[dict[str, Any]],
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Reject stories matching source_filters patterns."""
        if not filters:
            return stories

        reject_title = [p.lower() for p in filters.get("reject_title_patterns", [])]
        reject_topic = [p.lower() for p in filters.get("reject_topic_patterns", [])]

        filtered = []
        for story in stories:
            title = (story.get("title") or "").lower()
            source = (story.get("source") or "").lower()

            if any(p in title for p in reject_title):
                continue
            if any(p in source for p in reject_topic):
                continue
            filtered.append(story)

        rejected = len(stories) - len(filtered)
        if rejected:
            logger.info(
                "[%s] Source filters rejected %d/%d stories",
                self.__class__.__name__,
                rejected,
                len(stories),
            )
        return filtered

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        sources_config = self._load_sources_config()
        max_stories = context.get("niche_config", {}).get("max_stories_per_run", 20)
        fetch_timeout = context.get("niche_config", {}).get("fetch_timeout_s", 45)

        # Phase 1: Steam + Twitch in parallel (independent of each other)
        steam_config = sources_config.get("steam", {})
        steam_stories: list[dict[str, Any]] = []
        twitch_stories: list[dict[str, Any]] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            steam_future = pool.submit(SteamSpikeFetcher(steam_config).fetch)
            twitch_future = pool.submit(TwitchTrendingFetcher().fetch)

            for future in concurrent.futures.as_completed(
                {steam_future, twitch_future},
                timeout=fetch_timeout,
            ):
                try:
                    result = future.result()
                    if future is steam_future:
                        steam_stories = result
                        logger.info("[FETCH] Steam returned %d stories", len(result))
                    else:
                        twitch_stories = result
                        logger.info("[FETCH] Twitch returned %d stories", len(result))
                except concurrent.futures.TimeoutError:
                    logger.error("[FETCH] Source timed out after %ds — continuing", fetch_timeout)
                except Exception as e:
                    source = "Steam" if future is steam_future else "Twitch"
                    logger.error("[FETCH] %s failed: %s — continuing", source, e)

        # Phase 2: RSS (depends on trending_titles from Steam + Twitch)
        trending_titles = [s["title"] for s in steam_stories + twitch_stories]
        rss_feeds = sources_config.get("rss_feeds", [])
        try:
            rss_stories = RSSFeedAggregator(rss_feeds).fetch(trending_titles)
        except Exception as e:
            logger.error("[FETCH] RSS failed: %s — continuing with other sources", e)
            rss_stories = []

        # Merge all stories
        all_stories = steam_stories + twitch_stories + rss_stories

        # Apply source filters (reject garbage story types)
        source_filters = sources_config.get("source_filters", {})
        all_stories = self._apply_source_filters(all_stories, source_filters)

        # 3-pass dedup via genlab-core DedupEngine
        # Load thresholds from niche config (with gaming-tuned defaults)
        niche_config = context.get("niche_config", {})
        dedup_cfg = niche_config.get("dedup", {})
        jaccard_threshold = dedup_cfg.get("jaccard_threshold", 0.80)
        tfidf_threshold = dedup_cfg.get("tfidf_threshold", 0.70)

        dedup = DedupEngine(
            jaccard_threshold=jaccard_threshold,
            tfidf_threshold=tfidf_threshold,
            url_field="source_url",
            text_field="title",
        )
        dedup_result = dedup.run(all_stories)
        deduped = dedup_result.unique

        deduped.sort(key=lambda s: s["score"], reverse=True)
        final = deduped[:max_stories]

        context["stories"] = final

        # Stats
        context.setdefault("run_stats", {})["fetch"] = {
            "steam_count": len(steam_stories),
            "twitch_count": len(twitch_stories),
            "rss_count": len(rss_stories),
            "total_raw": len(all_stories),
            "after_dedup": len(deduped),
            "final_count": len(final),
            "dedup_pass1": dedup_result.pass1_removed,
            "dedup_pass2": dedup_result.pass2_removed,
            "dedup_pass3": dedup_result.pass3_removed,
        }

        # Log top stories
        top3 = [(s["title"], s["score"]) for s in final[:3]]
        logger.info(
            "[FETCH] %d stories: %s",
            len(final),
            ", ".join(f"{t} ({s:.2f})" for t, s in top3) if top3 else "none",
        )

        return context
