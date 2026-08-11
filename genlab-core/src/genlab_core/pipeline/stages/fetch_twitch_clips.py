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
from typing import Any, Final

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


# Title-keyword markers for non-gameplay Twitch clips. A clip captured
# while a streamer is in a "real" game category can still be an IRL
# moment (reacting to chat, talking about life, eating, etc.) — the
# game category check upstream doesn't filter these out because the
# streamer technically WAS in that game's category at clip-time.
#
# These markers are conservative — we drop a clip when its title (which
# the streamer or their chat names) clearly indicates non-gameplay
# content. False-negatives are fine (we'll see them in another run);
# false-positives (dropping a real gameplay clip) are also acceptable
# because we have other sources (YouTube trending, Reddit, Steam).
#
# Caught in prod 2026-06-20 from a streamer "zullysk" Overwatch IRL-tic
# clip that the LLM then hallucinated full Overwatch patch-notes content
# around. Operator screenshot in the PR body.
#
# All markers are space-padded so they don't fragment-match inside
# game/team/word names ("tic" inside "tactical", "irl" inside "irlbgames").
_NON_GAMEPLAY_TITLE_MARKERS = (
    # Every marker MUST be bracketed by spaces (or punctuation that
    # the padded-with-spaces matcher treats as a boundary) so
    # ``irlbgames`` doesn't false-positive on `` irl``. Verified
    # by ``test_substring_false_positive_guard``.
    " irl ",
    " tic ",
    " tics ",
    " tic moment ",
    " rant ",
    " rants ",
    " react ",
    " reaction ",
    " reacts ",
    " talking ",
    " talks about ",
    " chat ",
    " just chat ",
    " story time ",
    " storytime ",
    " explains why ",
    " opens up ",
    " breaks down crying ",
    " cries on stream ",
    " asmr ",
    " ramble ",
    " rambles ",
    " yelling at chat ",
    " rant about ",
    " chatting ",
    " fan questions ",
    " q&a ",
    " qna ",
    " ask me anything ",
    " ama ",
)


def _is_non_gameplay_clip(title: str) -> bool:
    """Return True when ``title`` contains a non-gameplay marker.

    Conservative title-text heuristic for filtering Twitch clips that
    were captured during non-gameplay moments (IRL reactions, talking,
    chat, ASMR, etc.) even though the clip is attached to a real game
    category. Caller drops the clip when this returns True.

    Lowercase + bracketed by spaces so we don't accidentally match
    substring fragments inside game/team/word names.
    """
    if not title:
        return False
    padded = " " + title.lower().strip() + " "
    return any(marker in padded for marker in _NON_GAMEPLAY_TITLE_MARKERS)


# Platform min-duration floor. Twitch clips shorter than this cannot
# survive ``validate_videos.SPEC.min_duration=15.0``; the render output
# comes in at the source-clip length (compositor doesn't pad), so
# rejecting at ingestion is the only place that avoids wasted work
# + a stuck DRAFTED blueprint. See 2026-07-15 investigation of
# Sheepy (5.0s) + Granny (5.072s) blueprints.
_MIN_CLIP_DURATION_SECONDS = 15.0


def _filter_clips_by_min_duration(
    clips: list[dict],
    min_duration_seconds: float = _MIN_CLIP_DURATION_SECONDS,
) -> tuple[list[dict], list[float]]:
    """Split clips into (kept, dropped-durations) by min-duration floor.

    Clips missing a ``duration`` key are treated as 0 → dropped. This
    matches the failure mode we're trying to prevent: an unknown-duration
    clip is more likely a scraper edge-case than a legitimately-short
    clip we want to preserve.

    Returns:
        (kept, dropped_durations): kept is the surviving list preserving
        input order; dropped_durations is the list of durations we
        discarded (for logging).
    """
    kept: list[dict] = []
    dropped: list[float] = []
    for c in clips:
        dur = float(c.get("duration", 0) or 0)
        if dur >= min_duration_seconds:
            kept.append(c)
        else:
            dropped.append(dur)
    return kept, dropped


# 2026-08-12: Writer requires >=40 chars of writable context in
# `summary` OR the story gets silently dropped as
# `excluded_incomplete_content` at QC (see run_report metric.qc).
# Previously this fetcher emitted "Twitch clip by <broadcaster>"
# (~15-25 chars) and every Twitch clip failed the floor -> gaming
# pipeline blueprints_count=0. Synthesizing from title + broadcaster
# + view_count + duration always clears the floor even for very terse
# clip titles like "gg". See sibling _build_steam_summary in
# fetch_steam_trailers.py; class-of-bug-fetcher-schema-drift memo.
_WRITER_MIN_CONTEXT_CHARS: Final[int] = 40


