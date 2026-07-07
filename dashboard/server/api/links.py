"""Link-in-bio pages and click-tracker for GenLab affiliate monetization.

Routes (ALL PUBLIC — no session auth):
    GET  /links/<channel>            -- Link-in-bio page for a channel
    GET  /links/go/<product_slug>    -- Click tracker → 302 redirect to affiliate URL

Features:
    - Smart product sorting: most-clicked products appear first
    - Social proof badges: "Trending", "Popular", "New" based on click data
    - Price display with subscription formatting
    - Coupon badges for active promotions
    - QR code ref tracking via ?ref=qr parameter
    - ?product=slug parameter for deep-linking to a specific product card
"""

import html
import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request
from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

logger = logging.getLogger(__name__)
bp = Blueprint("links", __name__)

# Simple in-memory rate limiter for public endpoints
_rate_buckets: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60  # seconds
_RATE_LIMIT_CLICK = 30  # max clicks per IP per minute
_RATE_LIMIT_SUBSCRIBE = 5  # max subscribe attempts per IP per minute


_RATE_BUCKET_MAX = 1000  # max IPs tracked before eviction


def _is_rate_limited(ip: str, limit: int) -> bool:
    """Check if IP has exceeded rate limit in the current window."""
    now = time.time()
    bucket = _rate_buckets[ip]
    # Prune old entries
    _rate_buckets[ip] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_rate_buckets[ip]) >= limit:
        return True
    _rate_buckets[ip].append(now)

    # LRU-style eviction: prune stale IPs when bucket count exceeds threshold
    if len(_rate_buckets) > _RATE_BUCKET_MAX:
        stale_ips = [k for k, v in _rate_buckets.items() if not v or (now - max(v)) >= _RATE_WINDOW]
        for k in stale_ips:
            del _rate_buckets[k]

    return False


# ── Click count cache for smart sorting + social proof ────────────────────────

_click_counts_cache: dict[str, dict[str, int]] = {}  # niche_id → {product_slug: count}
_click_counts_ts: float = 0.0
_CLICK_COUNTS_TTL = 300  # 5 minutes


def _get_click_counts(niche_id: str) -> dict[str, int]:
    """Fetch click counts per product from the database (5-minute cache).

    Returns a dict mapping product_id (slug) to total click count.
    Used for smart product sorting and social proof badges.
    """
    global _click_counts_cache, _click_counts_ts
    now = time.time()
    if _click_counts_cache and (now - _click_counts_ts) < _CLICK_COUNTS_TTL:
        return _click_counts_cache.get(niche_id, {})

    try:
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return {}

        from psycopg.rows import dict_row

        with pg_connect(dsn, row_factory=dict_row, niche_id=niche_id) as conn:
            rows = conn.execute(
                "SELECT product_id, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "GROUP BY product_id ORDER BY cnt DESC"
            ).fetchall()

        # Rebuild cache for all niches at once
        all_counts: dict[str, dict[str, int]] = {}
        for r in rows:
            product_id = r["product_id"] or ""
            cnt = r["cnt"]
            # We don't have niche in this query, so store globally
            all_counts.setdefault("_global", {})[product_id] = cnt

        # Also fetch per-niche counts
        with pg_connect(dsn, row_factory=dict_row, niche_id=niche_id) as conn:
            niche_rows = conn.execute(
                "SELECT niche_id, product_id, COUNT(*) AS cnt "
                "FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' "
                "GROUP BY niche_id, product_id ORDER BY cnt DESC"
            ).fetchall()

        for r in niche_rows:
            n = r["niche_id"] or "_global"
            pid = r["product_id"] or ""
            all_counts.setdefault(n, {})[pid] = r["cnt"]

        _click_counts_cache = all_counts
        _click_counts_ts = now
        return _click_counts_cache.get(niche_id, _click_counts_cache.get("_global", {}))

    except Exception as e:
        logger.debug("[Links] Click count fetch failed (non-fatal): %s", e)
        return _click_counts_cache.get(niche_id, {})


# ── Channel → niche mapping ───────────────────────────────────────────────────
_CHANNEL_TO_NICHE: dict[str, str] = {
    "blackboxbrief": "ai_creators",
    "criticalrush": "gaming",
    "clutchwire": "sports",
    "splicereel": "movies",
    "framedrift": "anime",
}

# ── Channel display metadata ──────────────────────────────────────────────────
_CHANNEL_META: dict[str, dict] = {
    "blackboxbrief": {
        "display_name": "BlackboxBrief",
        "handle": "@blackbox.brief",
        "accent": "#00D4FF",
        "niche_id": "ai_creators",
    },
    "criticalrush": {
        "display_name": "CriticalRush",
        "handle": "@critical_rush",
        "accent": "#f97316",
        "niche_id": "gaming",
    },
    "clutchwire": {
        "display_name": "ClutchWire",
        "handle": "@theclutchwire",
        "accent": "#FF2040",
        "niche_id": "sports",
    },
    "splicereel": {
        "display_name": "SpliceReel",
        "handle": "@splicereel",
        "accent": "#C9A84C",
        "niche_id": "movies",
    },
    "framedrift": {
        "display_name": "FrameDrift",
        "handle": "@theframedrift",
        "accent": "#7B3FE4",
        "niche_id": "anime",
    },
}

