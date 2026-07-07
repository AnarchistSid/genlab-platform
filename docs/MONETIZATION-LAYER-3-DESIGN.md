# Monetization Layer 3 — Product Bandit + Real Attribution
## Design spec — session-scale sprint (10-14 PRs)

**Owner**: agent + operator collaboration
**Status**: Design, ready for kickoff
**Effort estimate**: 2-4 weeks of focused sessions, similar shape to the 16-PR intelligent transformation sprint (2026-07-05)
**Related memory**: `[[monetization-gap-analysis-2026-07-07]]`

---

## Problem statement

The affiliate/product monetization stack has 5 shipped layers (matcher, CTA
injection, reply, bio-link hub, tracking) but the LEARNING loop is broken
in 3 orthogonal ways:

1. **Selection is static** — keyword-match + highest-commission wins. No
   feedback from clicks/revenue back to product choice.
2. **Attribution is impossible** — click table stores slugs, blueprint
   stores title-case names, no JOIN key.
3. **Signal loops back to wrong arms** — when a blueprint's product gets
   clicked, the CONTENT-side arm gets the reward. The PRODUCT-side arm
   receives nothing because it doesn't exist as an arm.

Meanwhile the 2026-06-22 audit disabled 4 of 5 niches (`gaming, sports,
anime, ai_creators`) because of viewer-intent mismatch (Ronaldo trailer
→ Fitness Tracker CTA). Movies (default enabled) is the only niche
monetizing today.

The Layer 3 sprint fixes all three issues + the intent mismatch problem
via architecturally sound product bandit.

## Success criteria

* **Selection is bandit-driven** — LinUCB per-niche over the product
  arm space; context includes hook_style, content_type, platform,
  price_tier, seasonal_active.
* **Attribution works end-to-end** — click on affiliate URL X updates
  the exact product arm's posterior.
* **Real revenue tracked** — Amazon Associates Report API populates
  `affiliate_revenue.conversions > 0` for at least 10% of tracked clicks.
* **Intent mismatch solved** — per-blueprint match confidence gate;
  no product attached below threshold (better than blanket disable).
* **All 5 niches re-enabled** via the new gated selection, evidence-
  based flip in publishing.yaml.