def _build_twitch_summary(
    *,
    title: str,
    broadcaster: str,
    view_count: int,
    duration: float,
) -> str:
    """Construct a writer-usable summary >=40 chars from Twitch clip
    metadata. Even a bare "gg" title + "Kai_Cenat" broadcaster
    + view/duration facts yields ~55 chars — comfortably above the
    writer's thin-context floor."""
    return (
        f"{title}. Twitch highlight from {broadcaster}"
        f" — {int(view_count):,} views in {float(duration):.0f}s"
    ).strip()


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
                    # 2026-08-11 Phase 2: preserve API identifiers so the
                    # emitting stage can populate video_id + channel_id
                    # to satisfy Option C's video-invariant contract. Twitch
                    # clip ``id`` (e.g. "AwkwardHelplessSalamander...") is
                    # the canonical stable clip identifier; ``broadcaster_id``
                    # is the streamer's channel ID for attribution.
                    "id": clip.get("id", ""),
                    "broadcaster_id": clip.get("broadcaster_id", ""),
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
        # 2026-07-15: reject clips shorter than the platform min duration
        # (validate_videos SPEC.min_duration = 15s). Without this filter,
        # short Twitch clips (e.g. Sheepy 5.0s, Granny 5.072s observed
        # in prod) flow all the way through the pipeline, fail at
        # validate_videos with `too_short:5.0s`, and leave DRAFTED
        # blueprints stuck forever — the health-monitor stale_drafted
        # alert has been ticking on 2 such blueprints for 1-6 days.
        # Reject at ingestion so the wasted work never happens.
        min_duration = float(twitch_cfg.get("min_clip_duration_seconds", 15.0))

        # Get Twitch app token
        token = _get_twitch_app_token(client_id, client_secret)
        if not token:
            return context

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id,
        }

        # Use IGDB-enriched game IDs from context if available, else defaults.
        # max_games is config-driven (sources.yaml `twitch.max_games`); default
        # stays 5 for backward compat. 2026-06-28: raised gaming's value to 8
        # after audit surfaced this as a hidden cap that compounded with the
        # filter-stage cap (PR #621) to starve gaming's daily blueprints.
        # Rate-limit cost is negligible: 0.1s × max_games sleep = ~0.8s.
        max_games = twitch_cfg.get("max_games", 5)
        game_ids = context.get("trending_game_ids", _DEFAULT_GAME_IDS[:max_games])

        all_clips: list[dict] = []
        for game_id in game_ids[:max_games]:
            clips = _fetch_clips_for_game(game_id, headers, max_clips, lookback_days)
            all_clips.extend(clips)
            time.sleep(0.1)  # Be nice to Twitch API

        # Filter by min duration — validate_videos SPEC.min_duration=15.0
        # is the platform floor; anything shorter would be rejected at
        # render time and leave a stuck DRAFTED blueprint behind.
        before_dur = len(all_clips)
        all_clips, dropped_durations = _filter_clips_by_min_duration(
            all_clips, min_duration_seconds=min_duration
        )
        if dropped_durations:
            logger.info(
                "[TwitchClips] Dropped %d/%d clips shorter than %.1fs (platform min_duration): %s",
                len(dropped_durations),
                before_dur,
                min_duration,
                sorted(dropped_durations)[:5],
            )

        # Filter by min views and sort
        all_clips = [c for c in all_clips if c.get("view_count", 0) >= min_views]
        all_clips.sort(key=lambda x: x.get("view_count", 0), reverse=True)
        logger.info("[TwitchClips] %d clips passed view filter (>=%d)", len(all_clips), min_views)

        # Drop clips whose title indicates the captured moment is
        # non-gameplay (IRL talking, reacts, ASMR, rants, etc.) even
        # though the streamer was technically in a real game category.
        # Without this, the LLM downstream hallucinated full gameplay-
        # themed content around clips of streamers just talking in
        # their rooms. See `_is_non_gameplay_clip` and the prod
        # screenshot in the PR body. Added 2026-06-20.
        before_filter = len(all_clips)
        non_gameplay_titles: list[str] = []
        kept: list[dict] = []
        for clip in all_clips:
            if _is_non_gameplay_clip(clip.get("title", "")):
                non_gameplay_titles.append(clip.get("title", "")[:80])
                continue
            kept.append(clip)
        all_clips = kept
        if non_gameplay_titles:
            logger.info(
                "[TwitchClips] Dropped %d/%d non-gameplay clips by title heuristic: %s",
                len(non_gameplay_titles),
                before_filter,
                non_gameplay_titles[:5],
            )

        if all_clips:
            from genlab_core.cache.stable_ids import generate_story_id

            now_iso = datetime.now(UTC).isoformat()
            new_stories = []
            for clip in all_clips[:10]:  # Cap at 10 clips
                clip_url = clip["clip_url"]
                sid = generate_story_id(clip_url, now_iso)
                broadcaster = clip.get("broadcaster", "")
                clip_title = clip["title"] or ""
                summary = _build_twitch_summary(
                    title=clip_title,
                    broadcaster=broadcaster,
                    view_count=clip.get("view_count", 0),
                    duration=clip.get("duration", 0),
                )
                new_stories.append(
                    {
                        "story_id": sid,
                        "title": clip_title,
                        "source": "twitch_clips",
                        "source_url": clip.get("url", clip_url),
                        "canonical_url": clip_url,
                        "published_at": clip.get("created_at", now_iso),
                        "fetched_at": now_iso,
                        "summary": summary,
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
                        # 2026-08-11 Phase 2: satisfy Option C video-invariant.
                        # Twitch clip id (from Helix API) is the canonical
                        # stable video identifier; broadcaster_id is the
                        # streamer's channel id for the L1-L6 attribution
                        # defense stack. Before this line, twitch_clips
                        # stories were silently dropped by merge_stories'
                        # contract check because video_id was empty +
                        # no bypass was declared.
                        "video_id": clip.get("id", ""),
                        "channel_id": clip.get("broadcaster_id", ""),
                        "channel_name": broadcaster,
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
