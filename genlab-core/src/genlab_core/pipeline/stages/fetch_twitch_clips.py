"""Pipeline stage: Fetch trending Twitch clips for gaming content.

Returns direct MP4 CDN URLs — no yt-dlp or YouTube quota needed.
Requires TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in environment.
Rate: 800 req/min, no daily cap.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from genlab_core.pipeline.models import FetcherStage, merge_stories
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)

# Popular game IDs on Twitch (fallback if IGDB enrichment not available)
_DEFAULT_GAME_IDS = [
    # NOTE: "509658" (Just Chatting) REMOVED — it's an IRL category, not a game.
    # It's always #1 on Twitch, causing 24% of gaming content to be non-gaming spam.
    "32982",  # Grand Theft Auto V
    "516575",  # Valorant
    "21779",  # League of Legends
    "33214",  # Fortnite
    "32399",  # Counter-Strike 2
    "263490",  # Rust
    "518203",  # Apex Legends
    "511224",  # Call of Duty: Warzone
    "29595",  # Dota 2
    "27471",  # Minecraft
]


def _get_twitch_app_token(client_id: str, client_secret: str) -> str | None:
    """Get Twitch app access token via client credentials flow."""
    try:
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        logger.error("[TwitchClips] Token fetch failed: %s", e)
        return None


def _fetch_clips_for_game(
    game_id: str,
    headers: dict,
    max_clips: int = 5,
    lookback_days: int = 7,
) -> list[dict]:
    """Fetch top clips for a game from Twitch Clips API."""
    started_at = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()
    try:
        r = requests.get(
            "https://api.twitch.tv/helix/clips",
            headers=headers,
            params={
                "game_id": game_id,
                "first": max_clips,
                "started_at": started_at,
            },
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning("[TwitchClips] API returned %d for game %s", r.status_code, game_id)
            return []
        results = []
        for clip in r.json().get("data", []):
            clip.get("thumbnail_url", "")
            # Twitch CDN pattern: thumbnail URL → MP4
            # e.g. https://clips-media-assets2.twitch.tv/.../AT-xxx-preview-480x272.jpg
            # MP4: split on "-preview-", take [0], add ".mp4"
            # Use the clip page URL — yt-dlp handles Twitch natively.
            # The old CDN MP4 thumbnail trick (split on "-preview-") no
            # longer works; Twitch moved to a new CDN structure.
            clip_page_url = clip.get("url", "")
            if not clip_page_url:
                continue
            results.append(
                {
                    "title": clip.get("title", ""),
                    "clip_url": clip_page_url,
                    "url": clip_page_url,
                    "view_count": clip.get("view_count", 0),
                    "game_id": game_id,
                    "broadcaster": clip.get("broadcaster_name", ""),
                    "duration": clip.get("duration", 0),
                    "source": "twitch_clips",
                    "created_at": clip.get("created_at", ""),
                }
            )
        return results
    except Exception as e:
        logger.warning("[TwitchClips] Fetch failed for game %s: %s", game_id, e)
        return []


class FetchTwitchClips(FetcherStage):
    """Pipeline stage: fetch trending Twitch clips for gaming niche.

    Returns direct MP4 URLs from Twitch CDN — no YouTube quota needed.
    Clips are added as stories with pre-filled clip_url for DownloadTopVideos.
    """

    # P1, 2026-06-19 — declare emitted source values so downstream filters
    # (e.g. FilterGamingStories) can derive their trust list from producers
    # instead of maintaining a hand-edited frozenset. Closes PR #360's bug
    # class permanently.
    EMITTED_SOURCES = frozenset({"twitch_clips"})

    def execute(self, context: StageContext) -> StageContext:
        niche_id = context.get("niche_id", "")
        if niche_id != "gaming":
            return context

        client_id = os.environ.get("TWITCH_CLIENT_ID")
        client_secret = os.environ.get("TWITCH_CLIENT_SECRET")
        if not client_id or not client_secret:
            logger.warning("[TwitchClips] TWITCH_CLIENT_ID/SECRET not set, skipping")
            return context

        sources_config = context.get("sources_config", {})
        twitch_cfg = sources_config.get("twitch_clips", {})
        if twitch_cfg.get("enabled") is False:
            return context

        max_clips = twitch_cfg.get("max_clips_per_game", 5)
        min_views = twitch_cfg.get("min_view_count", 1000)
        lookback_days = twitch_cfg.get("lookback_days", 7)

        # Get Twitch app token
        token = _get_twitch_app_token(client_id, client_secret)
        if not token:
            return context

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id,
        }

        # Use IGDB-enriched game IDs from context if available, else defaults
        game_ids = context.get("trending_game_ids", _DEFAULT_GAME_IDS[:5])

        all_clips: list[dict] = []
        for game_id in game_ids[:5]:
            clips = _fetch_clips_for_game(game_id, headers, max_clips, lookback_days)
            all_clips.extend(clips)
            time.sleep(0.1)  # Be nice to Twitch API

        # Filter by min views and sort
        all_clips = [c for c in all_clips if c.get("view_count", 0) >= min_views]
        all_clips.sort(key=lambda x: x.get("view_count", 0), reverse=True)
        logger.info("[TwitchClips] %d clips passed view filter (>=%d)", len(all_clips), min_views)

        if all_clips:
            from genlab_core.cache.stable_ids import generate_story_id

            now_iso = datetime.now(UTC).isoformat()
            new_stories = []
            for clip in all_clips[:10]:  # Cap at 10 clips
                clip_url = clip["clip_url"]
                sid = generate_story_id(clip_url, now_iso)
                broadcaster = clip.get("broadcaster", "")
                new_stories.append(
                    {
                        "story_id": sid,
                        "title": clip["title"],
                        "source": "twitch_clips",
                        "source_url": clip.get("url", clip_url),
                        "canonical_url": clip_url,
                        "published_at": clip.get("created_at", now_iso),
                        "fetched_at": now_iso,
                        "summary": f"Twitch clip by {broadcaster}",
                        "view_count": clip.get("view_count", 0),
                        "duration_seconds": clip.get("duration", 0),
                        "niche_id": niche_id,
                        "video_source": "twitch",
                        # Attribution: Twitch Creator Terms require crediting streamer
                        "broadcaster": broadcaster,
                        "attribution": f"Clip from {broadcaster} on Twitch" if broadcaster else "",
                        # Pre-filled clip info so DownloadTopVideos can use directly
                        "_trending_video": True,
                        "_clip_url": clip_url,
                        "source_mention_count": 2,
                    }
                )

            existing = context.get("stories", [])
            existing_urls = {s.get("source_url") for s in existing}
            new_stories = [s for s in new_stories if s["source_url"] not in existing_urls]
            # P1: ``merge_stories`` validates each item against StoryCandidate
            # and replaces the copy-pasted ``context["stories"] = existing + new``
            # convention with an intent-revealing function name. PR #358's
            # REPLACE-not-MERGE bug class becomes impossible here.
            merge_stories(context, new_stories)

        run_stats = context.setdefault("run_stats", {})
        run_stats["twitch_clips_found"] = len(all_clips)
        return context

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.execute(context)
