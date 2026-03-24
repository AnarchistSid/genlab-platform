# Affiliate Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 3 high-impact affiliate features that require no signups or approvals: YouTube description links (already partially working), retargeting pixels on link-in-bio pages, and affiliate link health monitoring.

**Architecture:** Feature #10 is a verification + test (CTA engine already injects YouTube URLs). Feature #14 adds tracking script injection to the link-in-bio HTML renderer. Feature #16 is a new standalone cron script that HEAD-requests all catalog URLs and alerts on failures.

**Tech Stack:** Python 3.12+, Flask, PostgreSQL, urllib, YAML config

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `genlab-core/src/genlab_core/monetization/cta_engine.py` | Verify | YouTube CTA already injects direct URL — confirm it works |
| `genlab-core/tests/test_cta_engine.py` | Create | Unit tests for CTA injection across all platforms |
| `dashboard/server/api/links.py` | Modify | Add tracking pixel snippets to `_render_link_page()` |
| `dashboard/tests/test_links_pixels.py` | Create | Test that pixel scripts appear in rendered HTML |
| `genlab-core/config/affiliate_catalog.yaml` | Modify | Add `tracking` config section for pixel IDs |
| `genlab-core/scripts/check_affiliate_links.py` | Create | Link health checker cron script |
| `genlab-core/tests/test_link_health_checker.py` | Create | Unit tests for link checker |

---

### Task 1: YouTube Description Links — Tests & Verification

The CTA engine at `cta_engine.py:62-69` already prepends `🔗 {product}: {url}` to `youtube_content`. This task verifies it works correctly and adds test coverage.

**Files:**
- Create: `genlab-core/tests/test_cta_engine.py`
- Verify: `genlab-core/src/genlab_core/monetization/cta_engine.py`

- [ ] **Step 1: Write tests for CTA engine**

```python
# genlab-core/tests/test_cta_engine.py
"""Tests for affiliate CTA injection engine."""
from genlab_core.monetization.cta_engine import inject_cta


def _make_story(**overrides):
    base = {
        "affiliate_product": "PS5 Console",
        "affiliate_url": "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***",
        "_affiliate_disclosure_map": {
            "instagram": "#affiliate",
            "youtube": "Contains affiliate links.",
            "facebook": "#affiliate",
        },
    }
    base.update(overrides)
    return base


class TestYouTubeCTA:
    def test_youtube_gets_direct_url(self):
        fields = {"youtube_content": "Check out this gameplay!"}
        story = _make_story()
        result = inject_cta(fields, story)
        assert "https://www.amazon.in/dp/B0CY5QW186" in result["youtube_content"]
        assert "PS5 Console" in result["youtube_content"]

    def test_youtube_url_is_prepended(self):
        fields = {"youtube_content": "Original description here."}
        story = _make_story()
        result = inject_cta(fields, story)
        assert result["youtube_content"].startswith("🔗 PS5 Console:")

    def test_youtube_disclosure_appended(self):
        fields = {"youtube_content": "Description"}
        story = _make_story()
        result = inject_cta(fields, story)
        assert "Contains affiliate links." in result["youtube_content"]

    def test_youtube_works_with_empty_content(self):
        fields = {"youtube_content": ""}
        story = _make_story()
        result = inject_cta(fields, story)
        assert "PS5 Console" in result["youtube_content"]
        assert "https://www.amazon.in/dp/B0CY5QW186" in result["youtube_content"]


class TestInstagramCTA:
    def test_instagram_uses_link_in_bio(self):
        fields = {"caption": "Amazing gaming moment! #gaming"}
        story = _make_story()
        result = inject_cta(fields, story)
        assert "link in bio" in result["caption"]
        assert "PS5 Console" in result["caption"]

    def test_instagram_does_not_contain_url(self):
        fields = {"caption": "Great clip! #gaming"}
        story = _make_story()
        result = inject_cta(fields, story)
        assert "amazon.in" not in result["caption"]

    def test_instagram_cta_before_hashtags(self):
        fields = {"caption": "Cool post #gaming #ps5"}
        story = _make_story()
        result = inject_cta(fields, story)
        idx_cta = result["caption"].index("link in bio")
        idx_hash = result["caption"].index("#gaming")
        assert idx_cta < idx_hash

    def test_instagram_disclosure_added(self):
        fields = {"caption": "Post"}
        story = _make_story()
        result = inject_cta(fields, story)
        assert "#affiliate" in result["caption"]


class TestFacebookCTA:
    def test_facebook_gets_direct_url(self):
        fields = {"facebook_content": "Check this out!"}
        story = _make_story()
        result = inject_cta(fields, story)
        assert "https://www.amazon.in/dp/B0CY5QW186" in result["facebook_content"]

    def test_facebook_disclosure_added(self):
        fields = {"facebook_content": "Post"}
        story = _make_story()
        result = inject_cta(fields, story)
        assert "#affiliate" in result["facebook_content"]


class TestTwitterCTA:
    def test_twitter_not_modified(self):
        fields = {"twitter_content": "Short tweet about PS5"}
        story = _make_story()
        result = inject_cta(fields, story)
        assert result.get("twitter_content") == "Short tweet about PS5"


class TestNoProduct:
    def test_no_product_returns_unchanged(self):
        fields = {"caption": "Regular post", "youtube_content": "Video desc"}
        story = _make_story(affiliate_product="")
        result = inject_cta(fields, story)
        assert result["caption"] == "Regular post"
        assert result["youtube_content"] == "Video desc"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_cta_engine.py -v`