# ── Catalog path ──────────────────────────────────────────────────────────────
_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "genlab-core"
    / "config"
    / "affiliate_catalog.yaml"
)


_catalog_cache: dict = {}
_catalog_cache_ts: float = 0.0
_CATALOG_TTL = 300  # 5 minutes

# Slug → (niche_id, product_dict) reverse-lookup index, rebuilt every time
# the catalog cache is refreshed. Hot-path `/links/go/<slug>` was doing
# an O(niches × products) linear scan + slugify per click; with the index
# it's a single O(1) dict lookup. See PR #390.
#
# Cleared/rebuilt in ``_load_catalog()`` whenever the underlying catalog
# is (re)read from disk, so the index can never drift from the catalog
# it indexes.
_product_slug_index: dict[str, tuple[str, dict]] = {}


def _expand_catalog_env_vars(catalog: dict) -> dict:
    """Backwards-compat shim — delegates to the canonical loader.

    Retained because the dashboard tests pin against
    ``links._expand_catalog_env_vars``. The body now mirrors the
    canonical implementation in
    ``genlab_core.monetization.catalog_loader.expand_env_vars`` —
    keeping a thin alias here means downstream test imports keep
    working while a single source of truth lives upstream.
    """
    from genlab_core.monetization.catalog_loader import expand_env_vars

    return expand_env_vars(catalog)


def _rebuild_slug_index(catalog: dict) -> None:
    """Build the slug → (niche_id, product) reverse-lookup index.

    Called whenever the catalog cache is (re)populated. Last write wins
    when two niches expose products with the same slug — operators
    should keep slugs globally unique, and the regression test pins
    the contract by counting unique-vs-total slugs at load time.
    """
    index: dict[str, tuple[str, dict]] = {}
    duplicates: list[tuple[str, str, str]] = []  # (slug, first_niche, second_niche)
    for niche_id, niche_data in (catalog.get("niches") or {}).items():
        for product in niche_data.get("products", []):
            if not product.get("enabled", True):
                continue
            name = product.get("name")
            if not name:
                continue
            slug = _product_slug(name)
            if slug in index:
                duplicates.append((slug, index[slug][0], niche_id))
            index[slug] = (niche_id, product)
    if duplicates:
        # Log loudly — duplicate slugs mean one product's clicks redirect
        # to the wrong network/URL. Not a hard error (operator may
        # genuinely want overlap), but operators should know.
        for slug, first, second in duplicates:
            logger.warning(
                "[Links] Duplicate product slug '%s' across niches '%s' and '%s' — "
                "second wins in /links/go/ resolution",
                slug,
                first,
                second,
            )
    global _product_slug_index
    _product_slug_index = index


def _load_catalog() -> dict:
    """Load affiliate catalog YAML with 5-minute in-memory cache.

    Delegates the actual disk-read + env-var-expansion to
    ``genlab_core.monetization.catalog_loader.load_catalog`` so
    dashboard and genlab-core can never drift on URL substitution
    (see ``catalog_loader.py`` docstring for the bug history).
    Caching policy + the warn-and-serve-stale fallback stay local
    because they are dashboard-specific.

    Also (re)builds the slug → product reverse-lookup index used by
    ``_find_product_globally`` so the index can never go stale relative
    to the catalog it indexes.
    """
    global _catalog_cache, _catalog_cache_ts
    now = time.time()
    if _catalog_cache and (now - _catalog_cache_ts) < _CATALOG_TTL:
        return _catalog_cache
    try:
        from genlab_core.monetization.catalog_loader import load_catalog

        _catalog_cache = load_catalog(_CATALOG_PATH)
        _catalog_cache_ts = now
        _rebuild_slug_index(_catalog_cache)
        return _catalog_cache
    except Exception as e:
        logger.warning("[Links] Failed to load affiliate catalog: %s", e)
        return _catalog_cache or {}


