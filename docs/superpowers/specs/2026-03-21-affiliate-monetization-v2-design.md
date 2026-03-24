# Affiliate Monetization v2 — Design Spec

**Date:** 2026-03-21
**Scope:** Phase 2 — 16 features across 4 sub-projects
**Prerequisite:** Phase 1 complete (this spec extends the system built today)
**Full vision:** 8-phase roadmap toward Revenue Operating System (see bottom)

---

## Current State (Phase 1 — completed 2026-03-21)

| Component | File | What it does |
|-----------|------|-------------|
| Product catalog | `genlab-core/config/affiliate_catalog.yaml` | 15 products, 5 niches, 4 networks (Amazon IN/US, Cuelinks, EarnKaro) |
| Affiliate matcher | `genlab-core/src/genlab_core/monetization/affiliate_matcher.py` | Pipeline stage: keyword match content to products, pick highest-commission network |
| CTA engine | `genlab-core/src/genlab_core/monetization/cta_engine.py` | Inject platform-specific CTAs into IG caption, YT description, FB content |
| Link tracker | `genlab-core/src/genlab_core/monetization/link_tracker.py` | Log clicks to PostgreSQL `affiliate_clicks` table |
| Link-in-bio pages | `dashboard/server/api/links.py` | Public HTML pages with geo-routing (CF-IPCountry), product cards, click redirects |
| Revenue API | `dashboard/server/api/revenue.py` | Click stats + estimated revenue (clicks x 2% conversion x avg_order x commission) |
| PushToBacklog | `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py` | Writes affiliate fields to blueprint |

**Network stack:**

| Network | Status | Market | Tags/IDs |
|---------|--------|--------|----------|
| Amazon IN | LIVE | India | ***REMOVED*** |
| Amazon US | LIVE | US/Global | ***REMOVED*** |
| Cuelinks | LIVE | India | cid=***REMOVED*** |
| EarnKaro | Pending approval | India | -- |
| Impact.com | Signed up, pending brand approvals | Global | -- |

**Phase 1 limitations this spec addresses:**
- Static YAML catalog (3 products/niche) -- no dynamic product discovery
- Manual geo-routing logic -- Amazon OneLink handles this natively
- No revenue attribution back to specific posts/channels
- No seasonal awareness (same products year-round)
- Instagram CTA is "link in bio" only -- no direct links on YouTube/Facebook
- No CTA optimization (fixed templates, no A/B testing)
- No email capture or retargeting on link-in-bio pages
- No broken link detection

---

## Sub-project A: Catalog & Network Foundation

### Feature 1: Amazon OneLink

**What:** Replace manual IN/US geo-routing with Amazon's OneLink service. OneLink auto-redirects buyers to their local Amazon store using a single URL.

**Why:** Eliminates the `_best_network()` geo-routing logic in `links.py`. A single Amazon URL works globally. Reduces catalog complexity (one `amazon_onelink` network entry per product instead of `amazon` + `amazon_us` + future regional variants).

**How it works:**
1. Sign up for Amazon OneLink at `https://onelink.amazon.com/`
2. Associate all Amazon Associates tags: `***REMOVED***` (IN), `***REMOVED***` (US), plus new tags for UK/DE/JP/CA/AU
3. OneLink provides a base URL format: `https://www.amazon.com/dp/{ASIN}?tag=***REMOVED***&linkCode=ll1&language=en_US`
4. When a user clicks, Amazon detects their location and redirects to the local store with the correct tag

**Data flow:**
```
User clicks affiliate link
  -> /links/go/{slug}
    -> link_tracker.log_click() (unchanged)
    -> 302 redirect to Amazon OneLink URL
      -> Amazon detects user geo
        -> IN user -> amazon.in/dp/{ASIN}?tag=***REMOVED***
        -> US user -> amazon.com/dp/{ASIN}?tag=***REMOVED***
        -> UK user -> amazon.co.uk/dp/{ASIN}?tag=aspirehub-uk (new tag)
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/config/affiliate_catalog.yaml` | Replace `amazon` + `amazon_us` entries with single `amazon_onelink` entry per product |
| `dashboard/server/api/links.py` | Remove `_best_network()` geo-routing for Amazon. Simplify to: if network is `amazon_onelink`, use URL directly. Keep geo-routing for non-Amazon networks (Cuelinks, EarnKaro are India-only). |
| `genlab-core/src/genlab_core/monetization/affiliate_matcher.py` | `select_best_network()` -- no change needed (already picks highest commission regardless of network name) |

**New config in `affiliate_catalog.yaml`:**
```yaml
settings:
  amazon_onelink:
    enabled: true
    default_tag: ***REMOVED***  # US tag (OneLink associates local tags automatically)
    tags:
      IN: ***REMOVED***
      US: ***REMOVED***
      UK: aspirehub-uk       # register after Feature 4
      DE: aspirehub-de
      JP: aspirehub-jp
      CA: aspirehub-ca
      AU: aspirehub-au
```

**Catalog entry change (per product):**
```yaml
# BEFORE (Phase 1):
networks:
  amazon: { url: "https://www.amazon.in/dp/B0CY5QW186?tag=***REMOVED***", commission_pct: 3.0 }
  amazon_us: { url: "https://www.amazon.com/dp/B0DJHG2VVS?tag=***REMOVED***", commission_pct: 3.0 }

# AFTER (v2):
networks:
  amazon_onelink: { url: "https://www.amazon.com/dp/B0DJHG2VVS?tag=***REMOVED***&linkCode=ll1", commission_pct: 3.0 }
```

**Dependencies:** Feature 4 (Amazon Global) -- register regional tags before enabling OneLink for those regions.

---

### Feature 2: Expand Catalog to 50 Products

**What:** Scale from 3 products/niche to 10 products/niche (50 total). Add product categories: subscription, hardware, peripheral, gear, merchandise, tool, media, apparel.

**Why:** More products = higher match rate. Phase 1 skips ~60% of posts because no product keyword matches. With 10 products per niche (broader keyword coverage), target <30% skip rate.

**How it works:**
1. Research and curate 7 additional products per niche
2. Prioritize products with: high commission rates, broad keyword overlap with typical content, real affiliate URLs (not example.com placeholders)
3. Structure: each product has 8-15 keywords for better matching

**Product selection criteria per niche:**

| Niche | Current (3) | Add (7) | Target categories |
|-------|-------------|---------|-------------------|
| gaming | PS5, Xbox Game Pass, Razer Headset | Gaming monitor, mechanical keyboard, gaming chair, Steam Deck, gaming mouse, webcam, capture card | hardware, peripheral |
| sports | NBA League Pass, Nike Shoes, ESPN+ | Cricket bat, fitness tracker, sports sunglasses, protein supplement, yoga mat, basketball, team jersey | gear, apparel, supplement |
| movies | Netflix, Prime Video, JBL Soundbar | Disney+ Hotstar, Apple TV+, projector, Blu-ray player, popcorn maker, streaming mic, cinema camera | subscription, hardware |
| anime | Crunchyroll, One Piece Manga, Anime Figure | Funimation, manga subscription box, cosplay accessories, drawing tablet, anime poster set, light novel set, anime hoodie | merchandise, subscription, apparel |
| ai_creators | ChatGPT Plus, Midjourney, RTX 4090 | Claude Pro, Runway ML, AI microphone, ring light, green screen, coding keyboard, AI course bundle | tool, hardware, educational |

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/config/affiliate_catalog.yaml` | Add 35 new product entries across 5 niches |

**Dependencies:** None. Can start immediately.

---

### Feature 3: Impact.com + US Network Integration

**What:** Integrate Impact.com as a network for US-focused brand programs (Nike, Razer, JBL, Crunchyroll). Architecture supports future ShareASale and CJ Affiliate integration via a pluggable adapter pattern.

**Why:** Amazon commissions are 2-5%. Brand-direct programs via Impact.com pay 5-15%. Nike (8%), Razer (10%), JBL (6%), Crunchyroll (10%).

**How it works:**

```
AffiliateMatch.execute(context)
  -> match_product(text, niche_id, catalog)
  -> select_best_network(product)
    -> iterate networks, compare commission_pct
    -> if network == "impact":
        -> url is Impact.com tracking link (pre-generated in catalog)
    -> returns (network_name, url, commission_pct)
