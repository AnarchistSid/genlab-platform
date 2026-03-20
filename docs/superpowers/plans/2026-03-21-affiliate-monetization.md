# Affiliate Monetization Engine — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add affiliate monetization to GenLab's content pipeline — product catalog, keyword matching, platform-specific CTAs, self-hosted link-in-bio pages, click tracking, and revenue dashboard.

**Architecture:** New `monetization/` package in genlab-core with an `AffiliateMatch` pipeline stage that runs after content writing. Self-hosted link-in-bio pages served via the existing Flask dashboard + Cloudflare tunnel. Click tracker with geo-routing logs to a new `affiliate_clicks` PostgreSQL table. Revenue data surfaces in the dashboard's Monetisation view.

**Tech Stack:** Python 3.12, Flask, PostgreSQL (psycopg3), YAML config, Jinja2 templates (link pages), React/TypeScript (dashboard)

**Spec:** `docs/superpowers/specs/2026-03-21-affiliate-monetization-design.md`

---

## File Structure

### New Files

```
genlab-core/
├── config/
│   └── affiliate_catalog.yaml              (product catalog — 5 niches × 10 products)
├── src/genlab_core/monetization/
│   ├── __init__.py
│   ├── affiliate_matcher.py                (pipeline stage — keyword match + best commission)
│   ├── cta_engine.py                       (platform-specific CTA generation)
│   └── link_tracker.py                     (click logging + geo redirect)
└── migrations/versions/
    └── f1_create_affiliate_clicks.py       (new table migration)

dashboard/
├── server/api/
│   └── revenue.py                          (revenue summary API)
├── link-pages/
│   ├── templates/
│   │   └── link_page.html                  (Jinja2 template for link-in-bio)
│   └── static/
│       └── link-page.css                   (link page styles)
└── frontend/src/
    └── views/monetisation/
        └── (modify MonetisationProgress.tsx — add Revenue section)
```

### Modified Files

```
genlab-core/src/genlab_core/
├── pipeline/stages/push_to_backlog.py      (pass affiliate fields to blueprint)
├── storage/postgres.py                     (add affiliate_clicks to PROMOTED_COLUMNS + _VALID_TABLES)

dashboard/server/
├── review_server.py                        (register revenue blueprint + link page routes)
├── api/__init__.py                         (register revenue blueprint)
```

---

## Task 1: Affiliate Product Catalog

**Files:**
- Create: `genlab-core/config/affiliate_catalog.yaml`

- [ ] **Step 1: Create the catalog with starter products**

Create `genlab-core/config/affiliate_catalog.yaml` with the structure from the spec. Start with 3 products per niche (15 total) using placeholder affiliate URLs that you'll replace with real ones later. Include:
- `settings` block with `max_affiliate_posts_per_day: 3`, `default_network_priority`, `disclosure_text` per platform
- 5 niche sections each with 3 products
- Each product: name, keywords (5-10 per product), category, networks (at least 2 with url + commission_pct), image_url, price_inr

Use real product names and realistic keywords. Placeholder URLs: `https://example.com/affiliate/{network}/{product_slug}`

- [ ] **Step 2: Commit**

```bash
git add genlab-core/config/affiliate_catalog.yaml
git commit -m "feat: affiliate product catalog — 5 niches × 3 starter products"
```

---

## Task 2: Affiliate Matcher Pipeline Stage

**Files:**
- Create: `genlab-core/src/genlab_core/monetization/__init__.py`
- Create: `genlab-core/src/genlab_core/monetization/affiliate_matcher.py`
- Create: `genlab-core/tests/monetization/test_affiliate_matcher.py`

- [ ] **Step 1: Create the monetization package**

```bash
mkdir -p genlab-core/src/genlab_core/monetization
touch genlab-core/src/genlab_core/monetization/__init__.py
mkdir -p genlab-core/tests/monetization
touch genlab-core/tests/monetization/__init__.py
```

