"""Pipeline stage: Fetch official game trailers from Steam Storefront API.

Returns direct MP4 URLs hosted on Valve CDN — no authentication required.
No YouTube quota consumed. ~200 req/5 min rate limit.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import requests

from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)

# Popular Steam app IDs for fallback (if no trending games in context)
_DEFAULT_APP_IDS = [
    730,  # Counter-Strike 2
    570,  # Dota 2
    271590,  # Grand Theft Auto V
    1172470,  # Apex Legends
    252490,  # Rust
    578080,  # PUBG
    1938090,  # Call of Duty HQ
]


def _fetch_trailers_for_app(app_id: int, max_trailers: int = 2) -> list[dict]:
    """Fetch official game trailers from Steam Storefront API."""
    try:
        r = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": str(app_id), "filters": "movies,basic"},
            timeout=10,
        )
        data = r.json().get(str(app_id), {})
        if not data.get("success"):
            return []
        app_data = data.get("data", {})
        game_name = app_data.get("name", f"App {app_id}")
        results = []
        for movie in app_data.get("movies", [])[:max_trailers]:
            mp4 = movie.get("mp4", {})
            # Prefer 480p (~10-30MB, fine for reel), fall back to max
            url = mp4.get("480") or mp4.get("max")
            if not url:
                continue
            results.append(
                {
                    "title": f"{game_name} - {movie.get('name', 'Trailer')}",
                    "clip_url": url,
                    "source": "steam_trailer",
                    "app_id": app_id,
                    "game_name": game_name,
                }
            )
        return results
    except Exception as e:
        logger.warning("[SteamTrailers] Fetch failed for app %d: %s", app_id, e)
        return []


class FetchSteamTrailers:
    """Pipeline stage: fetch official game trailers from Steam.

    Returns direct MP4 URLs from Valve CDN. No auth required.
    Runs before DownloadTopVideos — provides video candidates without YouTube quota.
    """

    def execute(self, context: StageContext) -> StageContext:
        niche_id = context.get("niche_id", "")
        if niche_id != "gaming":
            return context

        sources_config = context.get("sources_config", {})
        steam_cfg = sources_config.get("steam_trailers", {})
        if steam_cfg.get("enabled") is False:
            return context

        max_per_game = steam_cfg.get("max_per_game", 2)

        # Use trending game app IDs from context (e.g. from Steam spike detector)
        # or fall back to popular defaults
        app_ids = context.get("trending_steam_app_ids", _DEFAULT_APP_IDS[:5])

        all_trailers: list[dict] = []
        for app_id in app_ids[:5]:
            trailers = _fetch_trailers_for_app(int(app_id), max_per_game)
            all_trailers.extend(trailers)
            time.sleep(0.3)  # Respect Steam rate limit

        logger.info(
            "[SteamTrailers] %d trailers from %d games", len(all_trailers), len(app_ids[:5])
        )

        if all_trailers:
            from genlab_core.cache.stable_ids import generate_story_id

            now_iso = datetime.now(UTC).isoformat()
            new_stories = []
            for t in all_trailers[:8]:  # Cap at 8 trailers
                sid = generate_story_id(t["clip_url"], now_iso)
                new_stories.append(
                    {
                        "story_id": sid,
                        "title": t["title"],
                        "source": "steam_trailer",
                        "source_url": t["clip_url"],
                        "canonical_url": t["clip_url"],
                        "published_at": now_iso,
                        "fetched_at": now_iso,
                        "summary": f"Official trailer for {t.get('game_name', '')}",
                        "niche_id": niche_id,
                        "video_source": "steam",
                        "_trending_video": True,
                        "_clip_url": t["clip_url"],
                        "source_mention_count": 2,
                    }
                )

            existing = context.get("stories", [])
            existing_urls = {s.get("source_url") for s in existing}
            new_stories = [s for s in new_stories if s["source_url"] not in existing_urls]
            context["stories"] = existing + new_stories

        run_stats = context.setdefault("run_stats", {})
        run_stats["steam_trailers_found"] = len(all_trailers)
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
