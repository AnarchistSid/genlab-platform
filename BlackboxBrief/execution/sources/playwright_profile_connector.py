"""Playwright connector for logged-in creator profile scraping."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import urlparse

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    _HAS_PLAYWRIGHT = True
except Exception:  # pragma: no cover - import guard
    _HAS_PLAYWRIGHT = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform_from_source(src: Dict[str, Any]) -> str:
    platform = (src.get("platform", "") or "").strip().lower()
    if platform:
        return platform
    host = urlparse(src.get("url", "")).netloc.lower()
    if "tiktok" in host:
        return "tiktok"
    if "threads.net" in host:
        return "threads"
    if "instagram" in host:
        return "instagram"
    return "generic"


def _extract_items(page: Any, platform: str, max_items: int) -> List[Dict[str, Any]]:
    """Extract timeline entries from known platform page structures."""
    if platform == "tiktok":
        script = r"""
(maxItems) => {
  const out = [];
  const seen = new Set();
  const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
  for (const a of anchors) {
    if (out.length >= maxItems) break;
    const href = a.href || a.getAttribute('href') || '';
    if (!href || seen.has(href)) continue;
    seen.add(href);
    const card = a.closest('article, div') || document;
    const media = Array.from(card.querySelectorAll('img[src],video[src],source[src]'))
      .map(el => el.getAttribute('src') || '')
      .filter(Boolean)
      .slice(0, 4);
    const title = (a.getAttribute('title') || a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 140);
    const t = card.querySelector('time');
    out.push({
      link: href,
      title: title || 'TikTok creator post',
      text: (card.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 450),
      media,
      published: t ? (t.getAttribute('datetime') || t.textContent || '') : ''
    });
  }
  return out;
}
"""
        return page.evaluate(script, max_items)

    if platform == "threads":
        script = r"""
(maxItems) => {
  const out = [];
  const seen = new Set();
  const anchors = Array.from(document.querySelectorAll('a[href*="/post/"]'));
  for (const a of anchors) {
    if (out.length >= maxItems) break;
    const href = a.href || a.getAttribute('href') || '';
    if (!href || seen.has(href)) continue;
    seen.add(href);
    const article = a.closest('article, div') || document;
    const media = Array.from(article.querySelectorAll('img[src],video[src],source[src]'))
      .map(el => el.getAttribute('src') || '')
      .filter(Boolean)
      .slice(0, 4);
    const timeEl = article.querySelector('time');
    out.push({
      link: href,
      title: (article.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 140) || 'Threads creator post',
      text: (article.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 450),
      media,
      published: timeEl ? (timeEl.getAttribute('datetime') || timeEl.textContent || '') : ''
    });
  }
  return out;
}
"""
        return page.evaluate(script, max_items)

    if platform == "instagram":
        script = r"""
(maxItems) => {
  const out = [];
  const seen = new Set();
  const anchors = Array.from(document.querySelectorAll('a[href*="/reel/"]'));
  for (const a of anchors) {
    if (out.length >= maxItems) break;
    const href = a.href || a.getAttribute('href') || '';
    if (!href || seen.has(href)) continue;
    seen.add(href);
    const card = a.closest('article, div') || document;
    const t = card.querySelector('time');
    const title = (a.getAttribute('aria-label') || a.getAttribute('title') || '').trim();
    out.push({
      link: href,
      title: title || 'Instagram Reel',
      text: (card.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 320),
      media: [href],
      published: t ? (t.getAttribute('datetime') || t.textContent || '') : ''
    });
  }
  return out;
}
"""
        return page.evaluate(script, max_items)

    # Generic fallback
    script = r"""
(maxItems) => {
  const out = [];
  const seen = new Set();
  const anchors = Array.from(document.querySelectorAll('a[href]'));
  for (const a of anchors) {
    if (out.length >= maxItems) break;
    const href = a.href || a.getAttribute('href') || '';
    if (!href || seen.has(href)) continue;
    if (!/(video|reel|short|post|status)/i.test(href)) continue;
    seen.add(href);
    out.push({
      link: href,
      title: (a.textContent || 'Creator post').trim().slice(0, 140),
      text: (a.textContent || '').trim().slice(0, 260),
      media: [href],
      published: ''
    });
  }
  return out;
}
"""
    return page.evaluate(script, max_items)


def _entry_from_item(item: Dict[str, Any], platform: str) -> Dict[str, Any]:
    link = (item.get("link", "") or "").strip()
    media_urls = [str(u).strip() for u in (item.get("media", []) or []) if str(u).strip()]
    summary_lines = []
    text = (item.get("text", "") or "").strip()
    if text:
        summary_lines.append(text[:450])
    summary_lines.extend([f"Media: {u}" for u in media_urls[:4]])
    summary_lines.append(f"Post: {link}")

    published = (item.get("published", "") or "").strip() or _now_iso()

    return {
        "title": (item.get("title", "") or f"{platform.title()} creator post").strip(),
        "link": link,
        "summary": "\n".join(summary_lines),
        "published": published,
        "author": platform,
    }


def fetch_source(src: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch creator posts from logged-in social profile pages."""
    if not _HAS_PLAYWRIGHT:
        return {
            "url": src.get("url", ""),
            "entry_count": 0,
            "entries": [],
            "fetch_status": "failed",
            "fetch_method": "connector:playwright_profile",
            "error": "Playwright not installed",
        }

    params = src.get("params", {}) or {}
    max_items = max(1, min(int(params.get("max_items", 40)), 120))
    wait_ms = max(0, int(params.get("wait_ms", 12000)))
    scroll_rounds = max(0, int(params.get("scroll_rounds", 4)))
    requires_login = bool(src.get("requires_login", False) or src.get("requires_auth", False))

    platform = _platform_from_source(src)
    target_url = src.get("url", "")

    profile_dir = (os.getenv("PLAYWRIGHT_PROFILE_DIR") or "").strip()
    if requires_login and not profile_dir:
        return {
            "url": target_url,
            "entry_count": 0,
            "entries": [],
            "fetch_status": "failed",
            "fetch_method": "connector:playwright_profile",
            "error": "PLAYWRIGHT_PROFILE_DIR is required for logged-in sources",
        }

    headless = os.getenv("PLAYWRIGHT_HEADLESS", "1").strip() not in {"0", "false", "False"}

    entries: List[Dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            context = None
            browser = None

            if requires_login:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=profile_dir,
                    headless=headless,
                    viewport={"width": 1280, "height": 2000},
                )
            else:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(viewport={"width": 1280, "height": 2000})

            page = context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=45000)

            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                pass

            page.wait_for_timeout(wait_ms)
            for _ in range(scroll_rounds):
                page.mouse.wheel(0, 3200)
                page.wait_for_timeout(1000)

            raw_items = _extract_items(page, platform, max_items)

            if context is not None:
                context.close()
            if browser is not None:
                browser.close()

        seen = set()
        for item in raw_items:
            entry = _entry_from_item(item, platform)
            link = entry.get("link", "")
            if not link or link in seen:
                continue
            seen.add(link)
            entries.append(entry)
            if len(entries) >= max_items:
                break

    except Exception as exc:
        message = str(exc)
        status = "timeout" if "timeout" in message.lower() else "failed"
        return {
            "url": target_url,
            "entry_count": 0,
            "entries": [],
            "fetch_status": status,
            "fetch_method": "connector:playwright_profile",
            "error": message,
        }

    return {
        "url": target_url,
        "entry_count": len(entries),
        "entries": entries,
        "fetch_status": "success",
        "fetch_method": "connector:playwright_profile",
    }
