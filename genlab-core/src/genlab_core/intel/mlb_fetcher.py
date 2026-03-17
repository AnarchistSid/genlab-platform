"""MLB StatsAPI highlight fetcher — no authentication required.

Returns direct CDN MP4 URLs for baseball highlights.
Schedule: GET https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD
Content:  GET https://statsapi.mlb.com/api/v1/game/{gamePk}/content

MP4 URLs at mediadownloads.mlb.com — prefer 2500K bitrate.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
_CONTENT_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/content"


def fetch_mlb_highlights(
    max_highlights_per_game: int = 3,
    max_age_hours: int = 24,
) -> list[dict[str, Any]]:
    """Fetch today's MLB game highlights with direct MP4 URLs.

    Returns story dicts with ``video_url`` pointing to CDN MP4.
    No API key required.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            _SCHEDULE_URL,
            params={"sportId": 1, "date": today},
            timeout=10,
        )
        resp.raise_for_status()
        dates = resp.json().get("dates", [])
    except Exception as e:
        logger.warning("[MLB] Schedule fetch failed: %s", e)
        return []

    if not dates:
        logger.info("[MLB] No games scheduled for %s", today)
        return []

    results: list[dict[str, Any]] = []
    games = dates[0].get("games", [])

    for game in games:
        game_pk = game.get("gamePk")
        status = game.get("status", {}).get("abstractGameState", "")
        if status not in ("Final", "Live"):
            continue

        away = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
        home = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "")

        try:
            content_resp = requests.get(
                _CONTENT_URL.format(game_pk=game_pk),
                timeout=10,
            )
            content_resp.raise_for_status()
            content = content_resp.json()
        except Exception as e:
            logger.debug("[MLB] Content fetch failed for game %s: %s", game_pk, e)
            continue

        highlights = (
            content.get("highlights", {})
            .get("highlights", {})
            .get("items", [])
        )

        count = 0
        for item in highlights:
            if count >= max_highlights_per_game:
                break

            headline = item.get("headline", "")
            playbacks = item.get("playbacks", [])

            # Prefer highest bitrate
            mp4_url = ""
            for pb in sorted(playbacks, key=lambda p: p.get("width", 0), reverse=True):
                url = pb.get("url", "")
                if url.endswith(".mp4"):
                    mp4_url = url
                    break

            if not mp4_url:
                continue

            results.append({
                "title": headline,
                "video_url": mp4_url,
                "url": mp4_url,
                "source_url": mp4_url,
                "teams": f"{away} vs {home}",
                "game_pk": game_pk,
                "source": "mlb_statsapi",
                "_trending_video": True,
                "_clip_url": mp4_url,
            })
            count += 1

    logger.info("[MLB] Found %d highlights across %d games", len(results), len(games))
    return results
