"""ClutchWire sports news fetcher.

Fetches from ESPN public API (scoreboard + news) and RSS feeds.
All ESPN API field access uses .get() to guard against missing keys.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


@dataclass
class SportsStoryItem:
    """Normalized sports story from any source."""

    title: str
    source: str
    source_url: str = ""
    sport: str = ""
    league: str = ""
    published_at: str = ""
    fetched_at: str = ""
    summary: str = ""
    teams: list[str] = field(default_factory=list)
    players: list[str] = field(default_factory=list)
    game_type: str = "regular_season"
    is_live: bool = False
    upvotes: int = 0
    comment_count: int = 0
    novelty_score: float = 0.5
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ESPNFetcher:
    """Fetch from ESPN public API (no auth required)."""

    def __init__(self, config: dict) -> None:
        self._endpoints = config.get("espn_api", {})
        self._timeout = self._endpoints.get("timeout_seconds", 10)

    def fetch_scoreboard(self) -> list[SportsStoryItem]:
        """Fetch live/recent scores from ESPN scoreboard endpoints."""
        items: list[SportsStoryItem] = []
        scoreboard_urls = self._endpoints.get("scoreboard", {})

        for league, url in scoreboard_urls.items():
            try:
                items.extend(self._fetch_scoreboard_league(league, url))
            except Exception:
                logger.exception("[ESPN] Failed scoreboard fetch for %s", league)

        logger.info("[ESPN] Fetched %d scoreboard items", len(items))
        return items

    def _fetch_scoreboard_league(self, league: str, url: str) -> list[SportsStoryItem]:
        items: list[SportsStoryItem] = []
        now_iso = datetime.now(UTC).isoformat()

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        for event in data.get("events", []):
            name = event.get("name", "")
            status_obj = event.get("status", {})
            status_type = status_obj.get("type", {})
            game_state = status_type.get("state", "")

            # Only include Final ("post") and Live ("in") games.
            # Scheduled ("pre") games have no highlight content and
            # should never enter the pipeline (CLAUDE.md rule).
            if game_state == "pre":
                logger.debug("[ESPN] Skipping scheduled game: %s", name)
                continue

            is_live = game_state == "in"
            game_type = self._classify_game_type(event)

            teams: list[str] = []
            for comp in event.get("competitions", []):
                for competitor in comp.get("competitors", []):
                    team_obj = competitor.get("team", {})
                    team_name = team_obj.get("displayName", "")
                    if team_name:
                        teams.append(team_name)

            items.append(
                SportsStoryItem(
                    title=name,
                    source="espn_scoreboard",
                    source_url=event.get("links", [{}])[0].get("href", "")
                    if event.get("links")
                    else "",
                    sport=self._league_to_sport(league),
                    league=league,
                    published_at=event.get("date", ""),
                    fetched_at=now_iso,
                    teams=teams,
                    game_type=game_type,
                    is_live=is_live,
                    summary=status_obj.get("type", {}).get("shortDetail", ""),
                )
            )

        return items

    def fetch_news(self) -> list[SportsStoryItem]:
        """Fetch news articles from ESPN news endpoints."""
        items: list[SportsStoryItem] = []
        news_urls = self._endpoints.get("news", {})

        for league, url in news_urls.items():
            try:
                items.extend(self._fetch_news_league(league, url))
            except Exception:
                logger.exception("[ESPN] Failed news fetch for %s", league)

        logger.info("[ESPN] Fetched %d news items", len(items))
        return items

    def _fetch_news_league(self, league: str, url: str) -> list[SportsStoryItem]:
        items: list[SportsStoryItem] = []
        now_iso = datetime.now(UTC).isoformat()

        with httpx.Client(timeout=self._timeout) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        for article in data.get("articles", []):
            headline = article.get("headline", "")
            description = article.get("description", "")
            published = article.get("published", "")

            links = article.get("links", {})
            web_link = links.get("web", {}).get("href", "") if isinstance(links, dict) else ""

            items.append(
                SportsStoryItem(
                    title=headline,
                    source="espn_news",
                    source_url=web_link,
                    sport=self._league_to_sport(league),
                    league=league,
                    published_at=published,
                    fetched_at=now_iso,
                    summary=description,
                )
            )

        return items

    @staticmethod
    def _classify_game_type(event: dict) -> str:
        season = event.get("season", {})
        slug = season.get("slug", "")
        if "playoff" in slug or "postseason" in slug:
            return "playoff_game"
        if "preseason" in slug:
            return "preseason"
        return "regular_season"

    @staticmethod
    def _league_to_sport(league: str) -> str:
        return {
            "nba": "basketball",
            "nfl": "football",
            "mlb": "baseball",
            "epl": "soccer",
        }.get(league, league)


class RSSFetcher:
    """Fetch stories from RSS feeds."""

    def __init__(self, config: dict) -> None:
        self._tiers = {
            "tier_1": config.get("tier_1", {}),
            "tier_2": config.get("tier_2", {}),
            "tier_3": config.get("tier_3", {}),
        }

    def fetch_all(self) -> list[SportsStoryItem]:
        items: list[SportsStoryItem] = []
        now_iso = datetime.now(UTC).isoformat()

        for tier_name, tier_config in self._tiers.items():
            for source in tier_config.get("sources", []):
                if source.get("type") != "rss":
                    continue
                url = source.get("url", "")
                name = source.get("name", "")
                weight = source.get("weight", 1.0)
                if not url:
                    continue
                try:
                    items.extend(self._fetch_feed(url, name, weight, tier_name, now_iso))
                except Exception:
                    logger.exception("[RSS] Failed to fetch %s", name)

        logger.info("[RSS] Fetched %d items from all tiers", len(items))
        return items

    @staticmethod
    def _fetch_feed(
        url: str,
        name: str,
        weight: float,
        tier: str,
        now_iso: str,
    ) -> list[SportsStoryItem]:
        feed = feedparser.parse(url)
        items: list[SportsStoryItem] = []

        for entry in feed.get("entries", []):
            published = ""
            if hasattr(entry, "published"):
                published = entry.published

            items.append(
                SportsStoryItem(
                    title=entry.get("title", ""),
                    source=f"rss_{name.lower().replace(' ', '_')}",
                    source_url=entry.get("link", ""),
                    published_at=published,
                    fetched_at=now_iso,
                    summary=entry.get("summary", ""),
                    extra={"tier": tier, "weight": weight},
                )
            )

        return items


def fetch_all_sports_news(config: dict | None = None) -> list[dict[str, Any]]:
    """Entry point: fetch from all sports sources, return normalized dicts.

    **2026-06-18 fix**: ESPN ``fetch_news`` is now opt-in (off by
    default) because it returns TEXT-ONLY articles — Premier League
    transfer rumours, op-eds, weekly previews — that have no video
    field. They scored 0.4 on visual_potential (default for unknown),
    cleared the 0.30 gate, displaced actual video candidates in the
    top-N cut, and then died at DownloadTopVideos because there's no
    video to download. Result on 2026-06-18: 24 ESPN news articles
    fetched per run, 5 made it past scoring, 0 had video → 0
    blueprints produced for two consecutive days.

    ClutchWire's `CLAUDE.md` is explicit: ``video_gate: require`` +
    ``fallback_to_text_render: false``. ESPN scoreboard (game
    headlines + scores) is still fetched because those headlines feed
    the video-discovery search; only the text news endpoint is gated.

    Operators who want the previous behaviour can set
    ``fetch_espn_news: true`` in ``config/sources.yaml`` (top-level).
    """
    if config is None:
        config = _load_yaml(NICHE_ROOT / "config" / "sources.yaml")

    espn = ESPNFetcher(config)
    rss = RSSFetcher(config)

    all_items: list[SportsStoryItem] = []
    all_items.extend(espn.fetch_scoreboard())
    # 2026-06-18: opt-in flag, default false (see docstring above).
    if (config or {}).get("fetch_espn_news", False):
        all_items.extend(espn.fetch_news())
        logger.info("[fetch] ESPN news enabled via sources.yaml flag")
    else:
        logger.debug(
            "[fetch] ESPN news skipped (default-off since 2026-06-18 — "
            "text-only articles polluted candidate pool)"
        )
    all_items.extend(rss.fetch_all())

    logger.info("[fetch] Total stories fetched: %d", len(all_items))
    return [item.to_dict() for item in all_items]