def _best_network(networks: dict, country: str = "") -> tuple[str, dict]:
    """Select the network with the highest commission_pct.

    Geo-routing: if the visitor is from India, prefer ``amazon`` over ``amazon_us``.
    For all other countries, prefer ``amazon_us`` over ``amazon`` (India-only networks
    like earnkaro are skipped for non-Indian visitors).

    2026-06-22 audit fixes:

    1. **cuelinks PERMANENTLY skipped.** Per the 2026-06-14 audit
       recorded in ``geo_link_resolver.py``, cuelinks redirects earn
       ₹0/click because cuelinks doesn't inject Amazon Associates
       tags — it just relays whatever URL is in the inner ``url=``
       param, and our catalog entries lacked the tag. The pipeline-
       side resolver was updated then; THIS dashboard-side selector
       was a separate code path that kept picking cuelinks via raw
       commission_pct ranking (7.0% > amazon's 3.0%). Now matches
       the audit decision unconditionally.

    2. **Default country=""** → treated as ``IN`` instead of "neither".
       The operator runs Indian niches with IN-skewed audience.
       Cloudflare's ``CF-IPCountry`` header isn't always reaching the
       Flask handler (Caddy/CF passthrough gaps), so the default was
       silently dropping all those visitors to amazon_us (US tag on
       amazon.com) — which Indian Amazon strips when it geo-redirects
       them back to amazon.in. Net effect: ₹0 commission on those
       clicks. Defaulting to IN gives them the IN tag instead, which
       amazon.in honors. Operators with US-skewed audiences can set
       ``GENLAB_DEFAULT_AFFILIATE_GEO=US`` env to override.
    """
    import os

    # Resolve effective geo: explicit country wins, else env default, else IN
    resolved = country.upper() if country else ""
    if not resolved:
        resolved = os.environ.get("GENLAB_DEFAULT_AFFILIATE_GEO", "IN").upper()
    is_india = resolved == "IN"

    best_name = ""
    best_data: dict = {}
    best_pct = -1.0
    for name, data in networks.items():
        url = data.get("url", "")
        # Skip placeholder URLs that haven't been replaced with real affiliate links
        if not url or "example.com" in url:
            continue
        # 2026-06-22: cuelinks earns ₹0/click per audit. Skip
        # unconditionally regardless of geo. Catalog cleanup PR
        # (separate) also removed cuelinks entries from all products,
        # but this guard catches any future re-add.
        if name == "cuelinks":
            continue
        # Geo-routing: skip India-only networks for non-Indian visitors
        if not is_india and name in ("amazon", "earnkaro"):
            continue
        # Geo-routing: skip US network for Indian visitors (prefer local networks)
        if is_india and name == "amazon_us":
            continue
        pct = float(data.get("commission_pct", 0))
        if pct > best_pct:
            best_pct = pct
            best_name = name
            best_data = data
    # Fallback: if no network matched after geo-filtering, pick any
    # real link EXCEPT cuelinks (which earns ₹0).
    if not best_name:
        for name, data in networks.items():
            if name == "cuelinks":
                continue
            url = data.get("url", "")
            if url and "example.com" not in url:
                return name, data
    return best_name, best_data


def _product_slug(name: str) -> str:
    """Slugify a product name: lowercase, spaces → hyphens, strip non-alphanum."""
    import re as _re

    slug = name.lower().replace(" ", "-")
    return _re.sub(r"[^a-z0-9-]", "", slug)


def _find_product_globally(
    catalog: dict, slug: str, country: str = ""
) -> tuple[str, str, dict] | None:
    """Look up a product by slug across all niches.

    Returns (niche_id, network_name, product_dict) or None.
    ``country`` is a 2-letter ISO code used for geo-routing (e.g. ``IN``, ``US``).

    O(1) hash lookup via ``_product_slug_index`` (built in
    ``_load_catalog()``). Previously was an O(niches × products) linear
    scan + slugify per click — at ~5 niches × ~50 products per niche
    that meant ~250 string compares per ``/links/go/<slug>`` redirect.
    The index lifts that to a single dict lookup.

    ``catalog`` parameter retained for callers that want strict
    deterministic mode (test fixtures pass a hand-built catalog);
    when ``catalog`` matches the cached one, we use the cached index,
    otherwise fall back to the original linear scan against the
    caller-supplied catalog so tests stay deterministic.
    """
    # Fast path: caller passed the cached catalog (the production path
    # via ``_load_catalog()``). Use the prebuilt index.
    if catalog is _catalog_cache and slug in _product_slug_index:
        niche_id, product = _product_slug_index[slug]
        network_name, network_data = _best_network(product.get("networks", {}), country=country)
        if network_name:
            return niche_id, network_name, {**product, "_best_network": network_data}
        return None
    # Slow path: caller passed a non-cached catalog (e.g. unit-test
    # fixture with a hand-built dict). Preserve legacy linear-scan
    # behavior so test contracts stay stable.
    niches = catalog.get("niches", {})
    for niche_id, niche_data in niches.items():
        for product in niche_data.get("products", []):
            if not product.get("enabled", True):
                continue
            if _product_slug(product["name"]) == slug:
                network_name, network_data = _best_network(
                    product.get("networks", {}), country=country
                )
                if network_name:
                    return niche_id, network_name, {**product, "_best_network": network_data}
    return None


# ── HTML page renderer ────────────────────────────────────────────────────────