```

**New file:** `genlab-core/src/genlab_core/monetization/network_registry.py`

```python
"""Network adapter registry for affiliate link generation and validation.

Each network adapter knows how to:
1. Validate a tracking link
2. Generate a tracking link from a product identifier
3. Fetch commission rates via API (for Feature 5: auto-commission sync)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


class NetworkAdapter(Protocol):
    """Protocol for affiliate network adapters."""
    network_id: str

    def validate_url(self, url: str) -> bool:
        """Check if a URL is a valid tracking link for this network."""
        ...

    def generate_url(self, product_id: str, **kwargs) -> str:
        """Generate a tracking link. Raises if credentials missing."""
        ...

    def fetch_commission(self, product_id: str) -> float | None:
        """Fetch current commission rate. Returns None if API unavailable."""
        ...


@dataclass
class AmazonOneLinkAdapter:
    network_id: str = "amazon_onelink"
    default_tag: str = "***REMOVED***"

    def validate_url(self, url: str) -> bool:
        return "amazon." in url and "tag=" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        return f"https://www.amazon.com/dp/{product_id}?tag={self.default_tag}&linkCode=ll1"

    def fetch_commission(self, product_id: str) -> float | None:
        return None  # PA-API handles this (Feature 5)


@dataclass
class ImpactAdapter:
    network_id: str = "impact"
    account_sid: str = ""  # from IMPACT_ACCOUNT_SID env var
    auth_token: str = ""   # from IMPACT_AUTH_TOKEN env var

    def validate_url(self, url: str) -> bool:
        return "impact.com" in url or "sjv.io" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        campaign_id = kwargs.get("campaign_id", "")
        return f"https://aspirehub.sjv.io/c/{self.account_sid}/{campaign_id}/{product_id}"

    def fetch_commission(self, product_id: str) -> float | None:
        return None  # TODO: Implement Impact.com Ads API call


@dataclass
class ShareASaleAdapter:
    """Stub -- activate when user signs up for ShareASale."""
    network_id: str = "shareasale"

    def validate_url(self, url: str) -> bool:
        return "shareasale.com" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        raise NotImplementedError("ShareASale credentials not configured")

    def fetch_commission(self, product_id: str) -> float | None:
        return None


@dataclass
class CJAffiliateAdapter:
    """Stub -- activate when user signs up for CJ Affiliate."""
    network_id: str = "cj"

    def validate_url(self, url: str) -> bool:
        return "cj.com" in url or "anrdoezrs.net" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        raise NotImplementedError("CJ Affiliate credentials not configured")

    def fetch_commission(self, product_id: str) -> float | None:
        return None


# Registry -- add new adapters here
NETWORK_ADAPTERS: dict[str, NetworkAdapter] = {
    "amazon_onelink": AmazonOneLinkAdapter(),
    "impact": ImpactAdapter(),
    "shareasale": ShareASaleAdapter(),
    "cj": CJAffiliateAdapter(),
    # Legacy networks (kept for backward compat):
    # "amazon", "amazon_us", "cuelinks", "earnkaro" use raw URLs from catalog
}
```

**Catalog entries for Impact.com programs:**
```yaml
# In affiliate_catalog.yaml, under sports:
- name: "Nike Running Shoes"
  keywords: [nike, running, shoes, sneakers, air max, jordan, athletics]
  category: gear
  networks:
    amazon_onelink: { url: "https://www.amazon.com/dp/B0C8THZHJ5?tag=***REMOVED***&linkCode=ll1", commission_pct: 4.0 }
    impact: { url: "https://aspirehub.sjv.io/c/XXXX/nike/shoes", commission_pct: 8.0 }
    cuelinks: { url: "https://linksredirect.com/?cid=***REMOVED***&...", commission_pct: 7.5 }
```

**Config (`.env`):**
```bash
IMPACT_ACCOUNT_SID=your_account_sid
IMPACT_AUTH_TOKEN=your_auth_token
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/network_registry.py` | New: network adapter protocol + implementations |
| `genlab-core/config/affiliate_catalog.yaml` | Add `impact` network entries for applicable products |
| `genlab-core/src/genlab_core/monetization/affiliate_matcher.py` | Import network_registry for URL validation in `select_best_network()` |
| `dashboard/server/api/links.py` | `_best_network()` -- add `impact` to recognized networks |

**Dependencies:** Impact.com account approval (pending). Can code the adapter now, activate when approved.

---

### Feature 4: Amazon Global (UK, DE, JP, CA, AU)

**What:** Extend Amazon coverage to 7 markets (IN, US, UK, DE, JP, CA, AU). Requires registering for each country's Amazon Associates program.

**Why:** ~40% of GenLab's YouTube audience is outside IN/US. UK+DE+JP represent significant Amazon markets with higher average order values.

**How it works:**
With Feature 1 (OneLink) implemented, this is purely a registration + config task:
1. Sign up for Amazon Associates in each country
2. Get approved tracking tags
3. Add tags to `affiliate_catalog.yaml` under `settings.amazon_onelink.tags`
4. OneLink auto-routes -- no code changes

**Config change only:**
```yaml
settings:
  amazon_onelink:
    enabled: true
    tags:
      IN: ***REMOVED***
      US: ***REMOVED***
      UK: aspirehub-uk       # NEW
      DE: aspirehub-de       # NEW
      JP: aspirehub-jp       # NEW
      CA: aspirehub-ca       # NEW
      AU: aspirehub-au       # NEW
```

**Dependencies:** Feature 1 (OneLink must be set up first). Human action: register for each country's Associates program.

---

### Feature 5: Auto-Commission Rate Sync

**What:** Periodically check network APIs for commission rate changes and update the catalog automatically.

**Why:** Amazon changes commission rates 1-2x/year (e.g., electronics dropped from 4% to 3% in 2024). Stale commission data means `select_best_network()` picks the wrong network.

**How it works:**

```
Cron (daily at 02:00 UTC)
  -> CommissionSync.run()
    -> for each network adapter in NETWORK_ADAPTERS:
      -> adapter.fetch_commission(product_id)
      -> if rate != catalog rate:
        -> update affiliate_catalog.yaml (ruamel.yaml preserves formatting)
        -> log change to commission_audit table
        -> alert if rate dropped >50% (possible program termination)
```

**New file:** `genlab-core/src/genlab_core/monetization/commission_sync.py`

```python
"""Auto-sync commission rates from network APIs.

Runs as a standalone cron job (not a pipeline stage -- commission rates
are catalog-level, not per-story).

Usage:
    python -m genlab_core.monetization.commission_sync
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from genlab_core.monetization.network_registry import NETWORK_ADAPTERS

logger = logging.getLogger(__name__)


def sync_commission_rates(catalog_path: Path) -> dict[str, Any]:
    """Check each network API for commission rate changes.

    Returns a summary dict: {updated: [...], failed: [...], unchanged: int}
    """
    ...


def _update_catalog_yaml(catalog_path: Path, updates: list[dict]) -> None:
    """Write updated commission rates back to the YAML file.

    Uses round-trip YAML (ruamel.yaml) to preserve comments and formatting.
    """
    ...
```

**Database change -- new table `commission_audit`:**
```sql
CREATE TABLE commission_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    network TEXT NOT NULL,
    product_name TEXT NOT NULL,
    old_rate REAL NOT NULL,
    new_rate REAL NOT NULL,
    detected_at TIMESTAMPTZ DEFAULT now()
);
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/commission_sync.py` | New: sync runner |
| `genlab-core/src/genlab_core/monetization/network_registry.py` | `fetch_commission()` implementations for Amazon PA-API, Impact.com |
| `genlab-core/src/genlab_core/storage/postgres.py` | Add `commission_audit` to `_VALID_TABLES` |
| Alembic migration | Create `commission_audit` table |
| launchd plist or crontab | Schedule daily sync at 02:00 UTC |

**Dependencies:** Feature 3 (network_registry.py), Amazon PA-API credentials (Feature 6 provides these).

---

## Sub-project B: Dynamic Matching & Intelligence

### Feature 6: Dynamic Product Matching via Amazon PA-API 5.0

**What:** Instead of matching only against the static 50-product catalog, query Amazon's Product Advertising API 5.0 to find products matching content keywords in real-time.

**Why:** Static catalog misses long-tail matches. If a gaming video mentions "Logitech G Pro X" and that product isn't in the catalog, the post gets no affiliate link. PA-API can find it dynamically.

**How it works:**

```
AffiliateMatch.execute(context)
  -> match_product(text, niche_id, catalog)     # Phase 1: static catalog
  -> if no static match AND paapi_enabled:
    -> _dynamic_match(text, niche_id)            # Phase 2: PA-API fallback
      -> extract top 3 keywords from text
      -> call PA-API SearchItems(Keywords=..., SearchIndex=niche_category)
      -> filter results: price > 500 INR, rating >= 4.0, prime_eligible
      -> return best match with auto-generated OneLink URL