* **Zero-cost fallback preserved** — if bandit posteriors are all cold
  (n_plays < 5), system falls back to today's static keyword matching.

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. WRITER STAGE (existing, minor changes)                           │
│    * runs FIRST as today                                            │
│    * NEW: prompt gets `{product_hint}` context if a HIGH-CONFIDENCE  │
│      product is pre-matched (based on story keywords + top arm)     │
│    * NEW: `caption_segments` can weave product name                 │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│ 2. PRODUCT SELECTOR (new — replaces static affiliate_matcher)       │
│    LinUCB over product arms per niche.                              │
│    Context (12-D):                                                  │
│      [hook_style_embedding_5D, content_type_1hot_3D,                │
│       platform_1hot_3D, price_tier_ordinal_1D]                      │
│    Confidence gate: reject match if UCB[best] - UCB[2nd best] < ε  │
│      (intent-mismatch safety — rather than the blanket disable)     │
│    Fallback: if cold-start (all arms n < 5), use static keyword     │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│ 3. CROSS-NICHE PRIOR (extends Intervention 2)                       │
│    NVIDIA RTX 4090 appears in ai_creators AND anime catalogs.       │
│    Products with n_plays >= 5 in one niche transfer moment-matched  │
│    Beta priors to fresh arms in other niches.                       │
│    Same helper as `get_transferred_prior` — extended for product    │
│    dimension.                                                       │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│ 4. CTA INJECTION (existing — unchanged)                             │
│    cta_engine.py picks price-aware CTA text                         │
│    NEW: gets `price_inr` from catalog (populate as part of sprint)  │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│ 5. PUBLISH + BIO-LINK (existing — unchanged)                        │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│ 6. CLICK TRACKING (existing schema + new attribution)               │
│    NEW: `affiliate_clicks.blueprint_id` FK-linked at click time     │
│      via UTM campaign parameter                                     │
│    NEW: JOIN via slug-normalized `product_slug` column added to     │
│      blueprints table (alembic migration)                           │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│ 7. AMAZON ASSOCIATES REPORT API                                     │
│    Nightly cron: fetch conversions from Amazon Associates           │
│    Populate `affiliate_revenue.conversions + revenue_amount`         │
│    Join on product_slug + blueprint_id + date                       │
│    Requires PA-API keys OR Report API access (via aws-lambda-power)  │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│ 8. REWARD ATTRIBUTION (new wire)                                    │
│    On click: PRODUCT arm posterior += 1 win                         │
│    On conversion: PRODUCT arm posterior += additional reward        │
│      weighted by price × commission (higher-value conversion         │
│      teaches selector harder)                                       │
│    On no-click (all publishes with product): PRODUCT arm posterior  │
│      += 1 loss after 48h no-click cutoff                            │
└─────────────────────────────────────────────────────────────────────┘
```

## PR sequence (10-14 PRs)

### Phase A — Foundation (3 PRs)

**PR 1 — Alembic migration: product_slug + affiliate_clicks.blueprint_id**
* Add `product_slug` column to `blueprints` (indexed for JOIN)
* Add `blueprint_id UUID` to `affiliate_clicks` (nullable, backfill separately)
* Add `commission_pct real` to `affiliate_clicks` (denormalized for
  attribution math)
* Add `price_inr int` to `blueprints` (denormalized from catalog)

**PR 2 — Slug normalization on write**
* In `affiliate_matcher.py`, when writing to `blueprints.affiliate_product`:
  * ALSO write `product_slug = slugify(product_name)`
* Backfill script: `UPDATE blueprints SET product_slug = lower(regexp_replace(affiliate_product, ' ', '-', 'g'))`

**PR 3 — Bandit arm registration script**
* New `scripts/register_product_arms.py`
* Reads catalog + seasonal, generates ~50-70 arms across 5 niches
* Arm ID format: `product__<slug>` (e.g., `product__nvidia-rtx-4090`)
* Dimension: `affiliate_product`
* Inserts rows into `bandit_arms` with Beta(1,1) prior + cross-niche
  transfer if available

### Phase B — Selector + Selection (3 PRs)

**PR 4 — `ProductSelector` module**
* New `genlab_core/monetization/product_selector.py`
* LinUCB implementation over product arms per niche
* 12-D context vector as designed above
* `select_product(niche_id, story_context) -> Product | None`
* Confidence gate (`ε = 0.15` default) — returns None if top 2 arms
  are within ε of each other

**PR 5 — Selector wire into pipeline**
* Modify `affiliate_matcher.py` (or add new stage `AffiliateSelect`
  that runs AFTER writer):
  * Extract `story_context` from writer's output
  * Call `product_selector.select_product(niche_id, story_context)`
  * If bandit returns None (cold or ε-gate), fall through to today's
    static keyword matching
  * If bandit picks, use its choice

**PR 6 — Cross-niche prior extension**
* Extend `learning/cross_niche_transfer.py:get_transferred_prior` to
  handle product arm keys
* Match `product__nvidia-rtx-4090` across niches (ai_creators + anime)
* Moment-matched Beta from source niche → prior for target niche fresh
  arm

### Phase C — Attribution + Reward (3 PRs)

**PR 7 — Click-to-blueprint attribution via UTM**
* Modify bio-link hub redirect (aspirehub side): include
  `utm_campaign=<blueprint_id>` in outbound Amazon URL
* Add `blueprint_id` column to `affiliate_clicks` schema
* When affiliate click hits our tracking endpoint, parse UTM campaign
  and store `blueprint_id`

**PR 8 — Reward wire: click → product arm**
* New consumer in `metric_collector.py` — after computing 48h reward:
  * For each blueprint with `product_slug`:
    * Query `affiliate_clicks WHERE blueprint_id = <bp> AND product_slug = <slug>`
    * If clicks > 0: increment product arm alpha by 1
    * If 0 clicks after 48h: increment product arm beta by 1
* Cost weighting: high-value products (price × commission) get
  scaled reward (`reward = 1 + log(1 + price * commission_pct / 100)`)

**PR 9 — CTR + revenue Mission Control card**
* New `ProductBanditCard.tsx` — per-niche table of top-5 products by
  posterior_mean
* Shows: arm_id, n_plays, alpha, beta, posterior mean, CTR (from
  clicks/impressions), estimated revenue
* Real-time visibility for operator

### Phase D — Real Revenue (2 PRs)

**PR 10 — Amazon Associates Report API integration**
* New `genlab_core/monetization/amazon_report_client.py`
* Nightly cron: fetch daily earnings report per tag
* Populate `affiliate_revenue.conversions + revenue_amount` (real, not
  proxy)
* Join by product_slug + tag + date

**PR 11 — Real-revenue reward escalation**
* Extend PR 8 — when real conversion recorded:
  * Product arm reward += revenue_amount (dollars) / 10 (scaling)
  * This teaches the selector to prefer HIGH-CONVERTING products
    over just HIGH-CLICKED products

### Phase E — Rollout (2-3 PRs)

**PR 12 — Confidence-gated re-enable of 4 disabled niches**
* Flip `affiliate_enabled: true` for gaming, sports, anime, ai_creators
* Trust the ε-gate to filter intent-mismatched product picks
* Add note in catalog explaining evidence-gated re-enable per Layer 3

**PR 13 — Price data population**
* Populate `price_inr` in catalog for all 50 products
* Add validation: refuse to publish blueprint with `price_inr = 0`

**PR 14 — Dead-network product cleanup**
* Remove products with `networks: {}` or `direct: 0.0` from catalog
* OR replace with valid affiliate program equivalents
* Products removed: Claude Pro Subscription, ChatGPT Plus,
  Midjourney Subscription, Crunchyroll Premium, FanCode Subscription

## Rollout plan

### Week 1 (PRs 1-3)
Ship schema + slug backfill. Run in observation mode — new columns
populate, no bandit selection yet. Operator can query manually.

### Week 2 (PRs 4-6)
Ship selector but keep the ε-gate CONSERVATIVE (`ε = 0.3` — most
picks fall through to static). Monitor which arms actually get plays.

### Week 3 (PRs 7-9)
Ship attribution + reward wire. Watch `ProductBanditCard`. Confirm
posteriors are moving.

### Week 4 (PRs 10-11)
Amazon Report API. First real conversion data lands.

### Week 5 (PRs 12-14)
Re-enable disabled niches. Populate prices. Clean dead networks.

## Success metrics

* **Selection**: `avg(bandit_selection_rate) ≥ 30%` — bandit picking
  over static fallback at least 30% of the time (after cold-start)
* **Attribution**: `count(affiliate_clicks WHERE blueprint_id IS NOT NULL)
  / count(affiliate_clicks) ≥ 0.7` — 70%+ of clicks attribute
* **Real revenue**: `count(affiliate_revenue WHERE conversions > 0) ≥ 5`
  per month
* **Value lift**: `sum(revenue_amount) 30d / sum(revenue_amount) prev
  30d ≥ 3` — 3× revenue at same audience within 2 months of full rollout
* **Intent match**: `<5% of user comments say "wrong product for this
  video"` — anecdotal safety check

