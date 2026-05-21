#!/usr/bin/env python3
"""Playwright gallery scrapers for AI creative tool platforms.

Scrapes public galleries to surface trending AI-generated content (images,
videos) from creator communities. Returns entries in the same contract as
news sources so they integrate seamlessly into the fetch → parse pipeline.

Currently supported:
  - pixwith_explore: https://pixwith.ai/explore (public, no auth)

Auth-walled (disabled):
  - imagineart_community: https://imagine.art/community (requires login)
  - revid_gallery: https://revid.ai/view (requires login)

Usage:
    # Test a single scraper
    python execution/scrapers/gallery_scrapers.py --test pixwith

    # Called programmatically from fetch_ai_creators.py
    from execution.scrapers.gallery_scrapers import fetch_gallery
    result = fetch_gallery("https://pixwith.ai/explore", "pixwith_explore")
"""

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# ======================================================================
# PIXWITH.AI EXPLORE
# ======================================================================


def scrape_pixwith_explore(
    headless: bool = True,
    max_posts: int = 30,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    """Scrape trending AI-generated content from pixwith.ai/explore.

    The gallery is a CSS grid of cards, each containing:
      - Model tag (e.g. "WAN 2.5 Fast", "Sora 2", "Veo 3.1")
      - Video/image from CDN (cdn.pixwith.ai)
      - Creator label (usually "Pixwith.AI")
      - "Create Similar Video" button

    Returns standard fetch contract with gallery_metadata extension.
    """
    url = "https://pixwith.ai/explore"

    if not PLAYWRIGHT_AVAILABLE:
        return _empty_result(url, "Playwright not installed")

    entries = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            logger.info("Pixwith scraper: loading %s", url)
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            # Wait for gallery grid to render
            try:
                page.wait_for_selector(".grid.grid-cols-2 > div", timeout=15000)
            except PlaywrightTimeout:
                logger.warning("Pixwith: timeout waiting for gallery grid")

            # Extra wait for video thumbnails to load
            page.wait_for_timeout(2000)

            # Scroll down to load more posts (lazy loading)
            for _ in range(3):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(1500)

            # Extract card data via JavaScript
            raw_cards = page.evaluate("""() => {
                const grid = document.querySelector('.grid.grid-cols-2');
                if (!grid) return [];
                return Array.from(grid.children).map((card, i) => {
                    const video = card.querySelector('video');
                    const imgs = Array.from(card.querySelectorAll('img'))
                        .map(img => img.src)
                        .filter(src => src.includes('cdn.pixwith.ai'));
                    const spans = Array.from(card.querySelectorAll('span'));
                    const modelSpan = spans.find(s =>
                        /WAN|Sora|Veo|Kling|Flux|MidJourney|Seedance|Luma|Pika/i.test(s.textContent)
                    );
                    const creatorSpan = spans.find(s =>
                        s.textContent.includes('Pixwith') || s.textContent.includes('@')
                    );
                    return {
                        index: i,
                        model: modelSpan ? modelSpan.textContent.trim() : '',
                        creator: creatorSpan ? creatorSpan.textContent.trim() : '',
                        videoSrc: video ? video.src : '',
                        thumbnails: imgs,
                        innerText: card.innerText.trim().substring(0, 200),
                    };
                });
            }""")

            browser.close()

        # Process raw cards into entries
        seen_urls = set()
        for card in raw_cards[:max_posts]:
            # Determine content type and primary media URL
            video_src = card.get("videoSrc", "")
            thumbnails = card.get("thumbnails", [])
            model = card.get("model", "Unknown")
            creator = card.get("creator", "Pixwith.AI")

            # Primary media: prefer video, fallback to thumbnail
            if video_src:
                primary_url = video_src
                content_type = "ai_video"
            elif thumbnails:
                primary_url = thumbnails[0]
                content_type = "ai_image"
            else:
                continue  # Skip cards with no media

            # Deduplicate by media URL
            if primary_url in seen_urls:
                continue
            seen_urls.add(primary_url)

            # Generate stable post ID from media URL
            url_hash = hashlib.sha256(primary_url.encode()).hexdigest()[:12]
            post_id = f"pixwith_{url_hash}"

            # Build title from model + content type
            title = f"AI {content_type.replace('ai_', '')} created with {model}"
            if creator and creator != "Pixwith.AI":
                title = f"{title} by {creator}"

            media_urls = [primary_url]
            if thumbnails and thumbnails[0] != primary_url:
                media_urls.extend(thumbnails[:1])

            entries.append(
                {
                    "title": title,
                    "link": f"https://pixwith.ai/explore#{post_id}",
                    "summary": f"Trending {content_type.replace('_', ' ')} on Pixwith.ai using {model}",
                    "published": datetime.now(timezone.utc).isoformat(),
                    "author": creator,
                    "gallery_metadata": {
                        "content_type": content_type,
                        "prompt": "",  # Not visible on explore page
                        "creator_handle": creator,
                        "model": model,
                        "media_urls": media_urls,
                        "gallery_source": "pixwith_explore",
                        "post_id": post_id,
                    },
                }
            )

    except Exception as exc:
        logger.error("Pixwith scraper failed: %s", exc, exc_info=True)
        return _empty_result(url, str(exc))

    logger.info("Pixwith scraper: extracted %d posts", len(entries))

    return {
        "url": url,
        "entry_count": len(entries),
        "entries": entries,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": "playwright_gallery",
    }


# ======================================================================
# IMAGINEART COMMUNITY (auth-walled)
# ======================================================================


def scrape_imagineart_community(
    headless: bool = True,
    max_posts: int = 30,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    """Scrape ImagineArt community gallery.

    NOTE: This gallery requires authentication. Scrolling or interacting
    triggers a login redirect. Returns empty results with a clear error.
    """
    url = "https://www.imagine.art/community"
    return _empty_result(
        url,
        "ImagineArt community requires authentication. Enable after adding auth support or API integration.",
    )


# ======================================================================
# REVID.AI GALLERY (auth-walled)
# ======================================================================


def scrape_revid_gallery(
    headless: bool = True,
    max_posts: int = 30,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    """Scrape Revid.ai video gallery.

    NOTE: /view redirects to /category (no videos), and /showcase
    requires login. Returns empty results with a clear error.
    """
    url = "https://www.revid.ai/view"
    return _empty_result(
        url,
        "Revid.ai gallery requires authentication. /view redirects to /category, /showcase needs login.",
    )


# ======================================================================
# DISPATCHER
# ======================================================================

GALLERY_SCRAPERS = {
    "pixwith_explore": scrape_pixwith_explore,
    "imagineart_community": scrape_imagineart_community,
    "revid_gallery": scrape_revid_gallery,
}


def fetch_gallery(
    url: str,
    gallery_type: str,
    headless: bool = True,
    max_posts: int = 30,
    timeout_ms: int = 30000,
) -> Dict[str, Any]:
    """Dispatch to the appropriate gallery scraper.

    Args:
        url: Gallery URL (for logging/cache keying).
        gallery_type: One of: pixwith_explore, imagineart_community, revid_gallery.
        headless: Run browser headless.
        max_posts: Max posts to extract.
        timeout_ms: Page load timeout.

    Returns:
        Standard fetch contract dict with optional gallery_metadata per entry.
    """
    scraper = GALLERY_SCRAPERS.get(gallery_type)
    if not scraper:
        logger.error("Unknown gallery type: %s", gallery_type)
        return _empty_result(url, f"Unknown gallery type: {gallery_type}")

    return scraper(headless=headless, max_posts=max_posts, timeout_ms=timeout_ms)


# ======================================================================
# HELPERS
# ======================================================================


def _empty_result(url: str, error: str) -> Dict[str, Any]:
    """Return an empty fetch result with error message."""
    return {
        "url": url,
        "error": error,
        "entry_count": 0,
        "entries": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": "playwright_gallery_failed",
    }


# ======================================================================
# CLI TEST MODE
# ======================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test gallery scrapers")
    parser.add_argument(
        "--test",
        choices=["pixwith", "imagineart", "revid", "all"],
        default="all",
        help="Which scraper to test",
    )
    parser.add_argument("--headful", action="store_true", help="Run browser with UI")
    parser.add_argument("--max-posts", type=int, default=10, help="Max posts to extract")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scrapers_to_test = {
        "pixwith": ("pixwith_explore", "https://pixwith.ai/explore"),
        "imagineart": ("imagineart_community", "https://www.imagine.art/community"),
        "revid": ("revid_gallery", "https://www.revid.ai/view"),
    }

    if args.test == "all":
        targets = scrapers_to_test
    else:
        targets = {args.test: scrapers_to_test[args.test]}

    for name, (gallery_type, url) in targets.items():
        print(f"\n{'=' * 50}")
        print(f"Testing: {name} ({url})")
        print(f"{'=' * 50}")

        result = fetch_gallery(
            url,
            gallery_type,
            headless=not args.headful,
            max_posts=args.max_posts,
        )

        if result.get("error"):
            print(f"ERROR: {result['error']}")
        else:
            print(f"Entries: {result['entry_count']}")
            for entry in result["entries"][:3]:
                meta = entry.get("gallery_metadata", {})
                print(f"  - {entry['title'][:60]}")
                print(f"    Model: {meta.get('model', '?')}, Type: {meta.get('content_type', '?')}")
                print(f"    Media: {meta.get('media_urls', ['?'])[0][:80]}")

        print("\nRaw result preview:")
        print(json.dumps(result, indent=2)[:1000])