```

**New file:** `genlab-core/src/genlab_core/monetization/paapi_client.py`

```python
"""Amazon Product Advertising API 5.0 client.

Wraps the PA-API for real-time product search. Uses circuit breaker
and caching to stay within the 1 request/second rate limit.

Rate limits (PA-API 5.0):
  - 1 request/second (hard limit)
  - 8,640 requests/day max (we will use ~100/day across 5 niches)
  - Requires $5+ revenue in trailing 30d to maintain access

Credentials:
  - PAAPI_ACCESS_KEY -- from Amazon Associates PA-API dashboard
  - PAAPI_SECRET_KEY -- from Amazon Associates PA-API dashboard
  - PAAPI_PARTNER_TAG -- ***REMOVED*** (US tag, OneLink handles geo)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# PA-API endpoints per region
_PAAPI_HOSTS: dict[str, str] = {
    "us": "webservices.amazon.com",
    "in": "webservices.amazon.in",
    "uk": "webservices.amazon.co.uk",
    "de": "webservices.amazon.de",
    "jp": "webservices.amazon.co.jp",
}

# Niche -> PA-API SearchIndex mapping
_NICHE_SEARCH_INDEX: dict[str, str] = {
    "gaming": "VideoGames",
    "sports": "SportingGoods",
    "movies": "MoviesAndTV",
    "anime": "Books",       # Anime merch is under Books + Toys
    "ai_creators": "Electronics",
}


@dataclass
class PaapiProduct:
    """Product returned from PA-API search."""
    asin: str
    title: str
    price_amount: float
    price_currency: str
    image_url: str
    rating: float
    prime_eligible: bool
    detail_url: str
    category: str


class PaapiClient:
    """Amazon PA-API 5.0 client with caching and rate limiting."""

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        partner_tag: str,
        region: str = "us",
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._partner_tag = partner_tag
        self._host = _PAAPI_HOSTS.get(region, _PAAPI_HOSTS["us"])
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[datetime, list[PaapiProduct]]] = {}

    def search_products(
        self,
        keywords: str,
        search_index: str = "All",
        min_price: int = 500,
        min_rating: float = 4.0,
        max_results: int = 5,
    ) -> list[PaapiProduct]:
        """Search PA-API for products matching keywords.

        Returns up to max_results products, filtered by price and rating.
        Results are cached for cache_ttl_seconds.
        """
        ...

    def get_product_by_asin(self, asin: str) -> PaapiProduct | None:
        """Fetch a single product by ASIN. Used for link health checks (Feature 16)."""
        ...

    def _sign_request(self, payload: dict) -> dict[str, str]:
        """Generate AWS Signature v4 headers for PA-API request."""
        ...
```

**Integration with AffiliateMatch -- modified `execute()` method:**

```python
# In affiliate_matcher.py

def execute(self, context: dict[str, Any]) -> dict[str, Any]:
    ...
    for story in stories:
        # Step 1: Try static catalog match (existing logic)
        product = match_product(search_text, niche_id, catalog)

        # Step 2: If no static match, try PA-API dynamic match
        if product is None and self._paapi_enabled:
            product = self._dynamic_match(search_text, niche_id)

        if product is None:
            skipped += 1
            continue
        ...

def _dynamic_match(self, text: str, niche_id: str) -> dict[str, Any] | None:
    """Query PA-API for a product matching the text.

    Extracts top keywords, searches PA-API, returns a product dict
    in the same format as catalog products (for downstream compatibility).
    """
    ...
```

**Caching strategy:**
- Cache PA-API results in `genlab-core/.tmp/cache/paapi/` as JSON files
- Cache key: `sha256(keywords + search_index)`
- TTL: 1 hour (products change price frequently, but we don't need real-time)
- Disk cache + in-memory LRU (max 500 entries)

**Config (`.env`):**
```bash
PAAPI_ACCESS_KEY=your_access_key
PAAPI_SECRET_KEY=your_secret_key
PAAPI_PARTNER_TAG=***REMOVED***
PAAPI_ENABLED=true
```

**Config (`affiliate_catalog.yaml`):**
```yaml
settings:
  paapi:
    enabled: true
    min_price_inr: 500
    min_rating: 4.0
    max_results_per_query: 5
    cache_ttl_hours: 1
    daily_budget: 100  # max PA-API calls per day across all niches
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/paapi_client.py` | New: PA-API 5.0 client |
| `genlab-core/src/genlab_core/monetization/affiliate_matcher.py` | Add `_dynamic_match()` fallback, `_paapi_enabled` flag |
| `genlab-core/config/affiliate_catalog.yaml` | Add `settings.paapi` config block |
| `.env` | Add `PAAPI_*` credentials |

**Dependencies:** Amazon Associates PA-API access (requires $5+ trailing revenue). Feature 1 (OneLink URLs for dynamic results).

---

### Feature 7: Revenue Attribution Analytics

**What:** Track which channel, platform, niche, and specific post drives the most affiliate revenue. Feed attribution data into the learning loop bandit.

**Why:** Without attribution, we know total clicks but not which content strategy produces revenue. Attribution enables the bandit to optimize for revenue, not just engagement.

**How it works:**

```
Click arrives at /links/go/{slug}
  -> Extract: referrer (platform), CF-IPCountry (geo), UTM params
  -> NEW: Extract blueprint_id from UTM: ?utm_content={blueprint_id}
  -> log_click() now also stores: blueprint_id, channel_slug, post_url
  -> 302 redirect to affiliate URL

Revenue Attribution Query:
  affiliate_clicks
    JOIN blueprints ON affiliate_clicks.blueprint_id = blueprints.candidate_id
    -> GROUP BY niche_id, platform_source
    -> revenue_per_post = clicks x conversion_rate x avg_order x commission
```

**CTA engine change -- embed UTM params in affiliate URLs:**

```python
# In cta_engine.py

def _add_utm(url: str, blueprint_id: str, platform: str, niche_id: str) -> str:
    """Append UTM tracking parameters to an affiliate URL."""
    separator = "&" if "?" in url else "?"
    return (
        f"{url}{separator}"
        f"utm_source=genlab&utm_medium={platform}"
        f"&utm_campaign={niche_id}&utm_content={blueprint_id}"
    )
```

**Database change -- extend `affiliate_clicks` table:**
```sql
ALTER TABLE affiliate_clicks ADD COLUMN blueprint_id TEXT;
ALTER TABLE affiliate_clicks ADD COLUMN channel_slug TEXT;
ALTER TABLE affiliate_clicks ADD COLUMN utm_source TEXT;
ALTER TABLE affiliate_clicks ADD COLUMN utm_medium TEXT;
ALTER TABLE affiliate_clicks ADD COLUMN utm_campaign TEXT;
CREATE INDEX idx_ac_blueprint ON affiliate_clicks(blueprint_id);
CREATE INDEX idx_ac_niche_platform ON affiliate_clicks(niche_id, platform_source);
CREATE INDEX idx_ac_created_at ON affiliate_clicks(created_at);
```

**New API endpoint:** `GET /api/v1/revenue/attribution`

```python
# In dashboard/server/api/revenue.py

@bp.route("/attribution")
def revenue_attribution():
    """Revenue attribution breakdown by channel, platform, post.

    Returns:
    {
      "by_channel": {"criticalrush": {clicks: 45, est_revenue: 120.5}, ...},
      "by_platform": {"instagram": {clicks: 80, ...}, "youtube": {clicks: 30, ...}},
      "top_posts": [{blueprint_id: "xxx", title: "...", clicks: 12, est_revenue: 35.0}, ...],
      "by_niche_platform": {"gaming_instagram": {clicks: 20, ...}, ...}
    }
    """
```

**Bandit integration -- revenue as reward signal:**

```python
# In genlab-core/src/genlab_core/learning/reward_shaper.py

def _compute_affiliate_reward(self, blueprint_id: str) -> float:
    """Query affiliate_clicks for this blueprint and compute revenue reward.

    Called during reward computation, adds affiliate revenue as a bonus
    signal to the engagement-based reward.
    """
    # clicks = SELECT COUNT(*) FROM affiliate_clicks WHERE blueprint_id = ?
    # est_revenue = clicks * conversion_rate * avg_order * commission
    # reward_bonus = min(est_revenue / 100.0, 1.0)  # cap at 1.0 bonus
    ...
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/link_tracker.py` | Add `blueprint_id`, `channel_slug`, `utm_*` params to `log_click()` |
| `genlab-core/src/genlab_core/monetization/cta_engine.py` | `_add_utm()` helper, inject UTMs into all affiliate URLs |
| `dashboard/server/api/revenue.py` | New `/attribution` endpoint |
| `dashboard/server/api/links.py` | Parse `utm_content` from query string in `link_go()` |
| `genlab-core/src/genlab_core/learning/reward_shaper.py` | `_compute_affiliate_reward()` method |
| `genlab-core/src/genlab_core/storage/postgres.py` | Add new columns to `affiliate_clicks` PROMOTED_COLUMNS |
| Alembic migration | ALTER TABLE affiliate_clicks, add indexes |

**Dependencies:** None. Can start immediately on existing `affiliate_clicks` table.

---

### Feature 8: Revenue Prediction Model

**What:** Predict estimated affiliate earnings per post before publishing, based on: engagement prediction, product-content match quality, audience geography, and historical conversion data.

**Why:** Helps the pipeline prioritize high-revenue posts. If two posts are ready, publish the one predicted to earn more.

**How it works:**

```
write_content stage produces blueprint
  -> RevenuePrediction.execute(context)
    -> For each story with affiliate match:
      -> features = [
          match_quality (keyword hits / total keywords),
          product_price,
          commission_pct,
          niche_avg_conversion_rate,
          day_of_week,
          audience_geo_india_pct,
          historical_ctr_for_product,
          engagement_prediction (from bandit),
        ]
      -> predicted_revenue = model.predict(features)
      -> story["affiliate_predicted_revenue"] = predicted_revenue
```

**Model approach:**
- Start with a simple Ridge regression using scikit-learn
- Features: 8 dimensions (listed above)
- Training data: `affiliate_clicks` joined with `publishing_analytics` (post-level engagement)
- Retrain weekly (cron job)
- Cold start: use heuristic (`match_quality * price * commission * 0.02`) until 100+ data points

**New file:** `genlab-core/src/genlab_core/monetization/revenue_predictor.py`

```python
"""Revenue prediction for affiliate-matched posts.

