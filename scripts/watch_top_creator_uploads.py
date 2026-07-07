#!/usr/bin/env python3
"""Poll top-tier creators' recent uploads (A.2, 2026-07-07).

Iterates the per-niche watchlist from ``config/top_creators.yaml``
and fetches each creator's ~5 most recent uploads via YouTube Data
API. Writes a per-niche JSON artifact for downstream consumers
(A.3 will feed this into TrendAnticipation as a new ``creator_upload_lead``
signal).

## Cost model

* ``channels.list`` — 1 unit per creator, cached to disk for 24h
  (uploads playlist ID rarely changes)
* ``playlistItems.list`` — 1 unit per creator per poll
* Steady state at 10 creators × 4 polls/day = 40 units/day out of a
  10K daily quota. Room to raise cadence if operator wants.

## Cache strategy

The uploads playlist ID for a channel doesn't change unless the
channel is deleted / merged. Caching it long-term saves the
``channels.list`` cost on every poll. Cache at ``$GENLAB_TMP/
top-creator-uploads/uploads-playlist-cache.json`` — a single flat
map.

## Artifact shape

Per-niche JSON written at ``$GENLAB_TMP/top-creator-uploads/
YYYYMMDD-<niche>.json``:

    {
      "generated_at": "2026-07-07T16:30:00+00:00",
      "niche_id": "gaming",
      "creators": [
        {
          "channel_id": "UC...",
          "label": "MKBHD",
          "uploads": [
            {
              "video_id": "...",
              "title": "...",
              "published_at": "2026-07-07T14:23:00Z",
              "hours_since_publish": 2.1
            },
            ...
          ]
        },
        ...
      ]
    }

## Guardrails

* ``GENLAB_TOP_CREATORS_ENABLED`` env flag — off by default. Runner
  is a no-op unless flipped, matching Intervention 5's discipline.
* Missing ``YOUTUBE_API_KEY`` → early return with WARN log.
* Empty watchlist for a niche → skip niche silently.
* Any single creator's fetch failure → log DEBUG + continue with
  the rest.

## Exit codes

    0 — success (or no-op when flag off)
    1 — YOUTUBE_API_KEY missing / config missing / no creators
    2 — API-side failure (all creators failed)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("watch_top_creator_uploads")

# Number of recent uploads to fetch per creator. 5 balances signal
# (catches multiple-per-day channels) against noise (a creator's
# back catalog isn't a leading indicator).
_UPLOADS_PER_CREATOR = 5

# Cache TTL for the channel_id → uploads_playlist_id lookup. Playlists
# don't change often but a 30-day TTL means merged/rebranded channels
# get picked up eventually.
_UPLOADS_CACHE_TTL_DAYS = 30


def _flag_enabled() -> bool:
    """Exact-match ``true`` per the intelligence-package convention."""
    return os.environ.get("GENLAB_TOP_CREATORS_ENABLED", "").strip() == "true"


def _artifact_dir() -> Path:
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "top-creator-uploads"


def _uploads_playlist_id(
    channel_id: str,
    api_key: str,
    cache: dict[str, dict[str, str]],
) -> str | None:
    """Resolve a channel's uploads playlist ID. Uses on-disk cache
    with 30-day TTL — reduces API cost from 1 unit/creator/poll to
    ~1 unit/creator/month.

    Cache dict shape:
        {channel_id: {"playlist_id": ..., "cached_at": ISO8601}}

    Returns None on any error — caller skips this creator this run.
    """
    from datetime import timedelta

    entry = cache.get(channel_id)
    if entry:
        try:
            cached_at = datetime.fromisoformat(entry["cached_at"])
            if datetime.now(UTC) - cached_at < timedelta(days=_UPLOADS_CACHE_TTL_DAYS):
                return entry["playlist_id"]
        except (KeyError, ValueError):
            pass  # cache entry malformed, refetch below

    try:
        import requests

        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={
                "part": "contentDetails",
                "id": channel_id,
                "key": api_key,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(
                "[top-creators] channels.list %s HTTP %d",
                channel_id,
                resp.status_code,
            )
            return None
        items = resp.json().get("items") or []
        if not items:
            logger.debug(
                "[top-creators] channels.list %s empty — deleted or invalid",
                channel_id,
            )
            return None
        playlist_id = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not playlist_id:
            return None
        # Update cache in place — caller writes to disk at end of run
        cache[channel_id] = {
            "playlist_id": playlist_id,
            "cached_at": datetime.now(UTC).isoformat(),
        }
        return playlist_id
    except Exception as exc:  # noqa: BLE001
        logger.debug("[top-creators] channels.list %s exception: %s", channel_id, exc)
        return None


def _recent_uploads(
    playlist_id: str,
    api_key: str,
    count: int,
) -> list[dict[str, Any]]:
    """Fetch the ``count`` most recent uploads from a playlist.

    Returns empty list on any error. Each entry:
        {video_id, title, published_at, hours_since_publish}
    """
    try:
        import requests

        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": count,
                "key": api_key,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(
                "[top-creators] playlistItems.list %s HTTP %d",
                playlist_id,
                resp.status_code,
            )
            return []
        items = resp.json().get("items") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[top-creators] playlistItems.list %s exception: %s",
            playlist_id,
            exc,
        )
        return []

    now = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for item in items:
        snippet = item.get("snippet") or {}
        video_id = (snippet.get("resourceId") or {}).get("videoId") or ""
        title = snippet.get("title") or ""
        published_at = snippet.get("publishedAt") or ""
        if not video_id or not published_at:
            continue
        try:
            # Normalize Z suffix — YouTube uses ISO8601 with 'Z' for UTC.
            pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            hours_since = (now - pub_dt).total_seconds() / 3600.0
        except ValueError:
            hours_since = 0.0
        out.append(
            {
                "video_id": video_id,
                "title": title,
                "published_at": published_at,
                "hours_since_publish": round(hours_since, 2),
            }
        )
    return out


def _load_uploads_cache(dir_: Path) -> dict[str, dict[str, str]]:
    """Load the channel_id → uploads_playlist_id cache."""
    p = dir_ / "uploads-playlist-cache.json"
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[top-creators] cache read failed: %s", exc)
        return {}


def _save_uploads_cache(dir_: Path, cache: dict[str, dict[str, str]]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / "uploads-playlist-cache.json"
    try:
        p.write_text(json.dumps(cache, indent=2))
    except OSError as exc:
        logger.warning("[top-creators] cache write failed: %s", exc)


def _write_artifact(
    dir_: Path,
    niche_id: str,
    creators_out: list[dict[str, Any]],
) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = dir_ / f"{stamp}-{niche_id}.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "niche_id": niche_id,
        "creators": creators_out,
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def run(
    api_key: str,
    watchlist,
    dry_run: bool = False,
) -> dict[str, int]:
    """Fetch uploads across the watchlist and write per-niche artifacts.

    Returns stats dict for the CLI summary. Split from ``main()`` so
    tests can drive the poll flow without touching sys.exit.
    """
    dir_ = _artifact_dir()
    cache = _load_uploads_cache(dir_)
    stats = {
        "niches_processed": 0,
        "creators_polled": 0,
        "uploads_fetched": 0,
        "creators_failed": 0,
    }

    for niche_id, creators in watchlist.items():
        if not creators:
            continue
        creators_out: list[dict[str, Any]] = []
        for creator in creators:
            playlist_id = _uploads_playlist_id(creator.channel_id, api_key, cache)
            if playlist_id is None:
                stats["creators_failed"] += 1
                continue
            uploads = _recent_uploads(playlist_id, api_key, _UPLOADS_PER_CREATOR)
            creators_out.append(
                {
                    "channel_id": creator.channel_id,
                    "label": creator.label,
                    "uploads": uploads,
                }
            )
            stats["creators_polled"] += 1
            stats["uploads_fetched"] += len(uploads)

        if creators_out:
            if not dry_run:
                path = _write_artifact(dir_, niche_id, creators_out)
                logger.info(
                    "[top-creators] wrote %s (%d creators, %d uploads)",
                    path.name,
                    len(creators_out),
                    sum(len(c["uploads"]) for c in creators_out),
                )
            else:
                logger.info(
                    "[DRY-RUN] would write %s (%d creators, %d uploads)",
                    f"{niche_id}.json",
                    len(creators_out),
                    sum(len(c["uploads"]) for c in creators_out),
                )
            stats["niches_processed"] += 1

    if not dry_run:
        _save_uploads_cache(dir_, cache)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + count but don't write artifacts.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not _flag_enabled():
        logger.info("GENLAB_TOP_CREATORS_ENABLED not set — no-op. Flip flag to activate.")
        return 0

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        logger.error("YOUTUBE_API_KEY not set")
        return 1

    from genlab_core.intel.top_creators_config import (
        load_top_creators,
        total_creator_count,
    )

    watchlist = load_top_creators()
    total = total_creator_count(watchlist)
    if total == 0:
        logger.warning("Watchlist is empty — nothing to poll")
        return 1

    logger.info("Polling %d creators across %d niches", total, len(watchlist))
    stats = run(api_key, watchlist, dry_run=args.dry_run)

    logger.info(
        "Summary: %d niches processed, %d creators polled, %d uploads fetched, %d creators failed",
        stats["niches_processed"],
        stats["creators_polled"],
        stats["uploads_fetched"],
        stats["creators_failed"],
    )

    # Exit 2 when EVERY creator failed — signals genuine problem
    # (API quota exhausted, key revoked, network fully down).
    if stats["creators_polled"] == 0 and stats["creators_failed"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
