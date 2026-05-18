# Affiliate Monetization Engine — Design Spec

**Date:** 2026-03-21
**Scope:** Phase 1 — First revenue (affiliate links, link-in-bio, click tracking, CTA engine)
**Full vision:** 45 features across 8 phases → Revenue Operating System (see bottom)

---

## Phase 1 Scope (this spec)

Build the minimum system that generates affiliate revenue from day 1:
1. Affiliate product catalog (YAML config)
2. Affiliate matcher pipeline stage
3. Smart CTA engine (platform-specific)
4. Link-in-bio pages (self-hosted, 5 channels)
5. Click tracker with geo-routing
6. Revenue tracking in dashboard
7. Compliance/disclosure automation

---

## 1. Affiliate Product Catalog

**File:** `genlab-core/config/affiliate_catalog.yaml`

```yaml
settings:
  max_affiliate_posts_per_day: 3  # out of 5 daily posts, max 3 have affiliate
  default_network_priority: [earnkaro, cuelinks, vcommission, amazon]
  disclosure_text:
    instagram: "#affiliate"
    youtube: "Contains affiliate links — we may earn a commission"
    facebook: "#affiliate"
    twitter: ""  # too short for disclosure

niches:
  gaming:
    products:
      - name: "PS5 Console"
        keywords: [playstation, ps5, sony, dualsense, ps5 pro]
        category: hardware
        networks:
          amazon: { url: "https://amzn.to/xxx", commission_pct: 3.0 }
          earnkaro: { url: "https://ekaro.in/xxx", commission_pct: 6.5 }
        image_url: "https://m.media-amazon.com/images/I/xxx.jpg"
        price_inr: 49990

      - name: "Xbox Game Pass Ultimate"
        keywords: [xbox, game pass, microsoft, series x, halo, forza]
        category: subscription
        networks:
          amazon: { url: "https://amzn.to/xxx", commission_pct: 2.0 }
          earnkaro: { url: "https://ekaro.in/xxx", commission_pct: 5.0 }
        image_url: "..."
        price_inr: 699

      # 8-10 more gaming products...

  sports:
    products:
      - name: "NBA League Pass"
        keywords: [nba, basketball, league pass, playoffs]
        category: subscription
        networks:
          earnkaro: { url: "https://ekaro.in/xxx", commission_pct: 8.0 }
        price_inr: 1499
      # 8-10 more sports products...

  movies:
    products:
      - name: "Netflix Premium"
        keywords: [netflix, streaming, series, binge, original]
        category: subscription
        networks:
          cuelinks: { url: "https://cuel.ink/xxx", commission_pct: 10.0 }
        price_inr: 649
      # 8-10 more movies products...

  anime:
    products:
      - name: "Crunchyroll Premium"
        keywords: [crunchyroll, anime, streaming, subbed, dubbed, simulcast]
        category: subscription
        networks:
          cuelinks: { url: "https://cuel.ink/xxx", commission_pct: 8.0 }
        price_inr: 79
      # 8-10 more anime products...

  ai_creators:
    products:
      - name: "ChatGPT Plus"
        keywords: [chatgpt, openai, gpt, gpt-4, ai chat]
        category: tool
        networks:
          earnkaro: { url: "https://ekaro.in/xxx", commission_pct: 5.0 }
        price_inr: 1680
      # 8-10 more AI products...
```

---

## 2. Affiliate Matcher Pipeline Stage

**File:** `genlab-core/src/genlab_core/monetization/affiliate_matcher.py`

Runs after content writing, before QC gates. Scans hook + caption + title for keyword matches against the niche's product catalog.

**Logic:**
1. Load catalog for this niche
2. For each product, count keyword hits in `hook + caption + title + youtube_content + twitter_content`
3. Pick the product with the most keyword hits (minimum 1 hit required)
4. For that product, select the network with the highest `commission_pct`
5. Add to blueprint fields: `affiliate_product`, `affiliate_url`, `affiliate_network`, `affiliate_commission_pct`, `affiliate_cta`
6. Respect `max_affiliate_posts_per_day` — skip if daily limit reached
7. Non-fatal: if no match, post publishes without affiliate link

**Pipeline integration:**
```yaml
# In niche.yaml pipeline stages:
- class: genlab_core.monetization.affiliate_matcher.AffiliateMatch
```

**Output fields added to blueprint:**
```python
fields["affiliate_product"] = "PS5 Console"
fields["affiliate_url"] = "https://ekaro.in/xxx"  # highest commission
fields["affiliate_network"] = "earnkaro"
fields["affiliate_commission_pct"] = 6.5
fields["affiliate_cta"] = "🔗 Get the PS5 — link in bio"
```

---

## 3. Smart CTA Engine

**File:** `genlab-core/src/genlab_core/monetization/cta_engine.py`

Generates platform-specific CTAs and injects them into captions.