- [ ] **Step 2: Write tests for affiliate matching**

Create `genlab-core/tests/monetization/test_affiliate_matcher.py`:

```python
"""Tests for affiliate product matching."""
import pytest
from genlab_core.monetization.affiliate_matcher import (
    match_product,
    select_best_network,
    AffiliateMatch,
)


def _sample_catalog():
    return {
        "settings": {"max_affiliate_posts_per_day": 3},
        "niches": {
            "gaming": {
                "products": [
                    {
                        "name": "PS5 Console",
                        "keywords": ["playstation", "ps5", "sony", "dualsense"],
                        "category": "hardware",
                        "networks": {
                            "amazon": {"url": "https://amzn.to/ps5", "commission_pct": 3.0},
                            "earnkaro": {"url": "https://ekaro.in/ps5", "commission_pct": 6.5},
                        },
                    },
                    {
                        "name": "Xbox Game Pass",
                        "keywords": ["xbox", "game pass", "microsoft"],
                        "category": "subscription",
                        "networks": {
                            "amazon": {"url": "https://amzn.to/gp", "commission_pct": 2.0},
                        },
                    },
                ]
            }
        },
    }


class TestMatchProduct:
    def test_matches_by_keyword(self):
        catalog = _sample_catalog()
        result = match_product(
            "PS5 just dropped a new update",
            "Sony reveals the DualSense Pro controller",
            "gaming",
            catalog,
        )
        assert result is not None
        assert result["name"] == "PS5 Console"

    def test_no_match_returns_none(self):
        catalog = _sample_catalog()
        result = match_product(
            "Random cooking video",
            "How to make pasta",
            "gaming",
            catalog,
        )
        assert result is None

    def test_picks_product_with_most_keyword_hits(self):
        catalog = _sample_catalog()
        result = match_product(
            "PS5 DualSense Sony exclusive controller review",
            "PlayStation 5 accessories",
            "gaming",
            catalog,
        )
        assert result["name"] == "PS5 Console"  # 4 keyword hits vs 0 for Xbox

    def test_wrong_niche_returns_none(self):
        catalog = _sample_catalog()
        result = match_product("PS5 review", "", "anime", catalog)
        assert result is None


class TestSelectBestNetwork:
    def test_picks_highest_commission(self):
        product = {
            "networks": {
                "amazon": {"url": "https://amzn.to/x", "commission_pct": 3.0},
                "earnkaro": {"url": "https://ekaro.in/x", "commission_pct": 6.5},
            }
        }
        network, url, pct = select_best_network(product)
        assert network == "earnkaro"
        assert pct == 6.5

    def test_single_network(self):
        product = {
            "networks": {
                "amazon": {"url": "https://amzn.to/x", "commission_pct": 3.0},
            }
        }
        network, url, pct = select_best_network(product)
        assert network == "amazon"


class TestAffiliateMatchStage:
    def test_execute_adds_affiliate_fields(self):
        stage = AffiliateMatch()
        context = {
            "niche_id": "gaming",
            "stories": [
                {
                    "title": "PS5 Pro revealed",
                    "content": {"hook": "Sony just dropped the PS5 Pro", "instagram": {"caption": "PlayStation 5 Pro is here"}},
                }
            ],
        }
        result = stage.execute(context)
        story = result["stories"][0]
        assert "affiliate_product" in story or "affiliate_product" in story.get("content", {})

    def test_execute_no_match_leaves_stories_unchanged(self):
        stage = AffiliateMatch()
        context = {
            "niche_id": "gaming",
            "stories": [
                {"title": "Random", "content": {"hook": "Nothing relevant"}},
            ],
        }
        result = stage.execute(context)
        story = result["stories"][0]
        assert story.get("affiliate_product") is None
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run --package genlab-core pytest genlab-core/tests/monetization/test_affiliate_matcher.py -v
```
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 4: Implement affiliate_matcher.py**

