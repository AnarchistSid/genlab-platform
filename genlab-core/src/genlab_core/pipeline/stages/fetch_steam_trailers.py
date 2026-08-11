"""Pipeline stage: Fetch official game trailers from Steam Storefront API.

Returns direct MP4 URLs hosted on Valve CDN — no authentication required.
No YouTube quota consumed. ~200 req/5 min rate limit.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any, Final

import requests

from genlab_core.pipeline.models import FetcherStage, merge_stories
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


# 2026-08-12: Writer requires >=40 chars of writable context in
# `summary` OR the story gets silently dropped at QC. Previously this
# fetcher emitted "Official trailer for <game_name>" which for short
# game names fell below the floor. Now we prefer Steam's
# short_description (always well-formed marketing prose from the
# store page) and fall back to a synthesized string including
# app_id + trailer type as padding. Sibling fix to
# _build_twitch_summary in fetch_twitch_clips.py.
_WRITER_MIN_CONTEXT_CHARS: Final[int] = 40


def _build_steam_summary(
    *,
    game_name: str,
    movie_name: str,
    short_description: str,
    app_id: int,
) -> str:
    """Construct a writer-usable summary >=40 chars from Steam
    Storefront metadata. Prefers short_description (real prose)
    when present; falls back to synthesized string for the rare
    case where Steam returns a game with no short_description."""
    sd = (short_description or "").strip()
    if sd:
        return f"{game_name} — {movie_name}. {sd}"[:500]
    # Synthesized fallback — pad with trailer type + app_id.
    return (
        f"Official {movie_name} trailer for {game_name} "
        f"(Steam app {app_id})"
    )


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
        # 2026-08-12: pass through the game's short_description so the
        # downstream summary can clear the writer's >=40 char floor
        # even when the game name is very short (e.g. "Wu Kong").
        # Steam's `short_description` is one-line marketing prose
        # that's always well-formed. See sibling fix in fetch_twitch_clips.py.
        short_description = (app_data.get("short_description") or "").strip()
        results = []
        for movie in app_data.get("movies", [])[:max_trailers]:
            mp4 = movie.get("mp4", {})
            # Prefer 480p (~10-30MB, fine for reel), fall back to max
            url = mp4.get("480") or mp4.get("max")
            if not url:
                continue
            movie_name = movie.get("name", "Trailer")
            results.append(
                {
                    "title": f"{game_name} - {movie_name}",
                    "clip_url": url,
                    "source": "steam_trailer",
                    "app_id": app_id,
                    "game_name": game_name,
                    "movie_name": movie_name,
                    "short_description": short_description,
                }
            )
        return results
    except Exception as e:
        logger.warning("[SteamTrailers] Fetch failed for app %d: %s", app_id, e)
        return []


class FetchSteamTrailers(FetcherStage):
    """Pipeline stage: fetch official game trailers from Steam.

    Returns direct MP4 URLs from Valve CDN. No auth required.
    Runs before DownloadTopVideos — provides video candidates without YouTube quota.
    """

    # P1, 2026-06-19 — declare emitted source for the producer registry.
    EMITTED_SOURCES = frozenset({"steam_trailer"})

    def execute(self, context: StageContext) -> StageContext:
        niche_id = context.get("niche_id", "")
        if niche_id != "gaming":
            return context

        sources_config = context.get("sources_config", {})
        steam_cfg = sources_config.get("steam_trailers", {})
        if steam_cfg.get("enabled") is False:
            return context

        max_per_game = steam_cfg.get("max_per_game", 2)
        # max_games is config-driven (sources.yaml `steam_trailers.max_games`);
        # default stays 5 for backward compat. 2026-06-28: raised gaming's
        # value to 8 after audit surfaced this as a hidden cap that compounded
        # with the filter-stage cap (PR #621) to starve gaming's daily
        # blueprints. Rate-limit cost is negligible: 0.3s × max_games sleep
        # = ~2.4s.
        max_games = steam_cfg.get("max_games", 5)

        # Use trending game app IDs from context (e.g. from Steam spike detector)
        # or fall back to popular defaults
        app_ids = context.get("trending_steam_app_ids", _DEFAULT_APP_IDS[:max_games])

        all_trailers: list[dict] = []
        for app_id in app_ids[:max_games]:
            trailers = _fetch_trailers_for_app(int(app_id), max_per_game)
            all_trailers.extend(trailers)
            time.sleep(0.3)  # Respect Steam rate limit

        logger.info(
            "[SteamTrailers] %d trailers from %d games",
            len(all_trailers),
            len(app_ids[:max_games]),
        )

        if all_trailers:
            from genlab_core.cache.stable_ids import generate_story_id

            now_iso = datetime.now(UTC).isoformat()
            new_stories = []
            for t in all_trailers[:8]:  # Cap at 8 trailers
                sid = generate_story_id(t["clip_url"], now_iso)
                summary = _build_steam_summary(
                    game_name=t.get("game_name", ""),
                    movie_name=t.get("movie_name", "Trailer"),
                    short_description=t.get("short_description", ""),
                    app_id=t.get("app_id", 0),
                )
                new_stories.append(
                    {
                        "story_id": sid,
                        "title": t["title"],
                        "source": "steam_trailer",
                        "source_url": t["clip_url"],
                        "canonical_url": t["clip_url"],
                        "published_at": now_iso,
                        "fetched_at": now_iso,
                        "summary": summary,
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
            # P1, 2026-06-19 — intent-revealing merge call. Schema-validates
            # each new story; defaults score=0.5 if upstream omits it (PR #359
            # bug class fix).
            merge_stories(context, new_stories)

        run_stats = context.setdefault("run_stats", {})
        run_stats["steam_trailers_found"] = len(all_trailers)
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