## Cost estimates

* **Development**: 2-4 weeks of focused sessions
* **Runtime**: negligible — LinUCB matrix ops per niche cheap; PA-API
  Report is a nightly single request
* **API**: Amazon Report API is free with Associates account (already
  active). PA-API optional (unlocks dynamic matcher later, not required
  for Layer 3).

## Deferred (not in Layer 3)

* **Upsell chains** — post-click "customers also bought" — Layer 4
* **CTA mechanic bandit** — pinned/bio/story/inline — Layer 4
* **Multi-network arbitrage** — Layer 4
* **Real-time price monitoring** via PA-API — Layer 4
* **Bio-link hub bandit** (dynamic product ordering on aspirehub) — Layer 4
* **Impression tracking** (needed for real CTR denominator) — separate
  metric_collector fix, adjacent but not blocking

## Kill switches (in order of severity)

1. `GENLAB_PRODUCT_BANDIT_ENABLED=false` — global kill, falls back to
   static keyword matching
2. `ε = 1.0` per niche via config — bandit selects nothing, static fills
3. Individual `affiliate_enabled: false` per niche in catalog — pre-Layer 3
   behavior

## Companion memories after ship

Will update:
* `[[monetization-gap-analysis-2026-07-07]]` — mark Layer 3 as shipped
* `[[bandit-decision-architecture-2026-06-30]]` — add product dimension to
  the arm registry

New memory: `[[product-bandit-layer-3-shipped-YYYY-MM-DD]]` with
per-PR ledger + first-week performance data.