Create `genlab-core/src/genlab_core/monetization/affiliate_matcher.py`:

```python
"""Affiliate product matcher — keyword-based product matching with best-commission selection.

Pipeline stage that runs after content writing. Scans hook + caption + title
for keyword matches against the niche's product catalog. Selects the product
with the most hits and the network with the highest commission.

Non-fatal: if no match found, the post publishes without affiliate links.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "affiliate_catalog.yaml"
_catalog_cache: dict | None = None


def _load_catalog() -> dict:
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    if not _CATALOG_PATH.exists():
        logger.warning("[AffiliateMatch] Catalog not found: %s", _CATALOG_PATH)
        return {"settings": {}, "niches": {}}
    with open(_CATALOG_PATH) as f:
        _catalog_cache = yaml.safe_load(f) or {}
    return _catalog_cache


def match_product(
    hook: str,
    caption: str,
    niche_id: str,
    catalog: dict | None = None,
) -> dict | None:
    """Match content against the affiliate catalog for a niche.

    Returns the product dict with the most keyword hits, or None.
    """
    if catalog is None:
        catalog = _load_catalog()

    niche_products = catalog.get("niches", {}).get(niche_id, {}).get("products", [])
    if not niche_products:
        return None

    text = f"{hook} {caption}".lower()
    best_product = None
    best_hits = 0

    for product in niche_products:
        hits = sum(1 for kw in product.get("keywords", []) if kw.lower() in text)
        if hits > best_hits:
            best_hits = hits
            best_product = product

    return best_product if best_hits > 0 else None


def select_best_network(product: dict) -> tuple[str, str, float]:
    """Select the network with the highest commission for a product.

    Returns: (network_name, affiliate_url, commission_pct)
    """
    networks = product.get("networks", {})
    if not networks:
        return ("", "", 0.0)

    best = max(networks.items(), key=lambda x: x[1].get("commission_pct", 0))
    return (best[0], best[1].get("url", ""), best[1].get("commission_pct", 0))


class AffiliateMatch:
    """Pipeline stage: match content to affiliate products.

    Runs after content writing, before QC gates.
    Adds affiliate_product, affiliate_url, affiliate_network,
    affiliate_commission_pct, and affiliate_cta to each story's content.
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        niche_id = context.get("niche_id", "")
        stories = context.get("stories", [])
        catalog = _load_catalog()
        settings = catalog.get("settings", {})
        max_per_day = settings.get("max_affiliate_posts_per_day", 3)

        matched = 0
        for story in stories:
            if matched >= max_per_day:
                break

            content = story.get("content", {})
            if isinstance(content, str):
                continue

            hook = content.get("hook", "") or story.get("hook", "") or ""
            caption = content.get("instagram", {}).get("caption", "") if isinstance(content.get("instagram"), dict) else ""
            title = story.get("title", "")
            text_combined = f"{hook} {caption} {title}"

            product = match_product(text_combined, "", niche_id, catalog)
            if product is None:
                continue

            network, url, commission = select_best_network(product)
            if not url:
                continue

            story["affiliate_product"] = product["name"]
            story["affiliate_url"] = url
            story["affiliate_network"] = network
            story["affiliate_commission_pct"] = commission
            story["affiliate_cta"] = f"🔗 {product['name']} — link in bio"
            matched += 1

            logger.info(
                "[AffiliateMatch] %s matched → %s via %s (%.1f%%)",
                niche_id, product["name"], network, commission,
            )

        context.setdefault("run_stats", {})["affiliate"] = {
            "matched": matched,
            "total_stories": len(stories),
        }
        return context
```

- [ ] **Step 5: Run tests**

```bash
uv run --package genlab-core pytest genlab-core/tests/monetization/test_affiliate_matcher.py -v
```

- [ ] **Step 6: Commit**

```bash
git add genlab-core/src/genlab_core/monetization/ genlab-core/tests/monetization/
git commit -m "feat: affiliate matcher — keyword matching + best commission selection"
```