Predicts estimated revenue before publishing to help prioritize
high-value content. Uses a simple linear model trained on historical
click-through and conversion data.

Usage as pipeline stage:
    stage = RevenuePrediction()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_PATH = Path(__file__).parent.parent.parent.parent / "config" / "revenue_model.bin"


class RevenuePrediction:
    """Pipeline stage: predict affiliate revenue per story."""

    def __init__(self, model_path: Path | None = None) -> None:
        self._model_path = model_path or _MODEL_PATH
        self._model = self._load_model()

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Add affiliate_predicted_revenue to each story with an affiliate match."""
        ...

    def _load_model(self):
        """Load trained model from disk, or return None for heuristic fallback."""
        ...

    def _heuristic_predict(self, story: dict[str, Any]) -> float:
        """Simple heuristic when no trained model available.

        predicted_revenue = match_quality x price x commission_pct x 0.02
        """
        ...

    def _feature_vector(self, story: dict[str, Any], context: dict[str, Any]) -> np.ndarray:
        """Extract 8D feature vector for the model."""
        ...
```

**New file:** `genlab-core/src/genlab_core/monetization/train_revenue_model.py`

```python
"""Train the revenue prediction model.

Run weekly via cron:
    python -m genlab_core.monetization.train_revenue_model

Reads from affiliate_clicks + publishing_analytics, trains a Ridge
regression, saves to config/revenue_model.bin.
"""
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/revenue_predictor.py` | New: prediction pipeline stage |
| `genlab-core/src/genlab_core/monetization/train_revenue_model.py` | New: weekly model training script |
| `genlab-core/config/revenue_model.bin` | New: serialized model (auto-generated, gitignored) |

**Dependencies:** Feature 7 (revenue attribution provides training data). Needs ~100 attributed clicks before training is meaningful.

---

### Feature 9: Seasonal Product Rotation

**What:** Auto-swap promoted products for major shopping events: Prime Day (July), Black Friday/Cyber Monday (November), Diwali (October/November), Christmas (December), Republic Day Sale (January, India).

**Why:** Shopping events drive 3-10x higher conversion rates. Promoting event-relevant products during these windows captures outsized revenue.

**How it works:**

```
AffiliateMatch.execute(context)
  -> _check_seasonal_override(niche_id, today)
    -> if today is within a seasonal window:
      -> load seasonal product overrides from catalog
      -> replace/boost seasonal products in match ranking
      -> add "seasonal_event" tag to story for CTA customization
```

**Config in `affiliate_catalog.yaml`:**
```yaml
settings:
  seasonal_events:
    - event_id: prime_day
      name: "Amazon Prime Day"
      window_start: "07-10"   # MM-DD (recalculated each year)
      window_end: "07-17"
      boost_networks: [amazon_onelink]
      boost_factor: 2.0       # double the match score for Amazon products
      cta_prefix: "Prime Day Deal"

    - event_id: black_friday
      name: "Black Friday / Cyber Monday"
      window_start: "11-20"
      window_end: "12-02"
      boost_networks: [amazon_onelink, impact]
      boost_factor: 2.5
      cta_prefix: "Black Friday"

    - event_id: diwali
      name: "Diwali Sale"
      window_start: "10-15"
      window_end: "10-30"
      boost_networks: [amazon_onelink, cuelinks, earnkaro]
      boost_factor: 2.0
      cta_prefix: "Festive Deal"

    - event_id: christmas
      name: "Christmas & Year End"
      window_start: "12-15"
      window_end: "12-31"
      boost_networks: [amazon_onelink]
      boost_factor: 1.5
      cta_prefix: "Holiday Gift"

    - event_id: republic_day
      name: "Republic Day Sale"
      window_start: "01-20"
      window_end: "01-27"
      boost_networks: [amazon_onelink, cuelinks]
      boost_factor: 1.5
      cta_prefix: "Sale Alert"
```

**Per-niche seasonal products (in `affiliate_catalog.yaml`):**
```yaml
niches:
  gaming:
    seasonal_products:
      prime_day:
        - name: "PS5 Console (Prime Day)"
          keywords: [ps5, playstation, prime day, deal]
          networks:
            amazon_onelink: { url: "...", commission_pct: 4.0 }
          price_inr: 44990  # discounted
      black_friday:
        - name: "Gaming Monitor 4K (BF Deal)"
          keywords: [monitor, 4k, gaming, black friday]
          networks:
            amazon_onelink: { url: "...", commission_pct: 5.0 }
```

**New file:** `genlab-core/src/genlab_core/monetization/seasonal_rotation.py`

```python
"""Seasonal product rotation for affiliate catalog.

Detects if the current date falls within a configured seasonal event
window and returns boosted/replacement products.

Usage:
    rotator = SeasonalRotator(catalog)
    seasonal = rotator.get_active_event()
    if seasonal:
        products = rotator.get_seasonal_products(niche_id, seasonal.event_id)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SeasonalEvent:
    event_id: str
    name: str
    boost_factor: float
    boost_networks: list[str]
    cta_prefix: str


class SeasonalRotator:
    """Detect active seasonal events and return boosted products."""

    def __init__(self, catalog: dict[str, Any]) -> None:
        self._events = self._parse_events(catalog)
        self._catalog = catalog

    def get_active_event(self, today: date | None = None) -> SeasonalEvent | None:
        """Return the currently active seasonal event, or None."""
        ...

    def get_seasonal_products(
        self, niche_id: str, event_id: str
    ) -> list[dict[str, Any]]:
        """Return seasonal product overrides for a niche + event."""
        ...

    def boost_match_score(
        self, product: dict[str, Any], base_score: int, event: SeasonalEvent
    ) -> int:
        """Multiply match score if product's network is in the boost list."""
        ...

    def _parse_events(self, catalog: dict[str, Any]) -> list[dict]:
        ...
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/seasonal_rotation.py` | New: seasonal event detection + product boost |
| `genlab-core/src/genlab_core/monetization/affiliate_matcher.py` | Integrate `SeasonalRotator` -- check for active events, boost match scores |
| `genlab-core/config/affiliate_catalog.yaml` | Add `settings.seasonal_events` + per-niche `seasonal_products` |
| `genlab-core/src/genlab_core/monetization/cta_engine.py` | Use `cta_prefix` from seasonal event (e.g., "Prime Day Deal" instead of generic CTA) |

**Dependencies:** Feature 2 (expanded catalog provides the products to rotate).

---

## Sub-project C: Platform-Specific Link Injection

### Feature 10: YouTube Description Affiliate Links

**What:** Inject direct affiliate URLs into YouTube video descriptions instead of just "link in bio." YouTube descriptions support clickable links -- the highest-converting placement.

**Why:** YouTube description links have 5-10x higher CTR than "link in bio" on Instagram. Currently, `cta_engine.py` already prepends `{product}: {url}` to `youtube_content`, but this should use the direct affiliate URL with UTM tracking, not the link-in-bio redirect.

**How it works:**

```
cta_engine.inject_cta(fields, story)
  -> YouTube: prepend direct affiliate URL (not link-in-bio)
    -> URL = affiliate_url + UTM params (Feature 7)
    -> Add structured links section:
      "LINKS:
       Get {product}: {affiliate_url}
       More deals: {link_in_bio_url}
       Follow us: {social_links}"
