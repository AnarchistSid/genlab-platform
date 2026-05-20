"""Affiliate product matching pipeline stage.

Scans story content for keyword matches against the affiliate catalog and
selects the best network (highest commission) for each match.

Two matching strategies:
1. **Keyword matching** (fast, free) — regex word-boundary matching against product keywords
2. **LLM matching** (Claude Haiku fallback) — contextual understanding when keywords fail

The LLM matcher is only invoked when keyword matching returns zero hits, keeping
costs minimal (~$0.00005 per LLM call at ~200 input tokens).

Usage:
    stage = AffiliateMatch()
    context = stage.execute(context)
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from genlab_core.monetization.seasonal import load_seasonal_config, get_seasonal_products

logger = logging.getLogger(__name__)

# Absolute path to the shared affiliate catalog.
# Path: affiliate_matcher.py → monetization/ → genlab_core/ → src/ → genlab-core/
_CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "affiliate_catalog.yaml"


def _count_today_affiliate_blueprints(niche_id: str) -> int:
    """Return the count of blueprints created today for ``niche_id`` that
    already carry an affiliate match.

    Used to enforce ``max_affiliate_posts_per_day`` as a true daily cap
    across pipeline runs.  Without this, two scheduled runs for the same
    niche (e.g. cron at 02:00 + manual triage at 14:00) each got their
    own ``max_per_day`` allowance, doubling the real daily total.

    Returns 0 on any DB failure — better to over-attribute affiliates
    than to block the pipeline because of a transient query error.
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        return 0
    try:
        import psycopg
    except ImportError:
        return 0
    try:
        with psycopg.connect(db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM blueprints
                    WHERE niche_id = %s
                      AND affiliate_product IS NOT NULL
                      AND created_at::date = CURRENT_DATE
                    """,
                    (niche_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning(
            "[AffiliateMatch] Daily-cap query failed (%s) — treating as 0", exc,
        )
        return 0


def _load_catalog(catalog_path: Path | None = None) -> dict[str, Any]:
    """Load the affiliate catalog YAML from disk.

    Expands ``${ENV_VAR}`` placeholders in all affiliate URLs so that
    tags like ``${AMAZON_US_AFFILIATE_TAG}`` resolve to their actual
    values from the environment.
    """
    path = catalog_path or _CATALOG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        catalog = yaml.safe_load(fh)

    # Expand ${...} env var placeholders in all network URLs
    for niche_data in (catalog.get("niches") or {}).values():
        for product in niche_data.get("products") or []:
            for net_info in (product.get("networks") or {}).values():
                url = net_info.get("url", "")
                if "${" in url:
                    net_info["url"] = os.path.expandvars(url)

    return catalog


def _keyword_hits(keywords: list, text_lower: str, *, return_matched: bool = False) -> int | tuple[int, list[str]]:
    """Count keyword matches using word-boundary regex.

    Uses ``\\b`` word boundaries instead of substring containment to prevent
    false positives like ``"mat"`` matching ``"post-match"`` or ``"led"``
    matching ``"called"``.  Casts each keyword to ``str`` to handle YAML
    auto-parsed integers (e.g. ``4090``).  Skips empty/None keywords.

    If *return_matched* is True, returns ``(hits, matched_keywords)`` instead
    of just *hits*.
    """
    hits = 0
    matched: list[str] = []
    for kw in keywords:
        kw_str = str(kw).lower().strip() if kw else ""
        if not kw_str:
            continue
        if re.search(r"\b" + re.escape(kw_str) + r"\b", text_lower):
            hits += 1
            if return_matched:
                matched.append(kw_str)
    if return_matched:
        return hits, matched
    return hits


_DEFAULT_MAX_PRICE_INR = 2500  # Impulse-buy threshold for short-form video viewers


def _price_filter(products: list, max_price_inr: int = _DEFAULT_MAX_PRICE_INR) -> list:
    """Filter products to those at or below the max price.

    High-ticket items (>₹2500) have near-zero conversion rate from
    short-form video traffic. Restrict to impulse-buy zone by default.
    Falls through to all products if no cheap matches exist.
    """
    cheap = [p for p in products if 0 < p.get("price_inr", 0) <= max_price_inr]
    return cheap if cheap else products


def match_product(
    text: str,
    niche_id: str,
    catalog: dict[str, Any],
    seasonal_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Scan text for keyword matches against niche products.

    Checks seasonal products first (if any active events), then falls back
    to static catalog. Returns the product with the most keyword hits, or None.
    Uses word-boundary matching to prevent substring false positives.
    Filters to impulse-buy price range (≤₹2500) by default.
    """
    # Strip hashtags and existing affiliate CTAs from search text to prevent
    # self-referencing circular matches and hashtag keyword pollution.
    clean_text = re.sub(r"#\w+", "", text)  # Remove hashtags
    clean_text = re.sub(r"🔗.*?link in bio", "", clean_text, flags=re.IGNORECASE)
    text_lower = clean_text.lower()

    logger.debug("[AffiliateMatch] Search text (%d chars): %s...", len(text_lower), text_lower[:100])

    best_product: dict[str, Any] | None = None
    best_hits = 0
    best_matched_keywords: list[str] = []

    # 1. Check seasonal products first (highest priority during events)
    if seasonal_config is None:
        try:
            seasonal_config = load_seasonal_config()
        except Exception:
            seasonal_config = {}

    seasonal_products = get_seasonal_products(seasonal_config)
    for product in seasonal_products:
        # Only match seasonal products for their target niche (or all if unspecified)
        product_niche = product.get("niche_id", "")
        if product_niche and product_niche != niche_id:
            continue
        keywords = product.get("keywords", [])
        hits, matched_kws = _keyword_hits(keywords, text_lower, return_matched=True)
        if hits > best_hits:
            best_hits = hits
            best_product = product
            best_matched_keywords = matched_kws

    # If seasonal match found with 2+ keyword hits, return it
    if best_product is not None and best_hits >= 2:
        logger.info(
            "[AffiliateMatch] Seasonal match: '%s' (%d hits, keywords=%s, event: %s)",
            best_product.get("name", ""),
            best_hits,
            best_matched_keywords,
            best_product.get("_seasonal_event", ""),
        )
        return best_product

    # Reset if seasonal had only 1 weak hit — let static catalog try
    if best_hits < 2:
        best_product = None
        best_hits = 0
        best_matched_keywords = []

    # 2. Fall back to static catalog (skip disabled products).
    # Apply price filter: only consider impulse-buy items unless niche overrides.
    raw_niche_products = (catalog.get("niches") or {}).get(niche_id, {}).get("products", [])
    if not raw_niche_products:
        return None
    niche_max_price = (
        (catalog.get("niches") or {}).get(niche_id, {}).get("max_price_inr")
        or _DEFAULT_MAX_PRICE_INR
    )
    enabled = [p for p in raw_niche_products if p.get("enabled", True)]
    niche_products = _price_filter(enabled, niche_max_price)
    if len(niche_products) < len(enabled):
        logger.debug(
            "[AffiliateMatch] Price filter for %s: %d/%d products under ₹%d",
            niche_id, len(niche_products), len(enabled), niche_max_price,
        )

    for product in niche_products:
        keywords = product.get("keywords") or []
        hits, matched_kws = _keyword_hits(keywords, text_lower, return_matched=True)
        if hits > best_hits:
            best_hits = hits
            best_product = product
            best_matched_keywords = matched_kws

    if best_product is not None and best_hits > 0:
        logger.debug(
            "[AffiliateMatch] Static match: '%s' (%d hits, keywords=%s)",
            best_product.get("name", ""),
            best_hits,
            best_matched_keywords,
        )
        return best_product

    # 3. Evergreen fallback: every niche may designate one product as
    # `evergreen_default: true`. When specific keyword matching misses,
    # we serve the evergreen so the post still gets a monetization slot
    # instead of zero affiliate. Examples: gaming→Game Pass, anime→Figure
    # Collection, movies→Prime Video, sports→Fitness Tracker,
    # ai_creators→Claude Pro. These are niche-defining, broadly relevant
    # products that work as CTAs regardless of the specific story.
    evergreen = next(
        (p for p in enabled if p.get("evergreen_default") is True),
        None,
    )
    if evergreen is not None:
        logger.info(
            "[AffiliateMatch] Evergreen fallback for %s: '%s' (no keyword match)",
            niche_id, evergreen.get("name", ""),
        )
        return evergreen

    # No keyword match AND no evergreen configured. Caller can attempt
    # dynamic match (only re-enabled when PA-API is configured — see
    # affiliate_matcher.execute gate).
    return None


def select_best_network(product: dict[str, Any]) -> tuple[str, str, float]:
    """Return (network_name, url, commission_pct) for the network with the highest commission.

    Skips placeholder URLs (example.com) and validates link health via the
    geo_link_resolver health cache. Falls back to lower-commission networks
    if the highest-commission link is broken.
    """
    networks: dict[str, dict[str, Any]] = product.get("networks") or {}
    if not networks:
        return ("", "", 0.0)

    from genlab_core.monetization.geo_link_resolver import _is_placeholder, _is_url_healthy

    # Sort by commission descending so we try the best link first
    ranked = sorted(
        networks.items(),
        key=lambda kv: float(kv[1].get("commission_pct", 0.0)),
        reverse=True,
    )

    for name, info in ranked:
        url = info.get("url", "")
        if _is_placeholder(url):
            continue
        if not _is_url_healthy(url):
            logger.debug(
                "[AffiliateMatch] Skipping broken %s link for %s",
                name, product.get("name", "?"),
            )
            continue
        commission = float(info.get("commission_pct", 0.0))
        return (name, url, commission)

    return ("", "", 0.0)


class AffiliateMatch:
    """Pipeline stage: match affiliate products to stories.

    Loads the affiliate catalog, scans each story's text for keyword matches,
    selects the best affiliate network, and enriches the story dict with
    affiliate fields. Non-fatal: stories without a match are left unchanged.
    """

    def __init__(
        self,
        catalog_path: Path | None = None,
        seasonal_config_path: Path | None = None,
    ) -> None:
        self._catalog_path = catalog_path
        self._seasonal_config_path = seasonal_config_path

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories: list[dict[str, Any]] = context.get("stories", [])
        if not stories:
            logger.info("[AffiliateMatch] No stories to process")
            context.setdefault("run_stats", {})["affiliate"] = {
                "matched": 0, "skipped": 0, "cap_enforced": 0,
            }
            return context

        niche_id: str = context.get("niche_id", "")

        # Load catalog
        try:
            catalog = _load_catalog(self._catalog_path)
        except Exception as exc:
            logger.warning("[AffiliateMatch] Could not load catalog: %s — skipping", exc)
            context.setdefault("run_stats", {})["affiliate"] = {
                "matched": 0, "skipped": len(stories), "cap_enforced": 0,
                "error": str(exc),
            }
            return context

        # Load seasonal config once for the whole run
        try:
            seasonal_config = load_seasonal_config(self._seasonal_config_path)
        except Exception as exc:
            logger.warning("[AffiliateMatch] Could not load seasonal config: %s — using empty", exc)
            seasonal_config = {}

        catalog_settings: dict[str, Any] = catalog.get("settings") or {}
        # ``max_affiliate_posts_per_day`` is the per-niche-per-DAY cap.
        # The in-loop counter handles this run; the already-affiliated
        # blueprint count from the DB carries the rest of today.
        max_per_day: int = int(catalog_settings.get("max_affiliate_posts_per_day", 3))
        disclosure_map: dict[str, str] = catalog_settings.get("disclosure_text") or {}

        # Pre-charge the in-loop counter with today's already-affiliated
        # blueprints from the DB for this niche.  Without this, every
        # pipeline run effectively reset the cap — gaming could run at
        # 02:00 and 14:00 and produce 2*3=6 affiliated posts/day, not 3.
        existing_today = _count_today_affiliate_blueprints(niche_id)
        matched = existing_today
        skipped = 0
        cap_enforced = 0
        if existing_today:
            logger.info(
                "[AffiliateMatch] %s: %d affiliate blueprint(s) already exist today — "
                "cap remaining = %d/%d",
                niche_id, existing_today, max(0, max_per_day - existing_today), max_per_day,
            )

        for story in stories:
            # Respect daily cap (true daily, not per-run)
            if matched >= max_per_day:
                cap_enforced += 1
                logger.debug(
                    "[AffiliateMatch] Daily cap of %d reached — skipping story '%s'",
                    max_per_day,
                    story.get("title", "")[:60],
                )
                continue

            # Build search text from hook + caption + title
            content = story.get("content") or {}
            if isinstance(content, str):
                try:
                    import json
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    content = {}

            hook = content.get("hook", "")
            ig_caption = (content.get("instagram") or {}).get("caption", "")
            title = story.get("title", "")
            search_text = f"{hook} {ig_caption} {title}"

            # 1. Static catalog FIRST.  Curated products (with real
            #    networks, real prices, real keywords) beat LLM-extracted
            #    Amazon search URLs almost every time — and crucially, the
            #    static catalog has been hand-vetted for product fit.
            #
            #    Dynamic match falls back only when keyword matching against
            #    the catalog finds nothing.  Previously this was reversed and
            #    99% of blueprints got dynamic LLM matches, producing
            #    "Gemini 3.5 Flash book" / "Apples and Oranges bluray" /
            #    "AI-designed turbine book" — none of which exist.
            product = match_product(search_text, niche_id, catalog, seasonal_config)

            # 2. Dynamic LLM fallback when static catalog finds nothing —
            # gated on PA-API being configured. Without PA-API, dynamic_match
            # generates plausible-sounding but non-existent product names
            # ("Ronda Rousey vs Gina Carano jersey", "AI-designed turbine
            # book", "Gemini 3.5 Flash book") that ship as affiliate CTAs
            # pointing at Amazon search URLs that may return nothing. Setting
            # PAAPI_ACCESS_KEY + PAAPI_SECRET_KEY auto-re-enables.
            if product is None:
                from genlab_core.monetization.paapi_client import PaapiClient
                if PaapiClient().is_configured:
                    from genlab_core.monetization.dynamic_matcher import dynamic_match
                    from genlab_core.monetization.geo_link_resolver import NICHE_PRIMARY_GEO
                    geo = NICHE_PRIMARY_GEO.get(niche_id, "IN")
                    product = dynamic_match(search_text, niche_id, geo=geo)
                else:
                    logger.debug(
                        "[AffiliateMatch] PA-API not configured — skipping "
                        "dynamic fallback for '%s' (niche=%s)",
                        title[:60], niche_id,
                    )
            if product is None:
                skipped += 1
                logger.debug(
                    "[AffiliateMatch] No product match for story '%s'",
                    title[:60],
                )
                continue

            network_name, url, commission_pct = select_best_network(product)
            if not url:
                skipped += 1
                logger.debug(
                    "[AffiliateMatch] Product '%s' has no network URL — skipping",
                    product.get("name", ""),
                )
                continue

            product_name: str = product.get("name", "")

            # Geo-targeted link resolution with UTM tracking.
            # Returns BOTH url and the actual network used (geo-priority may
            # differ from commission-priority used by select_best_network).
            from genlab_core.monetization.geo_link_resolver import (
                resolve_affiliate_link_with_network,
            )
            blueprint_id = story.get("_candidate_id", story.get("story_id", ""))
            tracked_url, resolved_network = resolve_affiliate_link_with_network(
                product=product,
                niche_id=niche_id,
                platform="instagram",  # default; cta_engine overrides per-platform
                blueprint_id=blueprint_id,
            )
            if tracked_url:
                url = tracked_url
                # Sync network_name to whatever resolve_affiliate_link picked,
                # so the DB label matches the actual URL provider.
                if resolved_network:
                    network_name = resolved_network
                    # Update commission_pct to match the resolved network
                    resolved_info = (product.get("networks") or {}).get(resolved_network, {})
                    commission_pct = float(resolved_info.get("commission_pct", commission_pct))

            # Build CTA — actionable, price-aware, urgency-driven.
            # "link in bio" has near-zero CTR; show price + emoji + verb.
            price = product.get("price_inr", 0)
            if price and price < 1000:
                cta = f"🛒 Get {product_name} for ₹{price} — link in 1st comment 👇"
            elif price and price < 5000:
                cta = f"🔥 {product_name} — only ₹{price}. Link in 1st comment 👇"
            elif price:
                cta = f"⭐ {product_name} (₹{price}) — link below 👇"
            else:
                cta = f"🔗 Get {product_name} — link in 1st comment 👇"

            story["affiliate_product"] = product_name
            story["affiliate_url"] = url
            story["affiliate_network"] = network_name
            story["affiliate_commission_pct"] = commission_pct
            story["affiliate_cta"] = cta
            story["affiliate_price_inr"] = int(product.get("price_inr", 0) or 0)
            story["_affiliate_disclosure_map"] = disclosure_map

            matched += 1
            logger.info(
                "[AffiliateMatch] Matched '%s' → %s via %s (%.1f%%)",
                title[:60],
                product_name,
                network_name,
                commission_pct,
            )

        # Subtract the pre-charged DB count so run_stats reflects this run.
        run_matched = max(0, matched - existing_today)
        context.setdefault("run_stats", {})["affiliate"] = {
            "matched": run_matched,
            "skipped": skipped,
            "cap_enforced": cap_enforced,
        }
        logger.info(
            "[AffiliateMatch] %d matched this run, %d skipped, %d cap-enforced "
            "(daily total now %d/%d for %s)",
            run_matched, skipped, cap_enforced, matched, max_per_day, niche_id,
        )
        return context