---

## Task 3: CTA Engine

**Files:**
- Create: `genlab-core/src/genlab_core/monetization/cta_engine.py`

- [ ] **Step 1: Implement CTA engine**

Platform-specific CTA generation and caption injection. Each platform gets the right CTA format:
- Instagram: "🔗 [product] — link in bio" appended before hashtags
- YouTube: "[product]: [url]" prepended to description
- Facebook: "🔗 [url]" appended to content
- Twitter: empty (link goes in reply chain)

Also handles disclosure text injection from catalog settings.

- [ ] **Step 2: Commit**

```bash
git add genlab-core/src/genlab_core/monetization/cta_engine.py
git commit -m "feat: CTA engine — platform-specific affiliate CTAs + disclosure"
```

---

## Task 4: PushToBacklog Integration

**Files:**
- Modify: `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py`

- [ ] **Step 1: Pass affiliate fields through to blueprint**

In `PushToBacklog.execute()`, after the blueprint `fields` dict is built, add:

```python
# Affiliate fields (if matched by AffiliateMatch stage)
if story.get("affiliate_product"):
    fields["affiliate_product"] = story["affiliate_product"]
    fields["affiliate_url"] = story.get("affiliate_url", "")
    fields["affiliate_network"] = story.get("affiliate_network", "")
    fields["affiliate_cta"] = story.get("affiliate_cta", "")
```

These go into the `extra` JSONB since they're not promoted columns.

- [ ] **Step 2: Inject CTA into captions before writing blueprint**

After affiliate fields are set, call the CTA engine to modify the caption:

```python
if story.get("affiliate_product"):
    from genlab_core.monetization.cta_engine import inject_cta
    fields = inject_cta(fields, story)
```

- [ ] **Step 3: Commit**

```bash
git add genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py
git commit -m "feat: pass affiliate fields to blueprints + inject CTAs into captions"
```

---

## Task 5: Database — affiliate_clicks Table

**Files:**
- Modify: `genlab-core/src/genlab_core/storage/postgres.py`

- [ ] **Step 1: Add affiliate_clicks to PROMOTED_COLUMNS and _VALID_TABLES**

Add to `postgres.py`:
```python
# In _VALID_TABLES:
"affiliate_clicks",

# In PROMOTED_COLUMNS:
"affiliate_clicks": {
    "niche_id", "product_id", "network", "affiliate_url",
    "referrer", "country", "platform_source",
},
```

- [ ] **Step 2: Create the table in PostgreSQL**

```sql
CREATE TABLE IF NOT EXISTS affiliate_clicks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    network TEXT,
    affiliate_url TEXT,
    referrer TEXT,
    country TEXT,
    platform_source TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    extra JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX idx_ac_niche ON affiliate_clicks (niche_id);
CREATE INDEX idx_ac_product ON affiliate_clicks (product_id);
CREATE INDEX idx_ac_created ON affiliate_clicks (created_at DESC);
```

- [ ] **Step 3: Commit**

```bash
git add genlab-core/src/genlab_core/storage/postgres.py
git commit -m "feat: affiliate_clicks table + PROMOTED_COLUMNS"
```

---

## Task 6: Click Tracker + Link-in-Bio Pages

**Files:**
- Create: `genlab-core/src/genlab_core/monetization/link_tracker.py`
- Create: `dashboard/link-pages/templates/link_page.html`
- Create: `dashboard/link-pages/static/link-page.css`
- Modify: `dashboard/server/review_server.py`

- [ ] **Step 1: Create link_tracker.py**

Simple click logging function that writes to the `affiliate_clicks` table.

- [ ] **Step 2: Create the link-in-bio HTML template**

Jinja2 template with:
- Channel logo + name (niche accent color)
- "Today's Pick" hero product card
- 5-8 product cards from catalog
- Each card links to `/links/go/{product_slug}`
- Dark theme matching dashboard aesthetic
- Mobile-first responsive
- No auth required (public page)