```

**Modified `cta_engine.py` YouTube section:**

```python
def inject_cta(fields: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
    ...
    # -- YouTube content --
    yt_content: str = fields.get("youtube_content", "") or ""
    if yt_content or url:
        # v2: use direct affiliate URL with UTM tracking (not link-in-bio)
        tracked_url = _add_utm(url, story.get("candidate_id", ""), "youtube", niche_id)
        yt_links = (
            f"\n\n---\nLINKS:\n"
            f"Get {product_name}: {tracked_url}\n"
            f"More deals: https://review.aspirehub.ai/links/{channel_slug}\n"
        )
        yt_content = yt_content.rstrip() + yt_links
        if yt_disclosure:
            yt_content = yt_content.rstrip() + f"\n\n{yt_disclosure}"
        fields["youtube_content"] = yt_content
    ...
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/cta_engine.py` | YouTube section: use direct affiliate URL, add structured LINKS section, inject UTMs |

**Dependencies:** Feature 7 (UTM tracking for attribution).

---

### Feature 11: Smarter CTA A/B Testing via Bandit

**What:** Connect CTA template selection to the existing Thompson Sampling bandit. Instead of using a fixed CTA template, treat each CTA variant as a bandit arm and learn which converts best.

**Why:** Phase 1 uses a single CTA template per platform (e.g., Instagram always gets "product -- link in bio"). A/B testing different CTA styles (urgency, benefit, social proof) can improve CTR 20-50%.

**How it works:**

```
cta_engine.inject_cta(fields, story)
  -> cta_variant = bandit.select_arm(niche_id, platform, context_features)
  -> apply CTA template from variant
  -> store variant_id in story["_cta_variant"] for reward tracking

Later (metric collection):
  -> clicks for this blueprint -> attribute to cta_variant
  -> update bandit posterior for that variant arm
```

**CTA arm definitions -- new config file:**

```yaml
# genlab-core/config/cta_variants.yaml
platforms:
  instagram:
    variants:
      - arm_id: ig_link_in_bio
        template: "{product_name} -- link in bio"
      - arm_id: ig_get_yours
        template: "Get yours -- link in bio"
      - arm_id: ig_best_deal
        template: "Best deal on {product_name} -- link in bio"
      - arm_id: ig_limited
        template: "Limited time -- {product_name} in bio"
      - arm_id: ig_we_use
        template: "We use this daily -- link in bio"

  youtube:
    variants:
      - arm_id: yt_get_here
        template: "Get {product_name} here: {url}"
      - arm_id: yt_best_price
        template: "Best price on {product_name}: {url}"
      - arm_id: yt_recommended
        template: "Our pick -- {product_name}: {url}"

  facebook:
    variants:
      - arm_id: fb_check_out
        template: "Check out {product_name}: {url}"
      - arm_id: fb_get_deal
        template: "Get the best deal: {url}"
```

**Database change -- new table `cta_bandit_arms`:**
```sql
CREATE TABLE cta_bandit_arms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    arm_id TEXT NOT NULL,
    alpha REAL DEFAULT 1.0,    -- Thompson Sampling Beta posterior
    beta REAL DEFAULT 1.0,
    total_shown INTEGER DEFAULT 0,
    total_clicked INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(niche_id, platform, arm_id)
);
```

**New file:** `genlab-core/src/genlab_core/monetization/cta_bandit.py`

```python
"""CTA variant selection via Thompson Sampling.

Wraps the existing Thompson Sampling logic from genlab_core.learning
to select CTA variants and update posteriors based on click-through.

Usage:
    bandit = CTABandit(niche_id="gaming")
    variant = bandit.select(platform="instagram")
    # ... inject CTA using variant.template ...
    # After metric collection:
    bandit.update(variant.arm_id, platform="instagram", clicked=True)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import yaml

logger = logging.getLogger(__name__)


@dataclass
class CTAVariant:
    arm_id: str
    platform: str
    template: str
    alpha: float = 1.0
    beta: float = 1.0


class CTABandit:
    """Thompson Sampling bandit for CTA variant selection."""

    def __init__(self, niche_id: str, config_path: str | None = None) -> None:
        self._niche_id = niche_id
        self._variants = self._load_variants(config_path)

    def select(self, platform: str) -> CTAVariant:
        """Select a CTA variant using Thompson Sampling."""
        candidates = [v for v in self._variants if v.platform == platform]
        if not candidates:
            return CTAVariant(arm_id="default", platform=platform, template="{product_name}")

        # Sample from Beta posteriors
        samples = [
            (np.random.beta(v.alpha, v.beta), v)
            for v in candidates
        ]
        return max(samples, key=lambda x: x[0])[1]

    def update(self, arm_id: str, platform: str, clicked: bool) -> None:
        """Update posterior after observing click/no-click."""
        ...

    def _load_variants(self, config_path: str | None) -> list[CTAVariant]:
        """Load variants from YAML config + merge with DB posteriors."""
        ...
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/cta_bandit.py` | New: CTA bandit with Thompson Sampling |
| `genlab-core/config/cta_variants.yaml` | New: CTA variant definitions per platform |
| `genlab-core/src/genlab_core/monetization/cta_engine.py` | Use `CTABandit.select()` instead of fixed templates, store `_cta_variant` on story |
| `genlab-core/src/genlab_core/storage/postgres.py` | Add `cta_bandit_arms` to `_VALID_TABLES` |
| `genlab-core/src/genlab_core/monetization/link_tracker.py` | Add `cta_variant` column to click logging |
| Alembic migration | Create `cta_bandit_arms` table, add `cta_variant` to `affiliate_clicks` |

**Dependencies:** Feature 7 (click attribution to update bandit posteriors).

---

### Feature 12: Deep Linking for Mobile

**What:** Detect mobile users on link-in-bio pages and redirect to the Amazon/Flipkart app (if installed) instead of the mobile web. App-based purchases have 2-3x higher conversion rates.

**Why:** 70%+ of link-in-bio traffic is mobile (Instagram/YouTube in-app browsers). Amazon app has higher conversion than mobile web because payment info is saved.

**How it works:**

```
User clicks /links/go/{slug}
  -> Detect User-Agent (mobile vs desktop)
  -> If mobile:
    -> Generate deep link intent URL:
      Android: intent://amazon.in/dp/{ASIN}#Intent;scheme=com.amazon.mShop.android;end
      iOS: amzn://amazon.in/dp/{ASIN}
    -> Serve a redirect page that tries deep link first, falls back to web URL after 2s
  -> If desktop:
    -> 302 redirect to web affiliate URL (unchanged)
```

**Deep link redirect page (served instead of immediate 302 for mobile):**

```html
<html>
<head>
  <meta http-equiv="refresh" content="2;url={web_affiliate_url}" />
  <script>
    window.location = "{app_deep_link}";
    setTimeout(function() {
      window.location = "{web_affiliate_url}";
    }, 1500);
  </script>
</head>
<body>
  <p>Opening app... <a href="{web_affiliate_url}">Click here</a> if not redirected.</p>
</body>
</html>
```

**New file:** `genlab-core/src/genlab_core/monetization/deep_linker.py`

```python
"""Mobile deep link generation for affiliate URLs.

Generates platform-specific deep links that open products in native
apps (Amazon, Flipkart) for higher conversion rates.

Supported apps:
  - Amazon Shopping (Android + iOS)
  - Flipkart (Android + iOS, India only)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_MOBILE_UA_PATTERNS = [
    r"iPhone|iPad|iPod",
    r"Android",
    r"Mobile",
]


@dataclass
class DeepLink:
    app_url: str           # Deep link (app)
    web_url: str           # Fallback (mobile web)
    is_ios: bool
    is_android: bool


def is_mobile(user_agent: str) -> bool:
    """Detect if the User-Agent indicates a mobile device."""
    return any(re.search(p, user_agent, re.I) for p in _MOBILE_UA_PATTERNS)


def generate_deep_link(affiliate_url: str, user_agent: str) -> DeepLink | None:
    """Generate a deep link for the given affiliate URL.

    Returns None if the URL doesn't match a supported app (non-Amazon/Flipkart).
    """
    ...


def amazon_deep_link(asin: str, tag: str, user_agent: str) -> DeepLink:
    """Generate Amazon app deep link from ASIN."""
    is_ios = bool(re.search(r"iPhone|iPad|iPod", user_agent, re.I))
    is_android = bool(re.search(r"Android", user_agent, re.I))

    web_url = f"https://www.amazon.com/dp/{asin}?tag={tag}"

    if is_android:
        app_url = (
            f"intent://www.amazon.com/dp/{asin}?tag={tag}"
            f"#Intent;scheme=https;package=com.amazon.mShop.android.shopping;"
            f"S.browser_fallback_url={web_url};end"
        )
    elif is_ios:
        app_url = f"amzn://amazon.com/dp/{asin}?tag={tag}"
    else:
        app_url = web_url

    return DeepLink(app_url=app_url, web_url=web_url, is_ios=is_ios, is_android=is_android)
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/deep_linker.py` | New: mobile detection + deep link generation |
| `dashboard/server/api/links.py` | `link_go()`: detect mobile UA, serve deep link redirect page instead of 302 |
| `genlab-core/src/genlab_core/monetization/link_tracker.py` | Add `is_mobile` boolean to click log |

**Dependencies:** Feature 1 (OneLink provides consistent ASIN-based URLs to extract from). No hard blockers.

---

## Sub-project D: Link-in-Bio & Audience Growth

### Feature 13: Email Capture on Link-in-Bio Pages

**What:** Add a "Get notified about deals" email opt-in form above product cards on link-in-bio pages. Captured emails feed into a future deals newsletter.

**Why:** Link-in-bio visitors are high-intent (they clicked from social). Capturing their email creates an owned audience for direct deal promotion (bypassing platform algorithms).

**How it works:**

```
User visits /links/{channel}
  -> Page renders with email capture form above product cards
  -> User submits email
    -> POST /links/subscribe
      -> Validate email format
      -> Store in email_subscribers table
      -> Return success message (inline, no redirect)
      -> (Future: trigger welcome email via Resend/SendGrid)
  -> Product cards render below