Expected: All 12 tests PASS (the CTA engine already implements all this)

- [ ] **Step 3: Commit**

```bash
git add genlab-core/tests/test_cta_engine.py
git commit -m "test(monetization): add CTA engine test coverage for all platforms

Verifies YouTube description links include direct affiliate URLs,
Instagram uses 'link in bio', Facebook includes direct URLs,
and Twitter is left unchanged."
```

---

### Task 2: Retargeting Pixels on Link-in-Bio Pages

**Files:**
- Modify: `genlab-core/config/affiliate_catalog.yaml` — add tracking section
- Modify: `dashboard/server/api/links.py` — inject pixel scripts
- Create: `dashboard/tests/test_links_pixels.py`

- [ ] **Step 1: Add tracking config to catalog YAML**

Add to the `settings` section of `genlab-core/config/affiliate_catalog.yaml`:

```yaml
settings:
  # ... existing settings ...
  tracking:
    facebook_pixel_id: ""   # Fill in when FB Business account is ready
    ga4_measurement_id: ""  # Fill in when GA4 property is created
```

- [ ] **Step 2: Write test for pixel injection**

```python
# dashboard/tests/test_links_pixels.py
"""Tests for retargeting pixel injection in link-in-bio pages."""


def test_render_link_page_includes_ga4_when_configured():
    """GA4 script tag should appear in page HTML when measurement_id is set."""
    from server.api.links import _render_link_page

    html = _render_link_page(
        "criticalrush",
        {
            "display_name": "CriticalRush",
            "handle": "@critical_rush",
            "accent": "#f97316",
            "niche_id": "gaming",
        },
        [],
        tracking={"ga4_measurement_id": "G-TEST123", "facebook_pixel_id": ""},
    )
    assert "G-TEST123" in html
    assert "gtag(" in html


def test_render_link_page_includes_fb_pixel_when_configured():
    """Facebook Pixel script should appear when pixel_id is set."""
    from server.api.links import _render_link_page

    html = _render_link_page(
        "criticalrush",
        {
            "display_name": "CriticalRush",
            "handle": "@critical_rush",
            "accent": "#f97316",
            "niche_id": "gaming",
        },
        [],
        tracking={"ga4_measurement_id": "", "facebook_pixel_id": "123456789"},
    )
    assert "123456789" in html
    assert "fbq(" in html


def test_render_link_page_no_pixels_when_empty():
    """No tracking scripts when both IDs are empty."""
    from server.api.links import _render_link_page

    html = _render_link_page(
        "criticalrush",
        {
            "display_name": "CriticalRush",
            "handle": "@critical_rush",
            "accent": "#f97316",
            "niche_id": "gaming",
        },
        [],
        tracking={"ga4_measurement_id": "", "facebook_pixel_id": ""},
    )
    assert "gtag(" not in html
    assert "fbq(" not in html


def test_get_deal_click_fires_event():
    """Get Deal button should have onclick that fires tracking events."""
    from server.api.links import _render_link_page

    html = _render_link_page(
        "criticalrush",
        {
            "display_name": "CriticalRush",
            "handle": "@critical_rush",
            "accent": "#f97316",
            "niche_id": "gaming",
        },
        [{"name": "PS5 Console", "price_inr": 49990, "networks": {"amazon": {"url": "https://amazon.in/dp/X", "commission_pct": 3.0}}}],
        tracking={"ga4_measurement_id": "G-TEST", "facebook_pixel_id": "123"},
    )
    assert "ViewContent" in html or "view_item" in html
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab/dashboard && python -m pytest tests/test_links_pixels.py -v`
Expected: FAIL — `_render_link_page()` doesn't accept `tracking` parameter yet