**CTA templates per platform:**
```python
PLATFORM_CTAS = {
    "instagram": [
        "🔗 {product} — link in bio",
        "Get yours → link in bio",
        "Best deal on {product} → link in bio",
    ],
    "youtube": [
        "🔗 {product}: {url}",
        "Get {product} here → {url}",
        "We use {product} daily → {url}",
    ],
    "facebook": [
        "🔗 Check out {product}: {url}",
        "Get the best deal → {url}",
    ],
    "twitter": [
        "",  # affiliate link goes in reply, not tweet
    ],
}
```

**Injection rules:**
- Instagram: append CTA to caption body (before hashtags)
- YouTube: prepend product link to `youtube_content` description
- Facebook: append CTA to `facebook_content`
- Twitter: queue a reply with the affiliate link (published after the main tweet)
- Add disclosure text per platform from config

---

## 4. Link-in-Bio Pages

**Directory:** `dashboard/link-pages/`

Self-hosted landing pages, one per channel, served via Cloudflare tunnel.

**URL:** `https://review.aspirehub.ai/links/{channel_slug}`

**Route:** Add to Flask app in `review_server.py`:
```python
@app.route("/links/<channel>")
def link_page(channel):
    # Serve branded link page — NO auth required (public)
    ...
```

**Page structure:**
- Channel logo + name (niche accent color header)
- "Today's Pick" — hero product card matching the latest published post
- 5-8 evergreen product cards (from catalog, sorted by commission)
- Each card: product image, name, price, "Get Deal →" button
- All links go through click tracker: `/links/go/{product_id}`
- Facebook Pixel snippet + GA4 tag (configurable in YAML)
- Responsive (mobile-first — most clicks come from mobile)
- Dark theme matching the dashboard aesthetic

**Auth:** Link pages are PUBLIC (exempted from session auth, like webhooks and CDN media).

---

## 5. Click Tracker

**File:** `genlab-core/src/genlab_core/monetization/link_tracker.py`
**Route:** `https://review.aspirehub.ai/links/go/{product_id}`

**Flow:**
1. User clicks product link on link-in-bio page
2. Server logs: product_id, niche_id, timestamp, referrer, user_agent, IP country (via simple geo lookup)
3. Selects the correct affiliate URL:
   - If geo-targeting enabled and user is from US → use Amazon US link
   - Otherwise → use highest-commission network link
4. 302 redirect to the affiliate URL

**Storage:** New `affiliate_clicks` table in PostgreSQL:
```sql
CREATE TABLE affiliate_clicks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id TEXT NOT NULL,
    niche_id TEXT NOT NULL,
    network TEXT,
    affiliate_url TEXT,
    referrer TEXT,
    country TEXT,
    platform_source TEXT,  -- which platform drove the click (instagram, youtube, etc.)
    created_at TIMESTAMPTZ DEFAULT now()
);
```

---

## 6. Revenue Tracking in Dashboard

**Backend:** New `/api/v1/revenue/summary` endpoint returning:
- Total clicks (today, 7d, 30d)
- Clicks by product, by niche, by network
- Estimated revenue: clicks × estimated_conversion_rate (2%) × avg_order_value × commission_pct
- Top products by clicks

**Frontend:** New section in Monetisation view:
- "Affiliate Revenue" card with click count + estimated earnings
- Product click leaderboard
- Network comparison chart
- Click trend sparkline (7 days)

---

## 7. Compliance

- Auto-append disclosure text from `affiliate_catalog.yaml` settings
- Instagram: `#affiliate` added to hashtags
- YouTube: disclosure line prepended to description
- Facebook: `#affiliate` appended
- Audit trail: every affiliate placement logged in blueprint `extra` JSONB

---

## Backend API Changes

| Endpoint | Status | Purpose |
|----------|--------|---------|
| `GET /links/<channel>` | New | Public link-in-bio page |
| `GET /links/go/<product_id>` | New | Click tracker redirect |
| `GET /api/v1/revenue/summary` | New | Revenue dashboard data |

---

## Database Changes

| Table | Status | Purpose |
|-------|--------|---------|
| `affiliate_clicks` | New | Click tracking |
| `blueprints.extra` | Extended | Stores affiliate_product, affiliate_url, etc. |

---

## Full Vision (Phases 2-8 — future specs)

| Phase | Features | Revenue Stream |
|-------|----------|---------------|
| 2 | Platform-specific links (YT desc, Twitter reply), QR overlays, geo-targeting | 3-5x more clicks |
| 3 | Revenue reward in bandit, financial product affiliates, predictive modeling | Higher conversion |
| 4 | Brand CRM, auto-outreach, media kit, sponsorship slots | Brand deal revenue |
| 5 | Email capture, newsletters, digital products, premium community | Owned revenue |
| 6 | AI shopping assistant, shoppable video overlays, voice commerce | Conversational commerce |
| 7 | Trend Intelligence API, data products, content syndication | Data revenue |
| 8 | GenLab Cloud SaaS (white-label for other creators) | Recurring SaaS revenue |

**Total potential:** ₹15L-1.5Cr/year across all streams.
