"""HTML scraper for content sources that don't have working RSS feeds.

Used as a fallback when RSS parsing fails (e.g., OpenAI blog, Anthropic news).
Each source has a custom parser that extracts stories from the HTML page.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import requests
except ImportError:
    requests = None

from genlab_core.cache.text_sanitizer import sanitize_text

logger = logging.getLogger(__name__)

# Browser-like headers to avoid bot blocks
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def fetch_html(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch raw HTML from a URL with browser-like headers."""
    if requests is None:
        raise ImportError("requests is required")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.warning("HTML fetch failed for %s: %s", url, exc)
        return None


def scrape_openai_blog(html: str) -> List[Dict[str, Any]]:
    """Parse OpenAI blog page for article entries."""
    if BeautifulSoup is None:
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries = []

    # OpenAI blog uses article cards with links
    # Try multiple selectors for resilience
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        # Blog post URLs match /index/... or /blog/... patterns
        if not re.match(r"/(index|blog|research)/[a-z0-9-]+", href):
            continue

        full_url = urljoin("https://openai.com", href)

        # Find title text
        title_el = link.find(["h2", "h3", "h4", "span"])
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        if not title or len(title) < 10:
            continue

        # Find date if available
        date_text = ""
        parent = link.parent
        if parent:
            date_el = parent.find("time") or parent.find(string=re.compile(r"\w+ \d{1,2}, \d{4}"))
            if date_el:
                date_text = str(date_el.string if hasattr(date_el, "string") else date_el).strip()

        # Find summary
        summary = ""
        summary_el = link.find("p") or (parent.find("p") if parent else None)
        if summary_el:
            summary = summary_el.get_text(strip=True)

        # Dedup by URL
        if not any(e["link"] == full_url for e in entries):
            entries.append(
                {
                    "title": sanitize_text(title, max_length=300),
                    "link": full_url,
                    "summary": sanitize_text(summary, max_length=1000),
                    "published": date_text,
                    "author": "OpenAI",
                }
            )

    logger.info("OpenAI HTML scraper: found %d entries", len(entries))
    return entries


def scrape_anthropic_news(html: str) -> List[Dict[str, Any]]:
    """Parse Anthropic news page for article entries."""
    if BeautifulSoup is None:
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries = []

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        # Anthropic news/research URLs
        if not re.match(r"/(news|research)/[a-z0-9-]+", href):
            continue

        full_url = urljoin("https://www.anthropic.com", href)

        title_el = link.find(["h2", "h3", "h4", "span"])
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        if not title or len(title) < 10:
            continue

        date_text = ""
        parent = link.parent
        if parent:
            date_el = parent.find("time")
            if date_el:
                date_text = date_el.get("datetime", date_el.get_text(strip=True))
            else:
                date_match = parent.find(string=re.compile(r"\w+ \d{1,2}, \d{4}"))
                if date_match:
                    date_text = str(date_match).strip()

        summary = ""
        summary_el = link.find("p") or (parent.find("p") if parent else None)
        if summary_el:
            summary = summary_el.get_text(strip=True)

        if not any(e["link"] == full_url for e in entries):
            entries.append(
                {
                    "title": sanitize_text(title, max_length=300),
                    "link": full_url,
                    "summary": sanitize_text(summary, max_length=1000),
                    "published": date_text,
                    "author": "Anthropic",
                }
            )

    logger.info("Anthropic HTML scraper: found %d entries", len(entries))
    return entries


def scrape_theverge_ai(html: str) -> List[Dict[str, Any]]:
    """Parse The Verge AI section for article entries."""
    if BeautifulSoup is None:
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries = []

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        # The Verge article URLs contain /YYYY/MM/DD/ date patterns
        if not re.search(r"/\d{4}/\d{1,2}/\d{1,2}/", href):
            continue

        full_url = urljoin("https://www.theverge.com", href)

        title_el = link.find(["h2", "h3", "h4", "h5"])
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        if not title or len(title) < 10:
            continue

        # Extract date from URL pattern
        date_match = re.search(r"/(\d{4}/\d{1,2}/\d{1,2})/", href)
        date_text = ""
        if date_match:
            try:
                date_text = datetime.strptime(date_match.group(1), "%Y/%m/%d").strftime("%a, %d %b %Y 00:00:00 +0000")
            except ValueError:
                pass

        # Look for summary in parent or sibling
        summary = ""
        parent = link.parent
        if parent:
            p_el = parent.find("p")
            if p_el:
                summary = p_el.get_text(strip=True)

        if not any(e["link"] == full_url for e in entries):
            entries.append(
                {
                    "title": sanitize_text(title, max_length=300),
                    "link": full_url,
                    "summary": sanitize_text(summary, max_length=1000),
                    "published": date_text,
                    "author": "",
                }
            )

    logger.info("The Verge HTML scraper: found %d entries", len(entries))
    return entries


def scrape_generic_news_page(html: str, base_url: str, path_pattern: str = r"/\d{4}/") -> List[Dict[str, Any]]:
    """Generic HTML scraper for news-like pages.

    Finds article links matching the path_pattern and extracts title/summary.
    """
    if BeautifulSoup is None:
        return []
    soup = BeautifulSoup(html, "html.parser")
    entries = []

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if not re.search(path_pattern, href):
            continue

        full_url = urljoin(base_url, href)

        title_el = link.find(["h2", "h3", "h4"])
        title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

        if not title or len(title) < 10:
            continue

        summary = ""
        parent = link.parent
        if parent:
            p_el = parent.find("p")
            if p_el:
                summary = p_el.get_text(strip=True)

        if not any(e["link"] == full_url for e in entries):
            entries.append(
                {
                    "title": sanitize_text(title, max_length=300),
                    "link": full_url,
                    "summary": sanitize_text(summary, max_length=1000),
                    "published": "",
                    "author": "",
                }
            )

    logger.info("Generic HTML scraper (%s): found %d entries", base_url, len(entries))
    return entries


# Registry of HTML scrapers keyed by source domain
SCRAPERS = {
    "openai.com": ("https://openai.com/blog", scrape_openai_blog),
    "anthropic.com": ("https://www.anthropic.com/news", scrape_anthropic_news),
    "theverge.com": ("https://www.theverge.com/ai-artificial-intelligence", scrape_theverge_ai),
}


def fetch_html_source(source_url: str) -> Dict[str, Any]:
    """Fetch a source via HTML scraping. Returns same format as RSS fetcher."""
    from urllib.parse import urlparse

    domain = urlparse(source_url).netloc.replace("www.", "")

    scraper_url, scraper_fn = None, None
    for key, (url, fn) in SCRAPERS.items():
        if key in domain:
            scraper_url, scraper_fn = url, fn
            break

    if not scraper_fn:
        logger.warning("No HTML scraper for %s", domain)
        return {
            "url": source_url,
            "error": f"No HTML scraper for {domain}",
            "entry_count": 0,
            "entries": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    html = fetch_html(scraper_url)
    if not html:
        return {
            "url": source_url,
            "error": f"HTML fetch failed for {scraper_url}",
            "entry_count": 0,
            "entries": [],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    entries = scraper_fn(html)
    return {
        "url": source_url,
        "feed_title": f"{domain} (HTML scraper)",
        "entry_count": len(entries),
        "entries": entries,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": "html_scraper",
    }