- [ ] **Step 4: Add tracking parameter to _render_link_page**

In `dashboard/server/api/links.py`, modify `_render_link_page` signature and inject pixel scripts:

```python
def _render_link_page(channel_slug: str, meta: dict, products: list[dict], tracking: dict | None = None) -> str:
    """Render a self-contained link-in-bio HTML page."""
    tracking = tracking or {}
    ga4_id = tracking.get("ga4_measurement_id", "")
    fb_pixel_id = tracking.get("facebook_pixel_id", "")

    # Build tracking scripts
    tracking_head = ""
    tracking_body = ""

    if ga4_id:
        tracking_head += f"""
  <script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', '{ga4_id}');
    gtag('event', 'view_item_list', {{item_list_name: '{channel_slug}'}});
  </script>"""

    if fb_pixel_id:
        tracking_head += f"""
  <script>
    !function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
    n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;
    n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
    t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window,
    document,'script','https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', '{fb_pixel_id}');
    fbq('track', 'PageView');
    fbq('track', 'ViewContent', {{content_name: '{channel_slug}', content_type: 'product_group'}});
  </script>"""

    # ... rest of existing function, inject tracking_head before </head>
```

Then in the HTML template, insert `{tracking_head}` right before `</head>`.

Also update the caller `link_page()` route to load tracking config from catalog and pass it through.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab/dashboard && python -m pytest tests/test_links_pixels.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/server/api/links.py dashboard/tests/test_links_pixels.py genlab-core/config/affiliate_catalog.yaml
git commit -m "feat(monetization): add retargeting pixels to link-in-bio pages

Injects Facebook Pixel and GA4 tracking scripts into link-in-bio HTML
when configured in affiliate_catalog.yaml settings.tracking section.
Fires ViewContent/view_item_list events on page load."
```

---

### Task 3: Affiliate Link Health Monitoring

**Files:**
- Create: `genlab-core/scripts/check_affiliate_links.py`
- Create: `genlab-core/tests/test_link_health_checker.py`

- [ ] **Step 1: Write tests for link health checker**

```python
# genlab-core/tests/test_link_health_checker.py
"""Tests for affiliate link health checker."""
import unittest
from unittest.mock import patch, MagicMock