- [ ] **Step 3: Add routes to review_server.py**

```python
@app.route("/links/<channel>")
def link_page(channel):
    """Public link-in-bio page for a channel."""
    # Load catalog, render template
    ...

@app.route("/links/go/<product_id>")
def affiliate_redirect(product_id):
    """Click tracker — log click and redirect to affiliate URL."""
    # Log to affiliate_clicks table
    # 302 redirect to affiliate URL
    ...
```

Exempt both routes from auth (like webhooks and CDN media).

- [ ] **Step 4: Commit**

```bash
git add genlab-core/src/genlab_core/monetization/link_tracker.py
git add dashboard/link-pages/ dashboard/server/review_server.py
git commit -m "feat: link-in-bio pages + click tracker with affiliate redirect"
```

---

## Task 7: Revenue API + Dashboard

**Files:**
- Create: `dashboard/server/api/revenue.py`
- Modify: `dashboard/server/review_server.py` (register blueprint)
- Modify: `dashboard/frontend/src/views/monetisation/MonetisationProgress.tsx`

- [ ] **Step 1: Create revenue API endpoint**

`GET /api/v1/revenue/summary` returns:
- Total clicks (today, 7d, 30d)
- Clicks by product, by niche, by network
- Estimated revenue calculation
- Top products by clicks

- [ ] **Step 2: Add Revenue section to Monetisation dashboard view**

Add a new card/section to the existing MonetisationProgress.tsx:
- "Affiliate Revenue" header
- Click count with trend
- Estimated earnings
- Top products table
- Network comparison

- [ ] **Step 3: Build + test**

```bash
cd dashboard/frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/server/api/revenue.py dashboard/frontend/src/
git commit -m "feat: revenue API + affiliate dashboard section"
```

---

## Task 8: Wire AffiliateMatch into Niche Pipelines

**Files:**
- Modify: `ClutchWire/config/niche.yaml`
- Modify: `SpliceReel/config/niche.yaml`
- Modify: `FrameDrift/config/niche.yaml`
- Modify: `BlackboxBrief/config/niche.yaml`

- [ ] **Step 1: Add AffiliateMatch stage to all 4 non-CR niche pipelines**

In each niche.yaml, add after the writing/hooks stages:
```yaml
- class: genlab_core.monetization.affiliate_matcher.AffiliateMatch
```

- [ ] **Step 2: Test with a dry-run pipeline**

```bash
uv run --package genlab-core python -m genlab_core.pipeline --niche sports --dry-run
```

Verify AffiliateMatch stage loads and runs.

- [ ] **Step 3: Commit**

```bash
git add ClutchWire/config/ SpliceReel/config/ FrameDrift/config/ BlackboxBrief/config/
git commit -m "feat: wire AffiliateMatch into all 4 niche pipelines"
```

---

## Task 9: Final Integration Test + Rebuild

- [ ] **Step 1: Run full test suite**

```bash
uv run --package genlab-core pytest genlab-core/tests/ -k "not postgres and not integration" --tb=short -q
```

- [ ] **Step 2: Run a real pipeline with affiliate matching**

```bash
uv run --package genlab-core python -m genlab_core.pipeline --niche sports
```

Check logs for `[AffiliateMatch]` entries.

- [ ] **Step 3: Build dashboard and restart**

```bash
cd dashboard/frontend && npm run build
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.genlab.review-server.plist
sleep 2
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.genlab.review-server.plist
```

- [ ] **Step 4: Verify link-in-bio pages**

Visit `https://review.aspirehub.ai/links/blackboxbrief` — should show branded page with product cards.

- [ ] **Step 5: Verify click tracking**

Click a product link → should redirect to affiliate URL. Check `affiliate_clicks` table for the logged click.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat: affiliate monetization engine — Phase 1 complete"
git push origin main
```
