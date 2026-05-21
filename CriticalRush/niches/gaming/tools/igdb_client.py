"""IGDB (Internet Game Database) client for game metadata enrichment.

Uses the Twitch/IGDB API via Apicalypse query syntax.
All methods return None when not configured — never raises on missing creds.

Usage:
    client = IGDBClient()
    game = client.search_game("Elden Ring")
    enriched = client.enrich_story(story_dict, context=pipeline_context)
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from niches.gaming.tools._twitch_auth import TwitchTokenManager

logger = logging.getLogger(__name__)


class IGDBClient:
    """Query IGDB for game metadata and enrich pipeline stories."""

    API_BASE = "https://api.igdb.com/v4"

    def __init__(self):
        from genlab_core.ratelimit.token_bucket import TokenBucket
        from genlab_core.settings import settings

        client_id = settings.twitch_client_id or ""
        client_secret = settings.twitch_client_secret or ""
        self._client_id = client_id
        self._configured = bool(client_id and client_secret)
        self._token_manager = TwitchTokenManager(client_id, client_secret)
        self._rate_limiter = TokenBucket(rate=4.0, burst=4.0)  # IGDB: 4 req/sec

        if not self._configured:
            logger.warning(
                "[IGDB] Not configured — set TWITCH_CLIENT_ID and "
                "TWITCH_CLIENT_SECRET to enable game enrichment"
            )

    def _headers(self) -> dict[str, str]:
        """Build request headers with a fresh token."""
        token = self._token_manager.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Client-Id": self._client_id,
            "Content-Type": "text/plain",
        }

    def search_game(self, title: str) -> dict[str, Any] | None:
        """Search IGDB for a game by title.

        Returns a dict with keys:
            igdb_game_id, steam_app_id, developer, cover_url, rating
        or None if no results or not configured.
        """
        if not self._configured:
            return None
        if not title:
            return None

        try:
            query = (
                f"fields id,name,slug,cover.url,"
                f"involved_companies.company.name,"
                f"involved_companies.developer,"
                f"external_games.category,"
                f"external_games.uid,"
                f"total_rating;"
                f' search "{title}"; limit 3;'
            )

            self._rate_limiter.acquire()
            resp = requests.post(
                f"{self.API_BASE}/games",
                headers=self._headers(),
                data=query,
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()

            if not results:
                logger.info("[IGDB] No results for '%s'", title)
                return None

            # Prefer exact title match (case-insensitive)
            chosen = results[0]
            for r in results:
                if r.get("name", "").lower() == title.lower():
                    chosen = r
                    break

            # Extract steam_app_id from external_games where category == 1
            steam_app_id = None
            for ext in chosen.get("external_games", []):
                if ext.get("category") == 1:
                    steam_app_id = ext.get("uid")
                    break

            # Extract developer name
            developer = None
            for ic in chosen.get("involved_companies", []):
                if ic.get("developer"):
                    company = ic.get("company", {})
                    developer = company.get("name")
                    break

            # Extract cover URL
            cover_url = None
            cover = chosen.get("cover")
            if cover and cover.get("url"):
                cover_url = (
                    "https:" + cover["url"] if cover["url"].startswith("//") else cover["url"]
                )

            return {
                "igdb_game_id": str(chosen["id"]),
                "steam_app_id": steam_app_id,
                "developer": developer,
                "cover_url": cover_url,
                "rating": chosen.get("total_rating"),
            }

        except Exception as e:
            logger.warning("[IGDB] Search failed for '%s': %s", title, e)
            return None

    def enrich_story(
        self, story: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Enrich a story dict with IGDB game metadata.

        If context is provided, checks the use_gaming_clip_sourcer feature
        flag — if False, returns story unchanged (the flag gates the entire
        IGDB/clip subsystem).

        Never raises — returns story unchanged on any failure.
        """
        if context is not None:
            flags = context.get("feature_flags", {})
            if not flags.get("use_gaming_clip_sourcer", False):
                return story

        title = story.get("title", "")
        if not title:
            return story

        result = self.search_game(title)
        if result is None:
            logger.info("[IGDB] No match found for '%s'", title)
            return story

        # Merge fields into story
        for key, value in result.items():
            if value is not None:
                story[key] = value

        logger.info(
            "[IGDB] Enriched '%s': steam_app_id=%s, igdb_id=%s",
            title,
            result.get("steam_app_id"),
            result.get("igdb_game_id"),
        )
        return story