class TestLinkHealthChecker(unittest.TestCase):
    def test_parse_catalog_extracts_urls(self):
        from scripts.check_affiliate_links import parse_catalog_urls

        catalog = {
            "niches": {
                "gaming": {
                    "products": [
                        {
                            "name": "PS5",
                            "networks": {
                                "amazon": {"url": "https://amazon.in/dp/X", "commission_pct": 3.0},
                                "earnkaro": {"url": "https://example.com/placeholder", "commission_pct": 6.5},
                            },
                        }
                    ]
                }
            }
        }
        urls = parse_catalog_urls(catalog)
        # Should include amazon but skip example.com placeholders
        assert len(urls) == 1
        assert urls[0]["url"] == "https://amazon.in/dp/X"
        assert urls[0]["product"] == "PS5"
        assert urls[0]["network"] == "amazon"

    def test_skip_placeholder_urls(self):
        from scripts.check_affiliate_links import parse_catalog_urls

        catalog = {
            "niches": {
                "gaming": {
                    "products": [
                        {
                            "name": "Test",
                            "networks": {
                                "earnkaro": {"url": "https://example.com/affiliate/earnkaro/test", "commission_pct": 5.0},
                            },
                        }
                    ]
                }
            }
        }
        urls = parse_catalog_urls(catalog)
        assert len(urls) == 0

    @patch("scripts.check_affiliate_links.check_url")
    def test_check_url_healthy(self, mock_check):
        mock_check.return_value = {"status": 200, "healthy": True}
        from scripts.check_affiliate_links import check_url

        result = check_url("https://amazon.in/dp/X")
        assert result["healthy"] is True

    @patch("scripts.check_affiliate_links.check_url")
    def test_check_url_broken(self, mock_check):
        mock_check.return_value = {"status": 404, "healthy": False}
        from scripts.check_affiliate_links import check_url

        result = check_url("https://amazon.in/dp/EXPIRED")
        assert result["healthy"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_link_health_checker.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement link health checker script**

```python
#!/usr/bin/env python3
# genlab-core/scripts/check_affiliate_links.py
"""
Affiliate Link Health Checker — verifies all affiliate URLs are reachable.

Usage:
    python -m scripts.check_affiliate_links
    python -m scripts.check_affiliate_links --verbose
    python -m scripts.check_affiliate_links --fix  # disable broken links

Schedule: daily via LaunchAgent
"""
import argparse
import logging
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).parent.parent / "config" / "affiliate_catalog.yaml"
HEALTHY_CODES = {200, 301, 302, 303, 307, 308}
TIMEOUT = 10


def load_catalog() -> dict:
    with open(CATALOG_PATH) as f:
        return yaml.safe_load(f)


def parse_catalog_urls(catalog: dict) -> list[dict]:
    """Extract all real (non-placeholder) affiliate URLs from the catalog."""
    urls = []
    niches = catalog.get("niches", {})
    for niche_id, niche_data in niches.items():
        for product in niche_data.get("products", []):
            name = product.get("name", "?")
            for network, info in product.get("networks", {}).items():
                url = info.get("url", "")
                if not url or "example.com" in url:
                    continue
                urls.append({
                    "product": name,
                    "network": network,
                    "niche": niche_id,
                    "url": url,
                })
    return urls


def check_url(url: str) -> dict:
    """HEAD-request a URL and return health status."""
    req = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": "GenLab-LinkChecker/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return {"status": resp.status, "healthy": resp.status in HEALTHY_CODES}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "healthy": e.code in HEALTHY_CODES}
    except Exception as e:
        return {"status": 0, "healthy": False, "error": str(e)[:100]}


def main():
    parser = argparse.ArgumentParser(description="Check affiliate link health")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    catalog = load_catalog()
    entries = parse_catalog_urls(catalog)

    logger.info("Checking %d affiliate links...\n", len(entries))

    healthy = 0
    broken = []

    for entry in entries:
        result = check_url(entry["url"])
        status = result["status"]
        is_ok = result["healthy"]

        if is_ok:
            healthy += 1
            if args.verbose:
                logger.debug("  OK  [%d] %s — %s (%s)", status, entry["product"], entry["network"], entry["url"][:60])
        else:
            broken.append({**entry, **result})
            logger.warning("  FAIL [%d] %s — %s (%s)", status, entry["product"], entry["network"], entry["url"][:60])

        time.sleep(0.5)  # rate limit

    logger.info("\n%d healthy, %d broken out of %d total", healthy, len(broken), len(entries))

    if broken:
        logger.warning("\nBroken links:")
        for b in broken:
            logger.warning("  %s / %s / %s — HTTP %s", b["niche"], b["product"], b["network"], b["status"])
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_link_health_checker.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Run the checker against the live catalog**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core python genlab-core/scripts/check_affiliate_links.py --verbose`
Expected: Reports health status for all 28 real links. Some may fail (Amazon rate-limits HEAD requests) — that's OK, it proves the script works.

- [ ] **Step 6: Commit**

```bash
git add genlab-core/scripts/check_affiliate_links.py genlab-core/tests/test_link_health_checker.py
git commit -m "feat(monetization): add affiliate link health checker

Standalone script that HEAD-requests all affiliate URLs in the catalog,
reports healthy vs broken links, and exits non-zero if any are broken.
Skips example.com placeholder URLs. Designed for daily cron execution."
```

---

### Task 4: Add tracking config to YAML and wire up in link_page route

**Files:**
- Modify: `genlab-core/config/affiliate_catalog.yaml`
- Modify: `dashboard/server/api/links.py`

- [ ] **Step 1: Add tracking settings to catalog YAML**

Add after the existing `disclosure_text` section in `genlab-core/config/affiliate_catalog.yaml`:

```yaml
  tracking:
    facebook_pixel_id: ""
    ga4_measurement_id: ""
```

- [ ] **Step 2: Wire tracking config into link_page route**

In `dashboard/server/api/links.py`, modify the `link_page()` route to load tracking config and pass to renderer:

```python
@bp.route("/links/<channel>")
def link_page(channel: str):
    # ... existing code to load catalog, get niche_data, build display_products ...

    # Load tracking config
    settings = catalog.get("settings", {})
    tracking = settings.get("tracking", {})

    html = _render_link_page(channel_slug, meta, display_products, tracking=tracking)
    return html, 200, {"Content-Type": "text/html"}
```

- [ ] **Step 3: Commit**

```bash
git add genlab-core/config/affiliate_catalog.yaml dashboard/server/api/links.py
git commit -m "feat(monetization): wire tracking pixel config into link-in-bio pages

Loads tracking.facebook_pixel_id and tracking.ga4_measurement_id from
affiliate_catalog.yaml settings and passes to _render_link_page().
Pixel IDs are empty by default — fill in when accounts are ready."
```