```

**Database change -- new table `email_subscribers`:**
```sql
CREATE TABLE email_subscribers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL,
    channel_slug TEXT NOT NULL,
    niche_id TEXT NOT NULL,
    source TEXT DEFAULT 'link_in_bio',   -- where the signup came from
    subscribed_at TIMESTAMPTZ DEFAULT now(),
    unsubscribed_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT true,
    UNIQUE(email, channel_slug)
);

CREATE INDEX idx_es_niche ON email_subscribers(niche_id) WHERE is_active = true;
```

**Link-in-bio page change (in `links.py` `_render_link_page()`):**

Add email form HTML between the header and the product cards section:
```html
<form class="email-form" action="/links/subscribe" method="POST">
  <input type="hidden" name="channel" value="{channel_slug}" />
  <p class="email-label">Get notified about exclusive deals</p>
  <div class="email-row">
    <input type="email" name="email" placeholder="your@email.com" required />
    <button type="submit" class="btn">Notify Me</button>
  </div>
</form>
```

**New route:** `POST /links/subscribe`

```python
@bp.route("/links/subscribe", methods=["POST"])
def link_subscribe():
    """Handle email subscription from link-in-bio page."""
    email = request.form.get("email", "").strip().lower()
    channel = request.form.get("channel", "")
    # Validate, store, return success/error HTML snippet
    ...
```

**File changes:**

| File | Change |
|------|--------|
| `dashboard/server/api/links.py` | Add email form to `_render_link_page()`, new `POST /links/subscribe` route |
| `genlab-core/src/genlab_core/storage/postgres.py` | Add `email_subscribers` to `_VALID_TABLES` |
| Alembic migration | Create `email_subscribers` table |

**Dependencies:** None. Standalone feature.

---

### Feature 14: Retargeting Pixels on Link-in-Bio Pages

**What:** Add Facebook Pixel and GA4 measurement tag to link-in-bio pages. This enables retargeting link-in-bio visitors with ads (future paid promotion).

**Why:** Visitors who browse products but don't buy are the highest-value retargeting audience. Facebook custom audiences from Pixel data allow $0.05-0.10 CPM retargeting -- far cheaper than cold acquisition.

**How it works:**

1. Add Facebook Pixel base code to link-in-bio page `<head>`
2. Fire `PageView` event on page load
3. Fire `ViewContent` event with product name when user scrolls to product card
4. Fire custom `ClickAffiliate` event when user clicks "Get Deal" button
5. Add GA4 gtag.js with similar event tracking

**Config (`.env`):**
```bash
FB_PIXEL_ID=your_pixel_id
GA4_MEASUREMENT_ID=G-XXXXXXXXXX
```

**Config in `affiliate_catalog.yaml`:**
```yaml
settings:
  tracking_pixels:
    facebook_pixel_id: ""         # Set in .env: FB_PIXEL_ID
    ga4_measurement_id: ""        # Set in .env: GA4_MEASUREMENT_ID
    enabled: true
```

**HTML injection (in `_render_link_page()`):**

```html
<head>
  <!-- Facebook Pixel -->
  <script>
    !function(f,b,e,v,n,t,s){...}(window,document,'script',
    'https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', '{fb_pixel_id}');
    fbq('track', 'PageView');
  </script>
  <noscript>
    <img height="1" width="1" style="display:none"
         src="https://www.facebook.com/tr?id={fb_pixel_id}&ev=PageView&noscript=1"/>
  </noscript>

  <!-- GA4 -->
  <script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', '{ga4_id}');
  </script>
</head>
```

**Click tracking (JavaScript on "Get Deal" buttons):**
```javascript
document.querySelectorAll('.btn').forEach(btn => {
  btn.addEventListener('click', function() {
    const product = this.closest('.card').querySelector('.product-name').textContent;
    if (typeof fbq !== 'undefined') {
      fbq('track', 'ViewContent', {content_name: product, content_type: 'product'});
    }
    if (typeof gtag !== 'undefined') {
      gtag('event', 'click_affiliate', {product_name: product});
    }
  });
});
```

**File changes:**

| File | Change |
|------|--------|
| `dashboard/server/api/links.py` | Inject Pixel + GA4 scripts in `_render_link_page()`, read IDs from env vars |
| `genlab-core/config/affiliate_catalog.yaml` | Add `settings.tracking_pixels` config block |

**Dependencies:** None. Requires FB Pixel and GA4 property to be created (human action).

---

### Feature 15: Coupon/Deal Aggregation

**What:** Auto-fetch active coupons and deals for catalog products. Display coupon codes on link-in-bio pages alongside product cards.

**Why:** Showing a coupon code increases CTR 2-3x ("save 10% with code SAVE10" is more compelling than a bare product link). Also improves user trust.

**How it works:**

```
Cron (every 6 hours)
  -> CouponAggregator.run()
    -> For each product in catalog:
      -> Check coupon sources:
        1. Amazon deal API (PA-API GetItems with Offers resource)
        2. Manual coupons in affiliate_catalog.yaml
        3. (Future: scrape coupon sites, brand RSS feeds)
      -> Store active coupons in coupon_cache table
      -> Expire old coupons

Link-in-bio page render
  -> Load active coupons for this niche's products
  -> Display coupon badge on product cards: "Use code: SAVE10 (-10%)"
```

**Database change -- new table `coupon_cache`:**
```sql
CREATE TABLE coupon_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_name TEXT NOT NULL,
    niche_id TEXT NOT NULL,
    coupon_code TEXT,
    discount_text TEXT,          -- "10% off" or "Rs 500 off"
    discount_type TEXT,          -- percentage, fixed, deal
    source TEXT,                 -- paapi, manual, scraped
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_cc_active ON coupon_cache(niche_id, is_active) WHERE is_active = true;
```

**Config (manual coupons in `affiliate_catalog.yaml`):**
```yaml
niches:
  gaming:
    products:
      - name: "PS5 Console"
        ...
        coupons:
          - code: "GAME10"
            discount: "10% off"
            valid_until: "2026-06-30"
            source: manual
```

**New file:** `genlab-core/src/genlab_core/monetization/coupon_aggregator.py`

```python
"""Coupon and deal aggregation for affiliate products.

Fetches active coupons from multiple sources and caches them in
PostgreSQL for display on link-in-bio pages.

Sources:
  1. Manual coupons in affiliate_catalog.yaml
  2. Amazon PA-API deal data (requires Feature 6)
  3. (Future: coupon site APIs)

Usage:
    python -m genlab_core.monetization.coupon_aggregator
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class CouponAggregator:
    """Fetch and cache active coupons for affiliate products."""

    def run(self) -> dict[str, Any]:
        """Fetch coupons from all sources, update cache, expire old ones."""
        ...

    def get_active_coupons(self, niche_id: str) -> list[dict[str, Any]]:
        """Return active coupons for a niche (used by link-in-bio renderer)."""
        ...

    def _fetch_manual_coupons(self, catalog: dict) -> list[dict]:
        """Extract manual coupons from catalog YAML."""
        ...

    def _fetch_paapi_deals(self, niche_id: str) -> list[dict]:
        """Query PA-API for active deals/coupons on catalog products."""
        ...
```

**Link-in-bio display (modified card HTML):**
```html
<div class="card">
  <div class="card-info">
    <span class="product-name">PS5 Console</span>
    <span class="price">Rs 49,990</span>
    <span class="coupon-badge">Use code: GAME10 (-10%)</span>
  </div>
  <a href="/links/go/ps5-console" class="btn">Get Deal</a>
</div>
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/coupon_aggregator.py` | New: coupon fetch + cache logic |
| `genlab-core/config/affiliate_catalog.yaml` | Add `coupons` arrays to products (manual coupons) |
| `dashboard/server/api/links.py` | Load active coupons in `link_page()`, add coupon badge to card HTML |
| `genlab-core/src/genlab_core/storage/postgres.py` | Add `coupon_cache` to `_VALID_TABLES` |
| Alembic migration | Create `coupon_cache` table |

**Dependencies:** Feature 6 (PA-API for automated deal detection). Manual coupons work without PA-API.

---

### Feature 16: Affiliate Link Health Monitoring

**What:** Cron job that checks all affiliate links for broken/expired/redirecting-to-wrong-page status. Alert on broken links and auto-swap to backup network.

**Why:** Affiliate links break when: products go out of stock, Amazon removes listings, network programs end, or URLs change. A broken link = lost revenue + bad user experience. Phase 1 has no detection.

**How it works:**

```
Cron (daily at 03:00 UTC)
  -> LinkHealthMonitor.run()
    -> For each product in catalog:
      -> For each network URL:
        -> HEAD request (follow redirects, 10s timeout)
        -> Check:
          1. HTTP status: 200/301/302 = OK, 404/410 = DEAD, 5xx = RETRY
          2. Final URL: still on expected domain? (amazon.com, not amazon.com/errors)
          3. Product availability: PA-API GetItems for Amazon ASINs
        -> Record status in link_health table
        -> If DEAD:
          -> Mark network entry as inactive
          -> If other networks available: auto-swap to next best
          -> Alert via dashboard notification
          -> Log to link_health_alerts table
```

**Database change -- new tables:**
```sql
CREATE TABLE link_health (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_name TEXT NOT NULL,
    niche_id TEXT NOT NULL,
    network TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,           -- healthy, dead, degraded, unknown
    http_status INTEGER,
    final_url TEXT,
    checked_at TIMESTAMPTZ DEFAULT now(),
    error_message TEXT
);

CREATE TABLE link_health_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_name TEXT NOT NULL,
    network TEXT NOT NULL,
    alert_type TEXT NOT NULL,       -- dead_link, product_unavailable, domain_mismatch
    message TEXT NOT NULL,
    resolved BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX idx_lh_product ON link_health(product_name, network);
CREATE INDEX idx_lha_unresolved ON link_health_alerts(resolved) WHERE resolved = false;
```

**New file:** `genlab-core/src/genlab_core/monetization/link_health_monitor.py`

```python
"""Affiliate link health monitoring.

