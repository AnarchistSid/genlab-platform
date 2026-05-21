"""Hugging Face Spaces connector for creator demo discovery."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

LIST_URL = "https://huggingface.co/api/spaces"
DETAIL_URL = "https://huggingface.co/api/spaces/{space_id}"
MEDIA_EXTS = (".mp4", ".webm", ".mov", ".gif", ".png", ".jpg", ".jpeg", ".webp")


def _to_iso(raw: str) -> str:
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).astimezone(timezone.utc).isoformat()
    except Exception:
        return raw


def _match_keywords(space: Dict[str, Any], keywords: List[str]) -> bool:
    if not keywords:
        return True

    blob = " ".join(
        [
            str(space.get("id", "")),
            str(space.get("author", "")),
            " ".join(space.get("tags", []) or []),
        ]
    ).lower()
    return any(k.lower() in blob for k in keywords)


def _detail(space_id: str) -> Dict[str, Any]:
    resp = requests.get(DETAIL_URL.format(space_id=space_id), timeout=20)
    if resp.status_code == 429:
        raise RuntimeError("429 rate_limited from Hugging Face")
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _find_media_url(space_id: str, detail: Dict[str, Any]) -> Optional[str]:
    siblings = detail.get("siblings", []) or []
    for sibling in siblings:
        name = str(sibling.get("rfilename", ""))
        lower = name.lower()
        if lower.endswith(MEDIA_EXTS):
            return f"https://huggingface.co/spaces/{space_id}/resolve/main/{name}"

    card = detail.get("cardData", {}) or {}
    demo = card.get("demo", "")
    if isinstance(demo, str) and demo.startswith("http"):
        return demo

    return None


def fetch_source(src: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch creator demos from Hugging Face Spaces listing."""
    params = src.get("params", {}) or {}
    max_results = max(1, min(int(params.get("max_results", 40)), 120))
    limit = max(1, min(int(params.get("list_limit", 120)), 500))
    keywords = [str(k).strip() for k in (params.get("keywords", []) or []) if str(k).strip()]
    authors = {str(a).strip().lower() for a in (params.get("authors", []) or []) if str(a).strip()}
    require_media = bool(params.get("require_media", True))

    try:
        resp = requests.get(LIST_URL, params={"limit": limit}, timeout=25)
        if resp.status_code == 429:
            raise RuntimeError("429 rate_limited from Hugging Face")
        resp.raise_for_status()
        raw_list = resp.json() if resp.content else []
    except Exception as exc:
        message = str(exc)
        status = "rate_limited" if "429" in message else "failed"
        return {
            "url": src.get("url", ""),
            "entry_count": 0,
            "entries": [],
            "fetch_status": status,
            "fetch_method": "connector:hf_spaces",
            "error": message,
        }

    spaces: List[Dict[str, Any]] = raw_list if isinstance(raw_list, list) else []
    filtered: List[Dict[str, Any]] = []
    for sp in spaces:
        sid = str(sp.get("id", "")).strip()
        if not sid:
            continue
        if authors and str(sp.get("author", "")).lower() not in authors:
            continue
        if not _match_keywords(sp, keywords):
            continue
        filtered.append(sp)
        if len(filtered) >= max_results * 3:
            break

    entries: List[Dict[str, Any]] = []
    seen_links = set()
    for sp in filtered:
        sid = str(sp.get("id", "")).strip()
        if not sid:
            continue

        try:
            det = _detail(sid)
        except Exception:
            det = sp

        link = f"https://huggingface.co/spaces/{sid}"
        media_url = _find_media_url(sid, det)
        host = det.get("host") or ""
        author = det.get("author") or sid.split("/", 1)[0]

        if require_media and not media_url:
            continue

        summary_lines = []
        short_desc = ((det.get("cardData") or {}).get("short_description") or "").strip()
        if short_desc:
            summary_lines.append(short_desc[:350])
        if media_url:
            summary_lines.append(f"Media: {media_url}")
        if host:
            summary_lines.append(f"Demo: {host}")
        summary_lines.append(f"Space: {link}")

        if link in seen_links:
            continue
        seen_links.add(link)

        entries.append(
            {
                "title": f"HF Space Demo: {sid}",
                "link": link,
                "summary": "\n".join(summary_lines),
                "published": _to_iso(det.get("lastModified", "") or sp.get("createdAt", "")),
                "author": author,
            }
        )

        if len(entries) >= max_results:
            break

    return {
        "url": src.get("url", ""),
        "entry_count": len(entries),
        "entries": entries,
        "fetch_status": "success",
        "fetch_method": "connector:hf_spaces",
    }