def _render_link_page(
    channel_slug: str,
    meta: dict,
    products: list[dict],
    tracking: dict | None = None,
    click_counts: dict[str, int] | None = None,
    highlight_product: str = "",
    referrer_source: str = "",
) -> str:
    """Render a self-contained link-in-bio HTML page.

    Args:
        click_counts: Dict mapping product slug to click count (for badges + sorting).
        highlight_product: Product slug to auto-scroll to on page load.
        referrer_source: Where the visitor came from (instagram, youtube, etc.)
            for personalized section headings.
    """
    accent = meta["accent"]
    display_name = meta["display_name"]
    handle = meta["handle"]
    click_counts = click_counts or {}

    # For light accent (SpliceReel #F5EDD6) use dark text on button
    is_light_accent = accent.lstrip("#").upper() in ("F5EDD6", "C9A84C")
    btn_text_color = "#09090b" if is_light_accent else "#ffffff"

    tracking = tracking or {}
    ga4_id = html.escape(tracking.get("ga4_measurement_id", ""))
    fb_pixel_id = html.escape(tracking.get("facebook_pixel_id", ""))
    safe_slug = html.escape(channel_slug)
    safe_display = html.escape(display_name)
    safe_handle = html.escape(handle)

    # Personalized section title based on referrer
    _referrer_titles = {
        "instagram": "From Our Latest Post",
        "youtube": "Featured In Our Video",
        "facebook": "Shared On Facebook",
        "twitter": "Mentioned On X",
        "qr": "Scanned? Here Are Our Picks",
    }
    section_title = _referrer_titles.get(referrer_source, "Today's Picks")

    tracking_head = ""
    if ga4_id:
        tracking_head += f"""
  <script async src="https://www.googletagmanager.com/gtag/js?id={ga4_id}"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', '{ga4_id}');
    gtag('event', 'view_item_list', {{item_list_name: '{safe_slug}'}});
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
    fbq('track', 'ViewContent', {{content_name: '{safe_slug}', content_type: 'product_group'}});
  </script>"""

    # Determine top-3 products by click count for "Trending" badge
    sorted_slugs_by_clicks = sorted(
        click_counts.keys(),
        key=lambda s: click_counts.get(s, 0),
        reverse=True,
    )
    top_3_slugs = set(sorted_slugs_by_clicks[:3]) if click_counts else set()

    cards_html = ""
    for product in products:
        name = html.escape(product.get("name", ""))
        price_inr = product.get("price_inr", 0)
        category = product.get("category", "")
        slug = _product_slug(name)
        go_url = f"/links/go/{slug}"

        # Price formatting: subscriptions show "From ₹X/mo", hardware shows "₹X"
        price_str = ""
        if price_inr:
            if category == "subscription":
                price_str = f"From ₹{price_inr:,}/mo"
            else:
                price_str = f"₹{price_inr:,}"
        price_tag = f'<span class="price">{price_str}</span>' if price_str else ""

        # Social proof badges
        badges_html = ""
        product_clicks = click_counts.get(slug, 0)

        if slug in top_3_slugs and product_clicks > 0:
            badges_html += '<span class="badge badge-trending">Trending</span>'
        elif product_clicks >= 5:
            badges_html += '<span class="badge badge-popular">Popular</span>'

        # "New" badge: check if product was added recently (using added_date if present)
        added_date_str = product.get("added_date", "")
        if added_date_str:
            try:
                added_dt = datetime.strptime(str(added_date_str), "%Y-%m-%d").date()
                if (date.today() - added_dt).days <= 7:
                    badges_html += '<span class="badge badge-new">New</span>'
            except (ValueError, TypeError):
                pass

        # Social proof: view count
        views_html = ""
        if product_clicks > 0:
            views_html = f'<span class="views-count">{product_clicks} people viewed</span>'

        # Check for active coupon (skip expired)
        coupons = product.get("coupons", [])
        coupon_html = ""
        if coupons:
            today_str = date.today().isoformat()
            for coupon in coupons:
                valid_until = str(coupon.get("valid_until", "9999-12-31"))
                if valid_until >= today_str:
                    code = html.escape(coupon.get("code", ""))
                    discount = html.escape(coupon.get("discount", ""))
                    if code:
                        coupon_html = (
                            f'<span class="coupon-badge">Use code: {code} ({discount})</span>'
                        )
                    elif discount:
                        coupon_html = f'<span class="coupon-badge">{discount}</span>'
                    break  # use first active coupon

        # Product image
        image_url = product.get("image_url", "")
        image_html = ""
        if image_url:
            safe_img = html.escape(image_url)
            image_html = f'<img src="{safe_img}" alt="{name}" class="product-img" loading="lazy" />'

        # Determine which network was selected (for GA4 event tracking)
        net_name_for_tracking, _ = _best_network(product.get("networks", {}))
        safe_net = html.escape(net_name_for_tracking)
        # Niche ID comes from the caller; embed it as data attribute for JS
        niche_for_tracking = html.escape(meta.get("niche_id", ""))
        # Escape single quotes for safe embedding in JS onclick string literals
        js_safe_name = name.replace("'", "\\'")

        # Highlight attribute for auto-scroll
        highlight_attr = f' id="product-{slug}"' if slug == highlight_product else ""

        cards_html += f"""
        <div class="card"{highlight_attr}>
          {image_html}
          <div class="card-info">
            <div class="badges-row">{badges_html}</div>
            <span class="product-name">{name}</span>
            {price_tag}
            {views_html}
            {coupon_html}
          </div>
          <a href="{go_url}" class="btn" onclick="trackClick('{js_safe_name}','{safe_net}','{niche_for_tracking}')">Get Deal &rarr;</a>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{safe_display} — Today's Picks</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg: #09090b;
      --surface: #0f0f12;
      --surface-2: #18181b;
      --text-primary: #fafafa;
      --text-muted: #a1a1aa;
      --accent: {accent};
      --btn-text: {btn_text_color};
      --radius: 14px;
    }}

    body {{
      background: var(--bg);
      color: var(--text-primary);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      min-height: 100vh;
      display: flex;
      justify-content: center;
      padding: 0 16px 48px;
    }}

    .container {{
      width: 100%;
      max-width: 480px;
      padding-top: 48px;
    }}

    /* ── Header ── */
    .header {{
      text-align: center;
      margin-bottom: 36px;
    }}

    .avatar {{
      width: 72px;
      height: 72px;
      border-radius: 50%;
      background: var(--surface-2);
      border: 3px solid var(--accent);
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0 auto 14px;
      font-size: 28px;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: -1px;
    }}

    .channel-name {{
      font-size: 22px;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: -0.3px;
      margin-bottom: 4px;
    }}

    .handle {{
      font-size: 14px;
      color: var(--text-muted);
    }}

    /* ── Section heading ── */
    .section-title {{
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-muted);
      margin-bottom: 16px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--surface-2);
    }}

    /* ── Product cards ── */
    .cards {{
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}

    .card {{
      background: var(--surface);
      border: 1px solid var(--surface-2);
      border-radius: var(--radius);
      padding: 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      transition: border-color 0.15s ease;
    }}

    .card:hover {{
      border-color: var(--accent);
    }}

    .product-img {{
      width: 48px;
      height: 48px;
      border-radius: 8px;
      object-fit: cover;
      flex-shrink: 0;
      background: var(--surface-2);
    }}

    .card-info {{
      display: flex;
      flex-direction: column;
      gap: 3px;
      flex: 1;
      min-width: 0;
    }}

    .product-name {{
      font-size: 15px;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .price {{
      font-size: 13px;
      color: var(--text-muted);
    }}

    .coupon-badge {{
      display: inline-block;
      font-size: 11px;
      font-weight: 600;
      color: #22c55e;
      background: rgba(34, 197, 94, 0.1);
      border: 1px solid rgba(34, 197, 94, 0.2);
      border-radius: 4px;
      padding: 2px 6px;
      margin-top: 2px;
    }}

    .badges-row {{
      display: flex;
      gap: 4px;
      flex-wrap: wrap;
    }}

    .badge {{
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: 2px 6px;
      border-radius: 4px;
    }}

    .badge-trending {{
      color: #f97316;
      background: rgba(249, 115, 22, 0.12);
      border: 1px solid rgba(249, 115, 22, 0.25);
    }}

    .badge-popular {{
      color: #8b5cf6;
      background: rgba(139, 92, 246, 0.12);
      border: 1px solid rgba(139, 92, 246, 0.25);
    }}

    .badge-new {{
      color: #3b82f6;
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.25);
    }}

    .views-count {{
      font-size: 11px;
      color: var(--text-muted);
      opacity: 0.7;
    }}

    .card.highlight {{
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent);
    }}

    .btn {{
      display: inline-block;
      background: var(--accent);
      color: var(--btn-text);
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      padding: 9px 16px;
      border-radius: 8px;
      white-space: nowrap;
      flex-shrink: 0;
      transition: opacity 0.15s ease;
    }}

    .btn:hover {{
      opacity: 0.85;
    }}

    /* ── Footer ── */
    .footer {{
      margin-top: 40px;
      text-align: center;
      font-size: 11px;
      color: var(--text-muted);
      opacity: 0.6;
    }}

    .email-form {{
      background: var(--surface);
      border: 1px solid var(--surface-2);
      border-radius: var(--radius);
      padding: 16px;
      margin-bottom: 24px;
      text-align: center;
    }}
    .email-label {{
      font-size: 13px;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 10px;
    }}
    .email-row {{
      display: flex;
      gap: 8px;
    }}
    .email-input {{
      flex: 1;
      background: var(--surface-2);
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 9px 12px;
      color: var(--text-primary);
      font-size: 14px;
      outline: none;
    }}
    .email-input:focus {{
      border-color: var(--accent);
    }}
    .email-btn {{
      flex-shrink: 0;
    }}
    .email-status {{
      font-size: 12px;
      margin-top: 8px;
      min-height: 16px;
    }}
    .email-status.success {{ color: #22c55e; }}
    .email-status.error {{ color: #ef4444; }}
  </style>{tracking_head}
</head>
<body>
  <div class="container">
    <header class="header">
      <div class="avatar">{safe_display[0]}</div>
      <div class="channel-name">{safe_display}</div>
      <div class="handle">{safe_handle}</div>
    </header>

    <form class="email-form" id="emailForm" onsubmit="return handleSubscribe(event)">
      <input type="hidden" name="channel" value="{safe_slug}" />
      <p class="email-label">Get notified about exclusive deals</p>
      <div class="email-row">
        <input type="email" name="email" placeholder="your@email.com" required class="email-input" />
        <button type="submit" class="btn email-btn">Notify Me</button>
      </div>
      <p class="email-status" id="emailStatus"></p>
    </form>

    <p class="section-title">{section_title}</p>

    <div class="cards">
      {cards_html}
    </div>

    <footer class="footer">
      Links may be affiliate links. We earn a small commission at no extra cost to you.
      <br><small>By subscribing, you agree to receive occasional deal notifications.
      <a href="/links/unsubscribe?channel={safe_slug}" style="color: {accent};">Unsubscribe</a></small>
    </footer>
  </div>
<script>
function trackClick(productName, networkName, nicheId) {{
  if (typeof gtag === 'function') {{
    gtag('event', 'affiliate_click', {{
      'product_name': productName,
      'network': networkName,
      'niche': nicheId
    }});
  }}
  if (typeof fbq === 'function') {{
    fbq('track', 'Lead', {{
      content_name: productName,
      content_category: nicheId
    }});
  }}
}}
// Auto-scroll to highlighted product if ?product=slug is set
(function() {{
  var hash = new URLSearchParams(window.location.search).get('product');
  if (hash) {{
    var el = document.getElementById('product-' + hash);
    if (el) {{
      el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
      el.classList.add('highlight');
    }}
  }}
}})();
function handleSubscribe(e) {{
  e.preventDefault();
  var form = document.getElementById('emailForm');
  var status = document.getElementById('emailStatus');
  var data = new FormData(form);
  fetch('/links/subscribe', {{method: 'POST', body: data}})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      if (d.ok) {{
        status.textContent = 'Subscribed! We will notify you about deals.';
        status.className = 'email-status success';
        form.querySelector('input[type=email]').disabled = true;
        form.querySelector('button').disabled = true;
      }} else {{
        status.textContent = d.error || 'Something went wrong.';
        status.className = 'email-status error';
      }}
    }})
    .catch(function() {{
      status.textContent = 'Network error. Please try again.';
      status.className = 'email-status error';
    }});
  return false;
}}
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────


@bp.route("/links/<channel>")
def link_page(channel: str):
    """Render the link-in-bio page for a channel."""
    channel_slug = channel.lower().replace("-", "").replace("_", "").replace(".", "")
    meta = _CHANNEL_META.get(channel_slug)
    if meta is None:
        return "Channel not found", 404

    catalog = _load_catalog()
    niche_id = meta["niche_id"]
    niche_data = catalog.get("niches", {}).get(niche_id, {})
    raw_products = niche_data.get("products", [])

    # Build display list — pick best network per product, skip disabled
    display_products = []
    for product in raw_products:
        if not product.get("enabled", True):
            continue
        networks = product.get("networks", {})
        if not networks:
            continue
        network_name, _network_data = _best_network(networks)
        if network_name:
            display_products.append(product)

    # Smart sorting: most-clicked products first
    click_counts = _get_click_counts(niche_id)
    if click_counts:
        display_products.sort(
            key=lambda p: click_counts.get(_product_slug(p.get("name", "")), 0),
            reverse=True,
        )

    settings = catalog.get("settings", {})
    tracking = settings.get("tracking", {})

    # Env var fallback: env vars take precedence over YAML values
    ga4_env = os.environ.get("GA4_MEASUREMENT_ID", "")
    fb_env = os.environ.get("FACEBOOK_PIXEL_ID", "")
    if ga4_env:
        tracking = {**tracking, "ga4_measurement_id": ga4_env}
    if fb_env:
        tracking = {**tracking, "facebook_pixel_id": fb_env}

    # Support ?product=slug to auto-scroll to a specific product
    highlight_product = request.args.get("product", "")

    # Referrer-based personalization: boost platform-relevant products to the top
    referrer = request.headers.get("Referer", "")
    ref_param = request.args.get("ref", "")
    referrer_source = ""
    if referrer:
        ref_lower = referrer.lower()
        if "instagram" in ref_lower:
            referrer_source = "instagram"
        elif "youtube" in ref_lower:
            referrer_source = "youtube"
        elif "facebook" in ref_lower or "fb.com" in ref_lower:
            referrer_source = "facebook"
        elif "twitter" in ref_lower or "t.co" in ref_lower or "x.com" in ref_lower:
            referrer_source = "twitter"
    if ref_param == "qr":
        referrer_source = "qr"

    page_html = _render_link_page(
        channel_slug,
        meta,
        display_products,
        tracking=tracking,
        click_counts=click_counts,
        highlight_product=highlight_product,
        referrer_source=referrer_source,
    )
    return page_html, 200, {"Content-Type": "text/html; charset=utf-8"}


@bp.route("/links/go/<product_slug>")
def link_go(product_slug: str):
    """Log click and redirect to affiliate URL."""
    client_ip = request.headers.get("CF-Connecting-IP", request.remote_addr or "")
    if _is_rate_limited(client_ip, _RATE_LIMIT_CLICK):
        return "Rate limited. Please try again later.", 429
    referrer = request.headers.get("Referer", "")
    platform_source = ""

    # Detect platform from referrer
    if referrer:
        ref_lower = referrer.lower()
        if "instagram" in ref_lower:
            platform_source = "instagram"
        elif "youtube" in ref_lower:
            platform_source = "youtube"
        elif "facebook" in ref_lower or "fb.com" in ref_lower:
            platform_source = "facebook"
        elif "twitter" in ref_lower or "t.co" in ref_lower or "x.com" in ref_lower:
            platform_source = "twitter"
        elif "threads" in ref_lower:
            platform_source = "threads"

    # Geo-detection: check CF-IPCountry header (Cloudflare) or X-Forwarded-For
    country = request.headers.get("CF-IPCountry", "")
    if not country:
        country = request.headers.get("X-Country", "")

    catalog = _load_catalog()
    result = _find_product_globally(catalog, product_slug, country=country)

    if result is None:
        # Product not found — redirect to the channel's link page (not dashboard login)
        logger.warning("[Links] Product slug not found: %s", product_slug)
        fallback_channel = ""
        if referrer:
            import re as _re_fb

            fb_match = _re_fb.search(r"/links/([a-z]+)", referrer.lower())
            if fb_match:
                fallback_channel = fb_match.group(1)
        if fallback_channel and fallback_channel in _CHANNEL_META:
            return redirect(f"/links/{fallback_channel}", 302)
        return redirect("/links/blackboxbrief", 302)

    niche_id, network_name, product = result
    network_data = product["_best_network"]
    raw_affiliate_url = network_data.get("url", "")

    # L3 PR 7 (2026-07-07): snapshot the network's commission_pct at
    # click time. Persisted onto affiliate_clicks.commission_pct
    # (column added in L3 PR 1 migration) so a retroactive catalog
    # rate change doesn't invalidate historical reward attribution.
    click_commission_pct: float | None = None
    try:
        raw_commission = network_data.get("commission_pct")
        if raw_commission is not None:
            click_commission_pct = float(raw_commission)
    except (TypeError, ValueError):
        click_commission_pct = None

    # Derive channel_id from referrer path (e.g. /links/criticalrush → "criticalrush")
    channel_id = ""
    if referrer:
        import re

        ch_match = re.search(r"/links/([a-z]+)", referrer.lower())
        if ch_match:
            channel_id = ch_match.group(1)

    # Blueprint ID from query parameter (appended by link-in-bio page)
    blueprint_id = request.args.get("bp", "")

    # Append UTM tracking parameters for Amazon Associates dashboard tracking
    try:
        from genlab_core.monetization.cta_engine import append_utm_params

        affiliate_url = append_utm_params(
            raw_affiliate_url,
            niche_id=niche_id,
            blueprint_id=blueprint_id,
        )
    except Exception:
        affiliate_url = raw_affiliate_url

    # Log the click (non-blocking — failure is logged and swallowed)
    try:
        from genlab_core.monetization.link_tracker import log_click

        log_click(
            product_id=product_slug,
            niche_id=niche_id,
            network=network_name,
            affiliate_url=affiliate_url,
            referrer=referrer,
            country=country,
            platform_source=platform_source,
            channel_id=channel_id,
            blueprint_id=blueprint_id,
            commission_pct=click_commission_pct,
        )
    except Exception as e:
        logger.warning("[Links] log_click import/call failed: %s", e)

    # NOTE: per-click CTA bandit updates were removed on 2026-05-17.  A Beta
    # bandit needs both successes AND failures to converge — per-click events
    # can only ever fire α (you can't observe an *absence* of a click as an
    # event).  Without β updates the bandit drifts the same way Bug F drifted
    # the engagement bandit (always-truthy signal).
    #
    # CTA bandit updates now flow exclusively through
    # ``metric_collector._update_cta_bandit_from_clicks`` at the 48h window:
    # it queries the click count for each (blueprint_id, platform_source),
    # and calls ``bandit.update(arm_id, platform, clicked=count>0)`` once per
    # post.  That gives the right binary signal for Thompson Sampling.
    #
    # log_click() above still writes to affiliate_clicks for attribution —
    # we just don't double-touch the bandit here anymore.

    # Helper: redirect to channel link page instead of dashboard login
    _fallback_url = (
        f"/links/{channel_id}"
        if channel_id and channel_id in _CHANNEL_META
        else "/links/blackboxbrief"
    )

    if not affiliate_url:
        logger.warning(
            "[Links] No affiliate URL for slug=%s network=%s", product_slug, network_name
        )
        return redirect(_fallback_url, 302)

    # Validate redirect URL domain against allowlist (prevent open redirect)
    _ALLOWED_DOMAINS = (
        "amazon.com",
        "amazon.in",
        "primevideo.com",
        "linksredirect.com",
        "ekaro.in",
        "earnkaro.com",
        "cuelinks.com",
        "impact.com",
        "goto.target.com",
        "sjv.io",
        "admitad.com",
        "ad.admitad.com",
        "jiohotstar.com",
        "hotstar.com",
        "flipkart.com",
        "myntra.com",
    )
    try:
        from urllib.parse import urlparse

        domain = urlparse(affiliate_url).hostname or ""
        if not any(domain.endswith(d) for d in _ALLOWED_DOMAINS):
            logger.warning("[Links] Blocked redirect to non-allowlisted domain: %s", domain)
            return redirect(_fallback_url, 302)
    except Exception:
        logger.warning("[Links] URL parsing failed for affiliate URL, redirecting to fallback")
        return redirect(_fallback_url, 302)

    # Mobile deep link — try to open in Amazon app for higher conversion
    user_agent = request.headers.get("User-Agent", "")
    try:
        from genlab_core.monetization.deep_linker import generate_deep_link, render_deep_link_page

        deep_link = generate_deep_link(affiliate_url, user_agent)
        if deep_link:
            product_name = product.get("name", "")
            return (
                render_deep_link_page(deep_link, product_name),
                200,
                {"Content-Type": "text/html"},
            )
    except Exception as e:
        logger.debug("[Links] Deep link generation failed, falling back to redirect: %s", e)

    # Desktop fallback (or if deep link not applicable)
    return redirect(affiliate_url, 302)


@bp.route("/links/subscribe", methods=["POST"])
def link_subscribe():
    """Handle email subscription from link-in-bio page."""
    import re

    # Basic origin check to prevent cross-site form submission
    origin = request.headers.get("Origin", "") or request.headers.get("Referer", "")
    if origin:
        from urllib.parse import urlparse as _urlparse

        origin_host = _urlparse(origin).hostname or ""
        _ALLOWED_ORIGINS = ("aspirehub.ai", "review.aspirehub.ai", "localhost", "127.0.0.1")
        if origin_host not in _ALLOWED_ORIGINS and not origin_host.endswith(".aspirehub.ai"):
            return jsonify({"ok": False, "error": "Invalid origin"}), 403
    client_ip = request.headers.get("CF-Connecting-IP", request.remote_addr or "")
    if _is_rate_limited(client_ip, _RATE_LIMIT_SUBSCRIBE):
        return jsonify({"ok": False, "error": "Too many attempts. Please try again later."}), 429

    email = (request.form.get("email") or "").strip().lower()
    channel = (request.form.get("channel") or "").strip().lower()

    # Validate email
    if not email or not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return jsonify({"ok": False, "error": "Please enter a valid email address."})

    # Get niche from channel
    niche_id = _CHANNEL_TO_NICHE.get(channel, "")

    # Store in database
    try:
        import os

        dsn = os.environ.get("DATABASE_URL", "")
        if dsn:
            from psycopg.rows import dict_row

            with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
                conn.execute(
                    "INSERT INTO email_subscribers (email, channel_slug, niche_id) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (email, channel_slug) DO UPDATE SET "
                    "is_active = true, unsubscribed_at = NULL, subscribed_at = NOW()",
                    (email, channel, niche_id),
                )
        logger.info("[Links] Email subscribed: %s for channel %s", email, channel)
    except Exception as e:
        logger.warning("[Links] Email subscribe DB error (non-fatal): %s", e)

    return jsonify({"ok": True})


@bp.route("/links/unsubscribe", methods=["GET", "POST"])
def link_unsubscribe():
    """Unsubscribe an email from deal notifications (GDPR erasure)."""
    channel = (request.args.get("channel") or request.form.get("channel") or "").strip().lower()

    if request.method == "GET":
        safe_channel = html.escape(channel)
        page = (
            '<!DOCTYPE html><html><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            "<title>Unsubscribe</title>"
            "<style>"
            "body { background: #09090b; color: #fafafa; font-family: -apple-system, sans-serif;"
            " display: flex; align-items: center; justify-content: center; min-height: 100vh;"
            " text-align: center; padding: 20px; }"
            ".container { max-width: 400px; width: 100%; }"
            "h2 { margin-bottom: 16px; font-size: 1.25rem; }"
            "input { padding: 10px 14px; border-radius: 8px; border: 1px solid #27272a;"
            " background: #18181b; color: #fafafa; width: 100%; margin: 8px 0; font-size: 0.9rem; }"
            "button { padding: 10px 20px; border-radius: 8px; border: none; background: #ef4444;"
            " color: #fff; cursor: pointer; font-size: 0.9rem; margin-top: 8px; }"
            "p { margin: 12px 0; font-size: 0.85rem; color: #a1a1aa; }"
            "</style></head><body>"
            '<div class="container">'
            "<h2>Unsubscribe from deal notifications</h2>"
            '<form method="POST" action="/links/unsubscribe">'
            f'<input type="hidden" name="channel" value="{safe_channel}" />'
            '<input type="email" name="email" placeholder="your@email.com" required />'
            "<button type=submit>Unsubscribe</button>"
            "</form>"
            "<p>Your email will be permanently removed.</p>"
            "</div></body></html>"
        )
        return page, 200, {"Content-Type": "text/html; charset=utf-8"}

    # POST: process unsubscribe
    import re

    email = (request.form.get("email") or "").strip().lower()
    if not email or not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return jsonify({"ok": False, "error": "Please enter a valid email address."})

    try:
        dsn = os.environ.get("DATABASE_URL", "")
        if dsn:
            with pg_connect(dsn, niche_id="all") as conn:
                conn.execute(
                    "UPDATE email_subscribers SET is_active = false, unsubscribed_at = NOW() "
                    "WHERE email = %s AND channel_slug = %s",
                    (email, channel),
                )
        logger.info("[Links] Email unsubscribed: %s from channel %s", email, channel)
    except Exception as e:
        logger.warning("[Links] Unsubscribe DB error: %s", e)

    return (
        (
            "<!DOCTYPE html><html><head><meta charset=UTF-8>"
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            "<title>Unsubscribed</title>"
            "<style>"
            "body { background: #09090b; color: #fafafa; font-family: -apple-system, sans-serif;"
            " display: flex; align-items: center; justify-content: center; min-height: 100vh;"
            " text-align: center; padding: 20px; }"
            "p { font-size: 1rem; color: #a1a1aa; }"
            "</style></head><body>"
            "<p>You have been unsubscribed. You will no longer receive deal notifications.</p>"
            "</body></html>"
        ),
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )
