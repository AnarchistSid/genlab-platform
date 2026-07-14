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
from genlab_core.cache.stable_ids import generate_story_id
from genlab_core.intelligence.dedup_engine import DedupEngine
from genlab_core.pipeline.models import FetcherStage, replace_stories
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

                # PR #506 (2026-06-24) — explicitly generate ``story_id``.
                # Without it, ``StoryCandidate.model_dump()`` (called via the
                # FetcherStage register path) fills the field with None default,
                # producing dicts whose ``story_id`` value is the literal None
                # (not absent). Downstream ``story.get("story_id", "")[:N]``
                # then crashes because the default-arg only fires on ABSENT
                # keys — the 2026-06-23 VideoGate outage shape (PR #499).
                # Setting it at the source kills the bug class architecturally,
                # matching the established pattern in TrendingVideoFetcher /
                # FetchTwitchClips / FetchRedditClips.
                published_iso = _now_utc().isoformat()
                source_url = f"https://store.steampowered.com/app/{app_id}"
                stories.append(
                    {
                        "story_id": generate_story_id(source_url, published_iso),
                        "title": name,
                        "source": "steam_spike",
                        "source_url": source_url,
                        "score": round(score, 3),
                        "published_at": published_iso,
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
    """Fetch top games from Twitch Helix API.

    Twitch's ``/helix/games/top`` returns the chart by viewer-count,
    which mixes actual games WITH non-game "categories" that Twitch
    treats as games for browse purposes: "Just Chatting", "IRL",
    "Music", "Sports", "Pools, Hot Tubs, and Beaches", etc. These
    non-game categories have no IGDB linkage (igdb_id is empty), no
    real video associated with them, and a recurring directory URL
    that re-deduplicates against yesterday's run — producing 0
    blueprints per pipeline pass (2026-06-18 outage root cause).

    We now skip any entry without an igdb_id, which is Twitch's own
    signal that the row IS a real video game and not a content
    category. Hardcoded fallback `_NON_GAME_CATEGORIES` covers the
    handful of well-known IDs Twitch assigns to non-game categories
    in case the igdb_id field is ever populated for them.
    """

    TOP_GAMES_URL = "https://api.twitch.tv/helix/games/top"
    # 2026-07-14: added streams lookup to attribute trending games to
    # a specific live streamer instead of the meaningless
    # ``twitch.tv/directory/game/X`` category URL.
    STREAMS_URL = "https://api.twitch.tv/helix/streams"

    # Twitch category IDs for known non-game "browse categories".
    # Catches the rare case where Twitch populates igdb_id for these
    # (the rows historically have igdb_id == "" but the API contract
    # isn't pinned). Lookup via curl /helix/games?name=...
    _NON_GAME_CATEGORIES = frozenset(
        {
            "509658",  # Just Chatting
            "509672",  # IRL
            "26936",  # Music
            "518203",  # Sports
            "116747788",  # Pools, Hot Tubs, and Beaches
            "509663",  # Special Events
            "417752",  # Talk Shows & Podcasts
            "509659",  # ASMR
            "743",  # Chess
            "417751",  # Travel & Outdoors
        }
    )

    def __init__(self):
        from genlab_core.settings import settings

        self._client_id = settings.twitch_client_id or ""
        self._client_secret = settings.twitch_client_secret or ""

    def _fetch_top_streamer(self, game_id: str, token: str) -> dict[str, Any] | None:
        """Fetch the top LIVE streamer for a given game_id.

        2026-07-14: added so twitch_trending stories can attribute to
        a specific creator instead of ``twitch.tv/directory/game/X``.
        Returns the top-viewer stream's user_login + user_name + title,
        or None on any failure (fail-open — parent falls back to
        directory URL).
        """
        if not game_id:
            return None
        try:
            resp = requests.get(
                self.STREAMS_URL,
                params={"game_id": game_id, "first": 1},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Client-Id": self._client_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            streams = resp.json().get("data", [])
            if not streams:
                return None
            top = streams[0]
            if not top.get("user_login"):
                return None
            return {
                "user_login": top["user_login"],
                "user_name": top.get("user_name") or top["user_login"],
                "title": (top.get("title") or "").strip()[:120],
            }
        except Exception as exc:
            logger.debug(
                "[Twitch] top-streamer lookup failed for game_id=%s: %s",
                game_id,
                exc,
            )
            return None

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
                params={"first": 20},  # over-fetch so we can keep 5 real games after filtering
                headers={
                    "Authorization": f"Bearer {token}",
                    "Client-Id": self._client_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            games = resp.json().get("data", [])

            # Filter out non-game categories. Two-pass: drop by hardcoded
            # category-ID list AND drop entries with empty igdb_id (the
            # canonical "I'm not a real game" signal from Twitch).
            real_games: list[dict[str, Any]] = []
            skipped_non_games: list[str] = []
            for game in games:
                game_id = str(game.get("id", ""))
                igdb_id = str(game.get("igdb_id", "")).strip()
                if game_id in self._NON_GAME_CATEGORIES or not igdb_id:
                    skipped_non_games.append(game.get("name", "?"))
                    continue
                real_games.append(game)

            if skipped_non_games:
                logger.info(
                    "[Twitch] Skipped %d non-game categories (no IGDB id or known browse category): %s",
                    len(skipped_non_games),
                    ", ".join(skipped_non_games[:5]),
                )

            stories: list[dict[str, Any]] = []
            for rank, game in enumerate(real_games[:5], start=1):
                score = round(1.0 - (rank - 1) * 0.18, 3)  # rank 1=1.0, 5=0.28
                # Twitch provides box_art_url with {width}x{height} placeholders
                box_art = (
                    (game.get("box_art_url") or "")
                    .replace("{width}", "285")
                    .replace("{height}", "380")
                )
                # PR #506 — explicit story_id (see SteamSpikeFetcher above
                # for the full rationale; same root cause + same fix shape).
                published_iso = _now_utc().isoformat()

                # 2026-07-14: fetch the top LIVE streamer for this game
                # so attribution points at a real human creator instead
                # of the meaningless directory URL. Directory URLs were
                # rejected by the compliance gate (commit 0b7a3e14) —
                # without this fetch, Twitch trending games would fail
                # attribution entirely + fall through to warn/block.
                # Fail-open: if the streams API call fails, keep the
                # directory URL as source_url so the story is still
                # created (compliance gate will still reject it as
                # attribution, but at least the story exists for
                # potential clip-fetch later).
                top_streamer = self._fetch_top_streamer(
                    game_id=str(game.get("id", "")),
                    token=token,
                )
                if top_streamer:
                    source_url = f"https://www.twitch.tv/{top_streamer['user_login']}"
                    source_channel_title = top_streamer.get("user_name") or top_streamer["user_login"]
                    stream_title = top_streamer.get("title", "") or f"Live: {game['name']}"
                else:
                    source_url = (
                        f"https://www.twitch.tv/directory/game/{game['name'].replace(' ', '%20')}"
                    )
                    source_channel_title = ""
                    stream_title = ""

                stories.append(
                    {
                        "story_id": generate_story_id(source_url, published_iso),
                        "title": game["name"],
                        "source": "twitch_trending",
                        "source_url": source_url,
                        "video_url": source_url,  # parity with format_source_attribution's URL fallback
                        "source_channel_title": source_channel_title,
                        "score": max(score, 0.1),
                        "published_at": published_iso,
                        "summary": (
                            f"{stream_title} — Twitch trending rank #{rank}"
                            if stream_title
                            else f"Twitch trending rank #{rank}"
                        ),
                        "steam_app_id": None,
                        "igdb_game_id": game.get("igdb_id") or game.get("id"),
                        "developer": None,
                        "thumbnail_url": box_art or None,
                    }
                )

            logger.info("[Twitch] Found %d trending games (after non-game filter)", len(stories))
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

                    # PR #506 — explicit story_id (see SteamSpikeFetcher above
                    # for the full rationale; same root cause + same fix shape).
                    published_iso = published_at.isoformat()
                    stories.append(
                        {
                            "story_id": generate_story_id(link, published_iso),
                            "title": title,
                            "source": "rss",
                            "source_url": link,
                            "score": round(score, 3),
                            "published_at": published_iso,
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


class FetchGamingStories(FetcherStage, ContentResearchStrategy):
    """Pipeline stage: fetch, merge, deduplicate gaming stories.

    Hybrid producer+consumer: fetches 3 local sources (Steam spike, Twitch
    trending, RSS), merges with upstream stories already in the pool, then
    dedupes + sorts + truncates to ``max_stories`` (final step is REPLACE
    semantics — see ``replace_stories(...)`` at the end of ``execute()``).
    """

    # P1 phase-3, 2026-06-19 — local fetcher emits 3 source values, but only
    # 2 belong in the producer registry: ``steam_spike`` and ``twitch_trending``
    # are gaming-by-construction (auto-trust). ``"rss"`` is INTENTIONALLY NOT
    # in this set — FilterGamingStories' design treats RSS as the one path
    # that MUST go through the keyword filter (gaming-news RSS feeds can
    # carry off-topic noise like phone deals, movie news, etc.). Adding
    # ``"rss"`` here would auto-pass it and break the keyword filter.
    EMITTED_SOURCES = frozenset({"steam_spike", "twitch_trending"})

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

        # Merge all locally-fetched stories
        all_stories = steam_stories + twitch_stories + rss_stories

        # Apply source filters (reject garbage story types)
        source_filters = sources_config.get("source_filters", {})
        all_stories = self._apply_source_filters(all_stories, source_filters)

        # 2026-06-19 FIX: merge with upstream-fetched stories instead of
        # replacing them. The pipeline runs FetchTrendingVideos +
        # FetchTwitchClips + FetchRedditClips BEFORE this stage, each of
        # which appends to ``context["stories"]`` via the canonical
        # ``context["stories"] = existing + new_stories`` pattern. Before
        # this fix, ``context["stories"] = final`` REPLACED those upstream
        # stories — silently dropping ~45 real video sources per run (20
        # content_pool YouTube clips + 25 Twitch clips), leaving only the
        # local Steam-spike + Twitch-chart commentary as candidates.
        # Operator observed this as "CR is producing useless content" —
        # every shipped hook was about Twitch chart positions instead of
        # actual gameplay clips.
        #
        # Schema-normalize upstream stories before merging: upstream
        # fetchers (FetchTrendingVideos, FetchTwitchClips) use a video-
        # centric schema that may omit ``score`` (default 0.5) and other
        # fields the local fetchers always populate. Without this normalize
        # step the downstream ``sort(key=lambda s: s["score"])`` raises
        # KeyError and the whole stage fails. Pin the contract: every
        # story merged here MUST have ``score`` and ``source_url`` so
        # dedup + sort work uniformly.
        upstream_stories = context.get("stories", [])
        for s in upstream_stories:
            s.setdefault("score", 0.5)
            s.setdefault("source_url", "")
            s.setdefault("title", "")
        all_stories = upstream_stories + all_stories

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

        # Use .get with a default — belt-and-suspenders for the case where
        # a story slipped past the schema-normalize above (e.g., via
        # someone else's upstream fetcher in a future PR).
        deduped.sort(key=lambda s: s.get("score", 0.0), reverse=True)
        final = deduped[:max_stories]

        # P1 phase-3: intent-revealing REPLACE — after dedup+sort+truncate this
        # is a narrowing operation, not a merge. The named function makes the
        # filter-vs-fetcher semantics explicit (PR #358's bug class fix).
        replace_stories(context, final)

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
