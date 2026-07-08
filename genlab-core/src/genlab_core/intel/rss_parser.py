"""RSS/feed entry parser — normalizes raw fetched entries into structured items.

Migrated from BlackboxBrief/execution/parse_extract.py (Sprint 68).
Used by content research strategies to parse fetch logs from RSS sources.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore

from genlab_core.cache.stable_ids import generate_story_id, normalize_url
from genlab_core.cache.text_sanitizer import check_for_injection, sanitize_text

logger = logging.getLogger(__name__)

_VIDEO_HINTS = re.compile(
    r"(?:v\.redd\.it|reddit_video|youtube\.com|youtu\.be|\.mp4(?:$|\?)|instagram\.com/reel|/reel/)",
    re.IGNORECASE,
)


def strip_html(raw_html: str) -> str:
    """Remove HTML tags, decode entities, and clean Reddit metadata."""
    if not raw_html:
        return ""
    if BeautifulSoup is not None:
        soup = BeautifulSoup(raw_html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
    else:
        text = re.sub(r"<[^>]+>", " ", raw_html)

    # Decode remaining HTML entities (&#32; etc.)
    import html

    text = html.unescape(text)

    # Strip Reddit metadata patterns
    text = re.sub(r"submitted by\s+/u/\S+\s*", "", text)
    text = re.sub(r"\[link\]\s*\[comments\]", "", text)
    text = re.sub(r"\[link\]|\[comments\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def extract_domain(url: str) -> str:
    """Extract domain from a URL."""
    try:
        domain = urlparse(url).netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain
    except Exception:
        return ""


def normalize_published(raw: str) -> str | None:
    """Parse a date string into ISO format, or None."""
    if not raw:
        return None
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw.strip(), fmt).isoformat()
        except ValueError:
            continue
    return None


def _extract_summary_urls(summary_raw: str) -> list[str]:
    """Extract href/src URLs from entry summary HTML."""
    if not summary_raw:
        return []
    urls: list[str] = []
    if BeautifulSoup is not None:
        soup = BeautifulSoup(summary_raw, "html.parser")
        for tag in soup.find_all(["a", "img", "video", "source"]):
            href = (tag.get("href") or tag.get("src") or "").strip()
            if href.startswith(("http://", "https://")):
                urls.append(href)
    else:
        urls = re.findall(r"""(?:href|src)=["'](https?://[^"']+)["']""", summary_raw)
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]


def _is_reddit_permalink(url: str) -> bool:
    """Return True for reddit comments/user/profile links."""
    try:
        host = urlparse(url).netloc.lower()
        path = urlparse(url).path.lower()
    except Exception:
        return False
    if not host.endswith("reddit.com"):
        return False
    return "/comments/" in path or path.startswith(("/user/", "/u/"))


def _select_primary_url(entry_link: str, summary_raw: str) -> dict[str, Any]:
    """Pick best canonical URL."""
    primary = entry_link or ""
    source_post_url = ""
    media_hints = _extract_summary_urls(summary_raw)

    if "reddit.com/" in (entry_link or "") and media_hints:
        video_candidates = [u for u in media_hints if _VIDEO_HINTS.search(u)]
        if video_candidates:
            primary = video_candidates[0]
            source_post_url = entry_link
        else:
            external = [
                u for u in media_hints if not _is_reddit_permalink(u) and "reddit.com/" not in u
            ]
            if external:
                primary = external[0]
                source_post_url = entry_link

    return {"primary_url": primary, "source_post_url": source_post_url, "media_hints": media_hints}


def parse_entry(entry: dict[str, Any], source_priority: float) -> dict[str, Any]:
    """Parse a single raw entry into a normalized item.

    Raises ``ValueError`` when the entry is missing usable link
    metadata — ``parse_fetch_log`` catches this and drops the row.
    Direct callers should be prepared for the same. DEV-3 (2026-07-08)
    observation: prior behaviour was to crash inside ``generate_story_id``
    with ``normalize_url requires a non-empty string`` — the raise is
    now explicit and validates the invariant at the top of the function
    rather than deep in a helper.
    """
    raw_link = entry.get("link", "")
    summary_raw = entry.get("summary", "")
    primary = _select_primary_url(raw_link, summary_raw)
    url = primary.get("primary_url", raw_link)
    if not url:
        # Fail-fast with a clear reason instead of ``normalize_url``'s
        # deeper "requires non-empty string". ``parse_fetch_log`` catches
        # ValueError and logs the row with an ERROR + skips it. A
        # missing link is a producer bug in the fetcher, not something
        # this parser can recover from.
        raise ValueError(
            "parse_entry requires 'link' or an extractable URL from "
            "the summary — got empty. Producer bug in the fetcher."
        )
    canonical_url = normalize_url(url)

    title = sanitize_text(strip_html(entry.get("title", "")), max_length=500)
    summary = sanitize_text(strip_html(summary_raw), max_length=5000)
    published = normalize_published(entry.get("published", ""))

    injection_flags = []
    for field, text in [("title", title), ("summary", summary)]:
        hits = check_for_injection(text)
        if hits:
            logger.warning("Injection patterns in %s of %s: %s", field, url, hits)
            injection_flags.extend(hits)

    story_id = generate_story_id(url, published or "1970-01-01T00:00:00+00:00")

    parsed: dict[str, Any] = {
        "story_id": story_id,
        "url": canonical_url,
        "domain": extract_domain(url),
        "title": title,
        "summary": summary,
        "published_at": published,
        "author": entry.get("author", ""),
        "source_priority": source_priority,
        "injection_flags": injection_flags,
        "parsed_at": datetime.now(UTC).isoformat(),
    }
    if primary.get("source_post_url"):
        parsed["source_post_url"] = normalize_url(primary["source_post_url"])
    if primary.get("media_hints"):
        parsed["media_hint_urls"] = primary["media_hints"][:8]
    if "gallery_metadata" in entry:
        parsed["gallery_metadata"] = entry["gallery_metadata"]

    return parsed


def parse_fetch_log(fetch_log: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse all entries from a fetch log into normalized items."""
    items = []
    for result in fetch_log.get("results", []):
        priority = result.get("source_priority", 0.5)
        for entry in result.get("entries", []):
            try:
                item = parse_entry(entry, priority)
                if item["url"]:
                    items.append(item)
            except Exception as exc:
                logger.error("Failed to parse entry: %s — %s", entry.get("link", "?"), exc)
    return items