Checks all affiliate URLs for availability, correct redirects, and
product existence. Alerts on broken links and auto-swaps to backup networks.

Usage:
    python -m genlab_core.monetization.link_health_monitor

Schedule: daily at 03:00 UTC via launchd or crontab.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_MAX_REDIRECTS = 5


@dataclass
class LinkStatus:
    product_name: str
    network: str
    url: str
    status: str          # healthy, dead, degraded
    http_status: int
    final_url: str
    error_message: str = ""


class LinkHealthMonitor:
    """Check all affiliate links and report/fix broken ones."""

    def __init__(self, catalog_path: Path | None = None) -> None:
        self._catalog_path = catalog_path

    def run(self) -> dict[str, Any]:
        """Check all links. Returns summary: {healthy: N, dead: N, swapped: N}."""
        ...

    def check_url(self, url: str) -> LinkStatus:
        """HEAD request with redirect following. Returns LinkStatus."""
        ...

    def _is_valid_destination(self, url: str, final_url: str) -> bool:
        """Check that the final URL is on the expected domain."""
        ...

    def _auto_swap(self, product: dict, dead_network: str) -> str | None:
        """Swap to next-best network for a product. Returns new network name or None."""
        ...

    def _send_alert(self, alert: dict) -> None:
        """Write alert to link_health_alerts table + log."""
        ...
```

**Dashboard integration -- new API endpoint:** `GET /api/v1/revenue/link-health`

```python
@bp.route("/link-health")
def link_health():
    """Return link health summary and unresolved alerts.

    Returns:
    {
      "total_links": 120,
      "healthy": 115,
      "dead": 3,
      "degraded": 2,
      "unresolved_alerts": [{product, network, alert_type, message, created_at}, ...]
    }
    """
```

**File changes:**

| File | Change |
|------|--------|
| `genlab-core/src/genlab_core/monetization/link_health_monitor.py` | New: health check + auto-swap logic |
| `genlab-core/src/genlab_core/storage/postgres.py` | Add `link_health`, `link_health_alerts` to `_VALID_TABLES` |
| `dashboard/server/api/revenue.py` | New `/link-health` endpoint |
| Alembic migration | Create `link_health` + `link_health_alerts` tables |
| launchd plist or crontab | Schedule daily at 03:00 UTC |

**Dependencies:** None. Works against current catalog. PA-API integration (Feature 6) enables deeper product availability checks.

---

## Database Schema Changes Summary

All changes are additive (no existing column drops or renames).

### New Tables

| Table | Feature | Purpose |
|-------|---------|---------|
| `commission_audit` | 5 | Track commission rate changes over time |
| `cta_bandit_arms` | 11 | Thompson Sampling state for CTA variants |
| `email_subscribers` | 13 | Email capture from link-in-bio pages |
| `coupon_cache` | 15 | Cached active coupons per product |
| `link_health` | 16 | Link check results |
| `link_health_alerts` | 16 | Unresolved broken link alerts |

### Altered Tables

| Table | Feature | Change |
|-------|---------|--------|
| `affiliate_clicks` | 7, 11, 12 | Add columns: `blueprint_id`, `channel_slug`, `utm_source`, `utm_medium`, `utm_campaign`, `cta_variant`, `is_mobile` |

### New Indexes

| Table | Index | Feature |
|-------|-------|---------|
| `affiliate_clicks` | `idx_ac_blueprint` | 7 |
| `affiliate_clicks` | `idx_ac_niche_platform` | 7 |
| `affiliate_clicks` | `idx_ac_created_at` | 7 |
| `email_subscribers` | `idx_es_niche` (partial, WHERE is_active) | 13 |
| `coupon_cache` | `idx_cc_active` (partial, WHERE is_active) | 15 |
| `link_health` | `idx_lh_product` | 16 |
| `link_health_alerts` | `idx_lha_unresolved` (partial, WHERE NOT resolved) | 16 |

---

## API Changes Summary

### New Endpoints

| Endpoint | Method | Auth | Feature | Purpose |
|----------|--------|------|---------|---------|
| `/api/v1/revenue/attribution` | GET | Basic | 7 | Revenue attribution by channel/platform/post |
| `/api/v1/revenue/link-health` | GET | Basic | 16 | Link health status + unresolved alerts |
| `/links/subscribe` | POST | None (public) | 13 | Email capture form submission |

### Modified Endpoints

| Endpoint | Feature | Change |
|----------|---------|--------|
| `GET /links/<channel>` | 13, 14, 15 | Add email form, tracking pixels, coupon badges |
| `GET /links/go/<slug>` | 7, 12 | Parse UTMs, deep link redirect for mobile |

---

## Config Changes Summary

### `genlab-core/config/affiliate_catalog.yaml`

```yaml
# NEW settings blocks:
settings:
  amazon_onelink:         # Feature 1
    enabled: true
    tags: {IN: ..., US: ..., UK: ..., ...}

  paapi:                  # Feature 6
    enabled: true
    min_price_inr: 500
    min_rating: 4.0
    cache_ttl_hours: 1
    daily_budget: 100

  seasonal_events:        # Feature 9
    - event_id: prime_day
      ...

  tracking_pixels:        # Feature 14
    facebook_pixel_id: ""
    ga4_measurement_id: ""
    enabled: true

# PER-PRODUCT additions:
niches:
  gaming:
    products:
      - name: "PS5 Console"
        networks:
          amazon_onelink: {...}   # Feature 1: replaces amazon + amazon_us
          impact: {...}           # Feature 3: new network
        coupons:                 # Feature 15: manual coupons
          - code: "GAME10"
            ...
    seasonal_products:           # Feature 9: event-specific products
      prime_day: [...]
```

### New Config Files

| File | Feature | Purpose |
|------|---------|---------|
| `genlab-core/config/cta_variants.yaml` | 11 | CTA template variants per platform |
| `genlab-core/config/revenue_model.bin` | 8 | Serialized prediction model (auto-generated, gitignored) |

### `.env` Additions

```bash
# Feature 3: Impact.com
IMPACT_ACCOUNT_SID=
IMPACT_AUTH_TOKEN=

# Feature 6: Amazon PA-API
PAAPI_ACCESS_KEY=
PAAPI_SECRET_KEY=
PAAPI_PARTNER_TAG=***REMOVED***
PAAPI_ENABLED=true

