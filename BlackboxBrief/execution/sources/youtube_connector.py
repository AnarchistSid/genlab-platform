"""YouTube connector for creator-video discovery."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional


def _build_service():
    """Build YouTube API service via OAuth refresh token credentials."""
    client_id = (os.getenv("YOUTUBE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("YOUTUBE_CLIENT_SECRET") or "").strip()
    refresh_token = (os.getenv("YOUTUBE_REFRESH_TOKEN") or "").strip()

    if not client_id or not client_secret or not refresh_token:
        raise ValueError(
            "Missing YouTube OAuth credentials (YOUTUBE_CLIENT_ID, "
            "YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN)"
        )

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("google-api-python-client is not installed") from exc

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def _chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _parse_iso_duration_seconds(raw: str) -> int:
    """Parse an ISO-8601 duration (PT#H#M#S) into integer seconds."""
    if not raw:
        return 0
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", raw)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    mins = int(match.group(2) or 0)
    secs = int(match.group(3) or 0)
    return hours * 3600 + mins * 60 + secs


def _resolve_channel_id(service: Any, seed: str) -> Optional[str]:
    """Resolve a channel ID from a raw seed (id, handle, or text)."""
    clean = (seed or "").strip()
    if not clean:
        return None

    if clean.startswith("UC") and len(clean) >= 20:
        return clean

    handle = clean[1:] if clean.startswith("@") else clean

    # forHandle is the most stable path for modern channel handles.
    try:
        resp = service.channels().list(part="id", forHandle=handle, maxResults=1).execute()
        items = resp.get("items", [])
        if items:
            return items[0].get("id")
    except Exception:
        pass

    # Fallback: search channel by query seed.
    try:
        resp = service.search().list(
            part="snippet",
            q=clean,
            type="channel",
            maxResults=1,
        ).execute()
        items = resp.get("items", [])
        if items:
            return items[0].get("snippet", {}).get("channelId")
    except Exception:
        pass

    return None


def _build_entry(snippet: Dict[str, Any], video_meta: Dict[str, Any]) -> Dict[str, Any]:
    vid = video_meta.get("id", "")
    title = snippet.get("title", "").strip()
    description = (snippet.get("description", "") or "").strip()
    channel = snippet.get("channelTitle", "") or "Unknown Creator"
    published = snippet.get("publishedAt", "") or datetime.now(timezone.utc).isoformat()

    watch_url = f"https://www.youtube.com/watch?v={vid}"
    thumb_max = f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg"
    thumb_hq = f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"

    summary_lines = [description[:350]] if description else []
    summary_lines.extend([
        f"Watch: {watch_url}",
        f"Thumbnail: {thumb_max}",
        f"ThumbnailBackup: {thumb_hq}",
    ])

    return {
        "title": title or f"YouTube Creator Video ({vid})",
        "link": watch_url,
        "summary": "\n".join(summary_lines),
        "published": published,
        "author": channel,
    }


def fetch_source(src: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch creator videos from YouTube (keywords + channel allowlists)."""
    params = src.get("params", {}) or {}
    max_results = max(1, min(int(params.get("max_results", 60)), 200))
    per_seed = max(1, min(int(params.get("per_seed", 20)), 50))
    days = max(1, int(params.get("published_after_days", 60)))
    require_shorts = bool(params.get("require_shorts", True))
    video_duration = str(params.get("video_duration", "short" if require_shorts else "any"))
    mode = str(params.get("mode", "keyword_discovery")).strip().lower()

    query_seeds = [q for q in (params.get("queries", []) or []) if str(q).strip()]
    channel_seeds = [c for c in (params.get("channels", []) or []) if str(c).strip()]

    if mode == "keyword_discovery" and not query_seeds:
        query_seeds = [
            "AI generated video timelapse",
            "made with AI short",
            "midjourney animation",
            "kling ai video",
            "sora ai demo",
        ]

    service = _build_service()
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    video_snippets: Dict[str, Dict[str, Any]] = {}

    def _add_search_items(items: List[Dict[str, Any]]) -> None:
        for item in items:
            vid = (item.get("id") or {}).get("videoId")
            if not vid:
                continue
            video_snippets.setdefault(vid, item.get("snippet", {}) or {})

    # Keyword discovery
    if mode in {"keyword_discovery", "mixed"}:
        for query in query_seeds:
            resp = service.search().list(
                part="snippet",
                q=query,
                type="video",
                order="date",
                publishedAfter=after,
                maxResults=per_seed,
                videoDuration=video_duration,
                relevanceLanguage="en",
                safeSearch="moderate",
            ).execute()
            _add_search_items(resp.get("items", []))
            if len(video_snippets) >= max_results:
                break

    # Channel allowlist discovery
    if mode in {"channel_allowlist", "mixed"} and channel_seeds:
        for seed in channel_seeds:
            channel_id = _resolve_channel_id(service, str(seed))
            if not channel_id:
                continue

            resp = service.search().list(
                part="snippet",
                channelId=channel_id,
                type="video",
                order="date",
                publishedAfter=after,
                maxResults=per_seed,
                videoDuration=video_duration,
                relevanceLanguage="en",
                safeSearch="moderate",
            ).execute()
            _add_search_items(resp.get("items", []))
            if len(video_snippets) >= max_results:
                break

    video_ids = list(video_snippets.keys())[:max_results]
    if not video_ids:
        return {
            "url": src.get("url", ""),
            "entry_count": 0,
            "entries": [],
            "fetch_status": "success",
            "fetch_method": "connector:youtube",
        }

    details: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(video_ids, 50):
        resp = service.videos().list(
            part="snippet,contentDetails,statistics",
            id=",".join(chunk),
        ).execute()
        for item in resp.get("items", []):
            details[item.get("id", "")] = item

    entries: List[Dict[str, Any]] = []
    for vid in video_ids:
        item = details.get(vid, {})
        snippet = item.get("snippet") or video_snippets.get(vid, {})
        duration_raw = (item.get("contentDetails") or {}).get("duration", "")
        duration_sec = _parse_iso_duration_seconds(duration_raw)

        if require_shorts and duration_sec and duration_sec > 60:
            continue

        entries.append(_build_entry(snippet, {"id": vid}))

    return {
        "url": src.get("url", ""),
        "entry_count": len(entries),
        "entries": entries,
        "fetch_status": "success",
        "fetch_method": "connector:youtube",
    }
