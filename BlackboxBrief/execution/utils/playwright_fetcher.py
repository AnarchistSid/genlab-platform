"""Playwright-based fetcher for JS-rendered pages.

Some sources (OpenAI blog, Anthropic news) serve JS-rendered SPAs that
feedparser/requests can't parse. This module uses Playwright to render
the page and extract article content.

Requires: pip install playwright && python -m playwright install chromium

Falls back gracefully if Playwright is not installed — the main pipeline
simply skips these sources.

Usage:
    from execution.utils.playwright_fetcher import fetch_js_rendered_source
    result = fetch_js_rendered_source("https://openai.com/blog")
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# Check if Playwright is available
try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.debug("Playwright not installed — JS-rendered sources will be skipped")

# Source-specific extraction configs
JS_SOURCE_CONFIGS = {
    "openai.com": {
        "url": "https://openai.com/index",
        "wait_selector": "a[href*='/index/']",
        "article_selector": "a[href*='/index/']",
        "title_attr": None,  # Extract from inner text
        "link_prefix": "https://openai.com",
        "max_articles": 20,
        "wait_timeout_ms": 15000,
    },
    "anthropic.com": {
        "url": "https://www.anthropic.com/news",
        "wait_selector": "a[href*='/news/']",
        "article_selector": "a[href*='/news/']",
        "title_attr": None,
        "link_prefix": "https://www.anthropic.com",
        "max_articles": 20,
        "wait_timeout_ms": 15000,
    },
}


def is_playwright_available() -> bool:
    """Check if Playwright is installed and ready."""
    return PLAYWRIGHT_AVAILABLE


def fetch_js_rendered_source(
    url: str,
    config: Optional[Dict[str, Any]] = None,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    """Fetch and extract articles from a JS-rendered page.

    Args:
        url: The page URL to render
        config: Source-specific extraction config (from JS_SOURCE_CONFIGS)
        headless: Whether to run browser headless
        timeout_ms: Page load timeout in milliseconds

    Returns:
        Dict with: url, entries (list), entry_count, fetched_at, fetch_method
    """
    if not PLAYWRIGHT_AVAILABLE:
        return {
            "url": url,
            "error": "Playwright not installed (pip install playwright && python -m playwright install chromium)",
            "entry_count": 0,
            "entries": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fetch_method": "playwright_unavailable",
        }

    # Resolve config
    domain = urlparse(url).netloc.replace("www.", "")
    if config is None:
        config = JS_SOURCE_CONFIGS.get(domain, {})

    actual_url = config.get("url", url)
    wait_selector = config.get("wait_selector", "article")
    article_selector = config.get("article_selector", "article a")
    link_prefix = config.get("link_prefix", "")
    max_articles = config.get("max_articles", 20)
    wait_timeout = config.get("wait_timeout_ms", timeout_ms)

    entries = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            logger.info("Playwright: loading %s", actual_url)
            page.goto(actual_url, wait_until="networkidle", timeout=timeout_ms)

            # Wait for content to render
            try:
                page.wait_for_selector(wait_selector, timeout=wait_timeout)
            except PlaywrightTimeout:
                logger.warning("Timeout waiting for %s on %s", wait_selector, actual_url)

            # Small additional wait for dynamic content
            page.wait_for_timeout(2000)

            # Extract article links and titles
            articles = page.query_selector_all(article_selector)
            seen_links = set()

            for article in articles[: max_articles * 2]:  # Overfetch to account for dupes
                try:
                    href = article.get_attribute("href") or ""
                    text = (article.inner_text() or "").strip()

                    # Skip empty or non-article links
                    if not href or not text or len(text) < 10:
                        continue

                    # Resolve relative URLs
                    if href.startswith("/"):
                        href = link_prefix + href
                    elif not href.startswith("http"):
                        href = urljoin(actual_url, href)

                    # Deduplicate
                    if href in seen_links:
                        continue
                    seen_links.add(href)

                    # Extract title (first line or heading)
                    title = text.split("\n")[0].strip()
                    if len(title) > 200:
                        title = title[:200]

                    # Extract summary (remaining text)
                    remaining = text.split("\n")[1:] if "\n" in text else []
                    summary = " ".join(line.strip() for line in remaining if line.strip())[:500]

                    entries.append(
                        {
                            "title": title,
                            "link": href,
                            "summary": summary,
                            "published": "",  # JS pages rarely expose dates in link elements
                            "author": "",
                        }
                    )

                    if len(entries) >= max_articles:
                        break

                except Exception as exc:
                    logger.debug("Error extracting article element: %s", exc)
                    continue

            browser.close()

    except Exception as exc:
        logger.error("Playwright fetch failed for %s: %s", url, exc)
        return {
            "url": url,
            "error": str(exc),
            "entry_count": 0,
            "entries": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fetch_method": "playwright_failed",
        }

    logger.info("Playwright: extracted %d articles from %s", len(entries), actual_url)

    return {
        "url": url,
        "entry_count": len(entries),
        "entries": entries,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": "playwright",
    }


def fetch_js_article_html(url: str, headless: bool = True, timeout_ms: int = 20000) -> Optional[str]:
    """Fetch full rendered HTML of a single article page.

    Useful for media extraction from JS-rendered article pages.
    """
    if not PLAYWRIGHT_AVAILABLE:
        logger.debug("Playwright not available for article HTML fetch")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        logger.warning("Playwright article fetch failed for %s: %s", url, exc)
        return None