# Feature 14: Tracking pixels
FB_PIXEL_ID=
GA4_MEASUREMENT_ID=
```

---

## New Files Summary

| File | Feature | Type |
|------|---------|------|
| `genlab-core/src/genlab_core/monetization/network_registry.py` | 3 | Network adapter protocol + implementations |
| `genlab-core/src/genlab_core/monetization/commission_sync.py` | 5 | Commission rate auto-sync cron job |
| `genlab-core/src/genlab_core/monetization/paapi_client.py` | 6 | Amazon PA-API 5.0 client |
| `genlab-core/src/genlab_core/monetization/revenue_predictor.py` | 8 | Revenue prediction pipeline stage |
| `genlab-core/src/genlab_core/monetization/train_revenue_model.py` | 8 | Weekly model training script |
| `genlab-core/src/genlab_core/monetization/seasonal_rotation.py` | 9 | Seasonal event detection + product boost |
| `genlab-core/src/genlab_core/monetization/cta_bandit.py` | 11 | CTA variant selection via Thompson Sampling |
| `genlab-core/src/genlab_core/monetization/deep_linker.py` | 12 | Mobile deep link generation |
| `genlab-core/src/genlab_core/monetization/coupon_aggregator.py` | 15 | Coupon fetch + cache |
| `genlab-core/src/genlab_core/monetization/link_health_monitor.py` | 16 | Broken link detection + auto-swap |
| `genlab-core/config/cta_variants.yaml` | 11 | CTA variant definitions |

---

## Modified Files Summary

| File | Features | Changes |
|------|----------|---------|
| `genlab-core/config/affiliate_catalog.yaml` | 1, 2, 3, 4, 9, 14, 15 | OneLink consolidation, 35 new products, Impact entries, seasonal config, pixel config, manual coupons |
| `genlab-core/src/genlab_core/monetization/affiliate_matcher.py` | 6, 9 | PA-API dynamic fallback, seasonal rotation integration |
| `genlab-core/src/genlab_core/monetization/cta_engine.py` | 7, 9, 10, 11 | UTM injection, seasonal CTA prefix, YouTube direct links, bandit-selected CTA variants |
| `genlab-core/src/genlab_core/monetization/link_tracker.py` | 7, 11, 12 | New columns: blueprint_id, channel_slug, UTMs, cta_variant, is_mobile |
| `genlab-core/src/genlab_core/storage/postgres.py` | 5, 11, 13, 15, 16 | 6 new tables added to `_VALID_TABLES` |
| `genlab-core/src/genlab_core/learning/reward_shaper.py` | 7 | `_compute_affiliate_reward()` revenue signal in bandit |
| `dashboard/server/api/links.py` | 1, 3, 12, 13, 14, 15 | Remove geo-routing for OneLink, deep link redirect, email form, pixel scripts, coupon badges |
| `dashboard/server/api/revenue.py` | 7, 16 | Attribution endpoint, link health endpoint |

---

## Dependency Graph

```
Feature 1 (OneLink)
  |-> Feature 4 (Amazon Global) -- needs OneLink tags per region
  |-> Feature 6 (PA-API) -- OneLink URLs for dynamic results
      |-> Feature 5 (Commission Sync) -- PA-API for Amazon rates
      |-> Feature 15 (Coupons) -- PA-API for deal detection

Feature 2 (Expand Catalog) -- independent
  |-> Feature 9 (Seasonal Rotation) -- needs products to rotate

Feature 3 (Impact.com) -- independent
  |-> Feature 5 (Commission Sync) -- Impact API for rates

Feature 7 (Revenue Attribution) -- independent
  |-> Feature 8 (Revenue Prediction) -- needs attribution training data
  |-> Feature 10 (YouTube Links) -- needs UTM tracking
  |-> Feature 11 (CTA A/B) -- needs click data for bandit updates

Feature 12 (Deep Linking) -- independent
Feature 13 (Email Capture) -- independent
Feature 14 (Retargeting Pixels) -- independent
Feature 16 (Link Health) -- independent
```

---

## Implementation Order (Recommended)

### Sprint A (Week 1-2): Foundation

| Priority | Feature | Effort | Blocked by |
|----------|---------|--------|------------|
| P0 | 2. Expand Catalog | 1 day | Nothing |
| P0 | 1. Amazon OneLink | 1 day | Amazon OneLink signup (human) |
| P0 | 7. Revenue Attribution | 2 days | Nothing |
| P1 | 3. Impact.com Integration | 2 days | Impact.com approval (human) |

### Sprint B (Week 3-4): Intelligence

| Priority | Feature | Effort | Blocked by |
|----------|---------|--------|------------|
| P0 | 10. YouTube Description Links | 1 day | Feature 7 |
| P1 | 16. Link Health Monitoring | 2 days | Nothing |
| P1 | 6. PA-API Dynamic Matching | 3 days | PA-API credentials (human) |
| P2 | 9. Seasonal Rotation | 2 days | Feature 2 |

### Sprint C (Week 5-6): Optimization

| Priority | Feature | Effort | Blocked by |
|----------|---------|--------|------------|
| P1 | 11. CTA A/B Testing | 2 days | Feature 7 |
| P1 | 12. Deep Linking | 1 day | Nothing |
| P1 | 13. Email Capture | 1 day | Nothing |
| P2 | 14. Retargeting Pixels | 0.5 day | FB Pixel + GA4 setup (human) |

### Sprint D (Week 7-8): Advanced

| Priority | Feature | Effort | Blocked by |
|----------|---------|--------|------------|
| P2 | 4. Amazon Global | 0.5 day | Feature 1 + regional signups (human) |
| P2 | 5. Commission Sync | 2 days | Features 3, 6 |
| P2 | 15. Coupon Aggregation | 2 days | Feature 6 (for auto-fetch) |
| P3 | 8. Revenue Prediction | 3 days | Feature 7 (needs 100+ data points) |

**Total estimated effort:** ~24 engineering days across 8 weeks.

---

## Non-Breaking Rollout Strategy

Phase 1 must keep working during v2 development. Each feature is designed to be additive:

1. **Feature flags** -- All new behavior gated by config flags (`paapi.enabled`, `amazon_onelink.enabled`, `tracking_pixels.enabled`). Default: disabled.
2. **Backward-compatible catalog** -- Old `amazon` / `amazon_us` network keys still work. `amazon_onelink` is a new key. Migration script converts old entries but doesn't break old code reading the catalog.
3. **Database migrations** -- All ALTER TABLE adds columns with DEFAULT values. New tables don't affect existing queries.
4. **Fallback chain** -- `affiliate_matcher.py` tries: seasonal products -> static catalog -> PA-API dynamic -> skip. Each layer is optional.
5. **CTA bandit degradation** -- If `cta_variants.yaml` is missing or bandit DB is empty, falls back to Phase 1 fixed templates.

---

## Full 8-Phase Roadmap

| Phase | Name | Features | Revenue Stream | Status |
|-------|------|----------|---------------|--------|
| 1 | First Revenue | Affiliate catalog, matcher, CTA engine, link-in-bio, click tracker, revenue dashboard, compliance | Affiliate commission | **COMPLETE** (2026-03-21) |
| 2 | **Scale & Optimize** | **OneLink, 50 products, Impact.com, Amazon Global, commission sync, PA-API, attribution, prediction, seasonal rotation, YouTube links, CTA A/B, deep linking, email capture, retargeting, coupons, link health** | **3-10x more clicks + higher conversion** | **THIS SPEC** |
| 3 | Revenue Intelligence | Revenue reward in bandit, financial product affiliates (credit cards, insurance), predictive ROI modeling, A/B test infrastructure | Higher conversion | Planned |
| 4 | Brand Deals | Brand CRM, auto-outreach emails, media kit generator, sponsorship slot marketplace, rate card | Brand deal revenue | Planned |
| 5 | Owned Audience | Email newsletters (deals digest), digital products (templates, presets), premium Discord community, paid tutorials | Owned revenue | Planned |
| 6 | Conversational Commerce | AI shopping assistant on link-in-bio, shoppable video overlays (product cards in video), voice commerce prototype | Conversational commerce | Planned |
| 7 | Data Products | Trend Intelligence API (sell trend data), content syndication licensing, white-label trend reports | Data revenue | Planned |
| 8 | GenLab Cloud SaaS | Multi-tenant monetization platform, white-label for other creators, self-serve onboarding, Stripe billing | Recurring SaaS revenue | Planned |

**Total potential across all phases:** Rs 15L-1.5Cr/year.

---

## Human Action Items (before engineering can start)

| # | Action | Needed for | Effort |
|---|--------|-----------|--------|
| H1 | Sign up for Amazon OneLink, associate IN+US tags | Feature 1 | 30 min |
| H2 | Wait for Impact.com brand approvals (Nike, Razer, JBL, Crunchyroll) | Feature 3 | 1-4 weeks |
| H3 | Register for Amazon Associates in UK, DE, JP, CA, AU | Feature 4 | 2 hours |
| H4 | Apply for Amazon PA-API 5.0 access (need $5+ trailing revenue) | Feature 6 | 10 min |
| H5 | Create Facebook Pixel (Meta Business Suite) | Feature 14 | 15 min |
| H6 | Create GA4 property (Google Analytics) | Feature 14 | 15 min |
| H7 | Register for ShareASale + CJ Affiliate (optional, for Feature 3 stubs) | Feature 3 | 30 min each |

---

## Testing Strategy

Each feature requires:

1. **Unit tests** in `genlab-core/tests/monetization/` -- mock external APIs (PA-API, Impact, httpx)
2. **Integration test** -- end-to-end with real catalog, verify pipeline output
3. **Manual validation** -- click through link-in-bio pages, verify redirects work, check mobile deep links

Key test scenarios:
- OneLink URL format validation
- PA-API client with mocked responses
- Seasonal event detection at boundary dates (Jan 1, Dec 31, event start/end)
- CTA bandit convergence (verify Thompson Sampling selects best arm after N iterations)
- Deep link generation for iOS vs Android vs desktop
- Link health monitor with mocked HTTP responses (200, 404, 503, timeout)
- Commission sync with rate change detection
- Revenue prediction with heuristic fallback (no trained model)
- Email validation and dedup in subscriber table
- Coupon expiry logic

Test file naming convention: `genlab-core/tests/monetization/test_{module_name}.py`
