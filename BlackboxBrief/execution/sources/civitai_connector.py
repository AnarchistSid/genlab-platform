"""Civitai connector for creator AI art/video outputs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

API_URL = "https://civitai.com/api/v1/images"


def _to_iso(raw: str) -> str:
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).astimezone(timezone.utc).isoformat()
    except Exception:
        return raw


def _is_media_url(url: str) -> bool:
    if not url:
        return False
    lower = url.lower()
    return bool(re.search(r"\.(?:mp4|webm|mov|gif|jpg|jpeg|png|webp)(?:$|\?)", lower) or "image.civitai.com" in lower)


def _fetch_items(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    resp = requests.get(API_URL, params=params, timeout=30)
    if resp.status_code == 429:
        raise RuntimeError("429 rate_limited from Civitai")
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("items", []) if isinstance(data, dict) else []


def _entry_from_item(item: Dict[str, Any], query_label: str) -> Dict[str, Any]:
    image_id = item.get("id")
    post_id = item.get("postId")
    media_url = item.get("url", "")

    link = ""
    if image_id:
        link = f"https://civitai.com/images/{image_id}"
    elif post_id:
        link = f"https://civitai.com/posts/{post_id}"
    else:
        link = media_url or "https://civitai.com"

    username = item.get("username") or (item.get("user") or {}).get("username") or "creator"
    post_title = item.get("postTitle") or item.get("title") or "Civitai creator output"
    prompt = (item.get("meta") or {}).get("prompt", "")

    summary_bits = []
    if prompt:
        summary_bits.append(prompt[:350])
    if media_url:
        summary_bits.append(f"Media: {media_url}")
    summary_bits.append(f"Source: {link}")
    if query_label:
        summary_bits.append(f"Tag: {query_label}")

    return {
        "title": f"{post_title} ({username})",
        "link": link,
        "summary": "\n".join(summary_bits),
        "published": _to_iso(item.get("createdAt", "")),
        "author": username,
    }


def fetch_source(src: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch media-rich creator outputs from Civitai image feed."""
    params = src.get("params", {}) or {}
    mode = str(params.get("mode", "tags")).strip().lower()

    tags = [str(t).strip() for t in (params.get("tags", []) or []) if str(t).strip()]
    creators = [str(c).strip() for c in (params.get("creators", []) or []) if str(c).strip()]

    limit = max(1, min(int(params.get("limit", 40)), 200))
    period = str(params.get("period", "Month"))
    sort = str(params.get("sort", "Most Reactions"))
    nsfw = str(params.get("nsfw", "None"))

    if mode == "tags" and not tags:
        tags = ["ai video", "midjourney", "kling", "runway", "flux"]

    seeds: List[tuple[str, Dict[str, Any]]] = []
    if mode in {"tags", "mixed"}:
        for tag in tags:
            seeds.append((tag, {"query": tag}))

    if mode in {"creator_feeds", "mixed"}:
        for creator in creators:
            seeds.append((creator, {"username": creator}))

    if not seeds:
        seeds.append(("", {}))

    entries: List[Dict[str, Any]] = []
    seen_links = set()

    try:
        for label, seed_params in seeds:
            req_params = {
                "limit": limit,
                "period": period,
                "sort": sort,
                "nsfw": nsfw,
                **seed_params,
            }
            items = _fetch_items(req_params)
            for item in items:
                media_url = item.get("url", "")
                if not _is_media_url(media_url):
                    continue
                entry = _entry_from_item(item, label)
                link = entry.get("link", "")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                entries.append(entry)
                if len(entries) >= limit:
                    break
            if len(entries) >= limit:
                break
    except Exception as exc:
        message = str(exc)
        status = "rate_limited" if "429" in message else "failed"
        return {
            "url": src.get("url", ""),
            "entry_count": 0,
            "entries": [],
            "fetch_status": status,
            "fetch_method": "connector:civitai",
            "error": message,
        }

    return {
        "url": src.get("url", ""),
        "entry_count": len(entries),
        "entries": entries,
        "fetch_status": "success",
        "fetch_method": "connector:civitai",
    }
